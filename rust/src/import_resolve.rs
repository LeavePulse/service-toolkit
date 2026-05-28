use pyo3::prelude::*;
use pyo3::types::{PyAnyMethods, PyDict, PyList};
use std::collections::{HashMap, HashSet};

type PyObject = Py<PyAny>;

/// Pick the best value for a field from candidates, respecting source priorities.
fn pick_field_value<'py>(
    py: Python<'py>,
    field: &str,
    candidates: &[Bound<'py, PyDict>],
    candidate_sources: &[String],
    candidate_timestamps: &[f64],
    priorities: &HashMap<String, usize>,
    field_priorities: &HashMap<String, usize>,
    forced_source: Option<&str>,
) -> PyResult<PyObject> {
    // 1. Forced source override
    if let Some(forced) = forced_source {
        let mut forced_indices: Vec<usize> = candidate_sources
            .iter()
            .enumerate()
            .filter_map(|(idx, source)| if source == forced { Some(idx) } else { None })
            .collect();
        forced_indices.sort_by(|&a, &b| {
            candidate_timestamps[b]
                .partial_cmp(&candidate_timestamps[a])
                .unwrap_or(std::cmp::Ordering::Equal)
                .then_with(|| a.cmp(&b))
        });
        for idx in forced_indices {
            if let Ok(Some(v)) = candidates[idx].get_item(field) {
                if !v.is_none() {
                    return Ok(v.unbind());
                }
            }
        }
    }

    let mut indices: Vec<usize> = (0..candidates.len()).collect();

    // 2. Field-specific priorities
    if !field_priorities.is_empty() {
        indices.sort_by(|&a, &b| {
            let a_fp = field_priorities
                .get(&candidate_sources[a])
                .copied()
                .unwrap_or(10_000);
            let b_fp = field_priorities
                .get(&candidate_sources[b])
                .copied()
                .unwrap_or(10_000);
            a_fp.cmp(&b_fp)
                .then_with(|| {
                    let a_p = priorities
                        .get(&candidate_sources[a])
                        .copied()
                        .unwrap_or(10_000);
                    let b_p = priorities
                        .get(&candidate_sources[b])
                        .copied()
                        .unwrap_or(10_000);
                    a_p.cmp(&b_p)
                })
                .then_with(|| {
                    candidate_timestamps[b]
                        .partial_cmp(&candidate_timestamps[a])
                        .unwrap_or(std::cmp::Ordering::Equal)
                })
                .then_with(|| candidate_sources[a].cmp(&candidate_sources[b]))
        });

        for &idx in &indices {
            if let Ok(Some(v)) = candidates[idx].get_item(field) {
                if !v.is_none() {
                    return Ok(v.unbind());
                }
            }
        }
    }

    // 3. Global priority ordering
    indices.sort_by(|&a, &b| {
        let a_p = priorities
            .get(&candidate_sources[a])
            .copied()
            .unwrap_or(10_000);
        let b_p = priorities
            .get(&candidate_sources[b])
            .copied()
            .unwrap_or(10_000);
        a_p.cmp(&b_p)
            .then_with(|| {
                candidate_timestamps[b]
                    .partial_cmp(&candidate_timestamps[a])
                    .unwrap_or(std::cmp::Ordering::Equal)
            })
            .then_with(|| candidate_sources[a].cmp(&candidate_sources[b]))
    });

    for &idx in &indices {
        if let Ok(Some(v)) = candidates[idx].get_item(field) {
            if !v.is_none() {
                return Ok(v.unbind());
            }
        }
    }

    Ok(py.None().into_pyobject(py)?.unbind())
}

fn entry_timestamp_order(py: Python<'_>, candidate: &Bound<'_, PyDict>) -> f64 {
    let raw = candidate
        .get_item("message_created_at")
        .ok()
        .flatten()
        .and_then(|v| v.extract::<String>().ok());

    match raw {
        Some(s) if !s.is_empty() => {
            let datetime = match py.import("datetime") {
                Ok(m) => m,
                Err(_) => return f64::NEG_INFINITY,
            };
            let dt_class = match datetime.getattr("datetime") {
                Ok(c) => c,
                Err(_) => return f64::NEG_INFINITY,
            };
            let replaced = s.replace("Z", "+00:00");
            let parsed = match dt_class.call_method1("fromisoformat", (replaced,)) {
                Ok(p) => p,
                Err(_) => return f64::NEG_INFINITY,
            };
            parsed
                .call_method0("timestamp")
                .ok()
                .and_then(|v| v.extract::<f64>().ok())
                .unwrap_or(f64::NEG_INFINITY)
        }
        _ => f64::NEG_INFINITY,
    }
}

/// Resolve candidates for a single identity group.
///
/// Returns `(resolved_dict, conflict_fields_list)`.
#[pyfunction]
#[pyo3(signature = (identity, candidates, source_priority, field_priority, manual_overrides, resolvable_fields))]
pub fn resolve_candidates<'py>(
    py: Python<'py>,
    identity: &str,
    candidates: Bound<'py, PyList>,
    source_priority: Vec<String>,
    field_priority: HashMap<String, Vec<String>>,
    manual_overrides: HashMap<String, HashMap<String, String>>,
    resolvable_fields: Vec<String>,
) -> PyResult<(PyObject, Vec<String>)> {
    let priorities: HashMap<String, usize> = source_priority
        .iter()
        .enumerate()
        .map(|(i, s)| (s.clone(), i))
        .collect();

    let candidate_dicts: Vec<Bound<'py, PyDict>> = candidates
        .iter()
        .filter_map(|item| item.downcast::<PyDict>().ok().map(|d| d.clone()))
        .collect();

    let candidate_sources: Vec<String> = candidate_dicts
        .iter()
        .map(|d| {
            d.get_item("_source")
                .ok()
                .flatten()
                .and_then(|v| v.extract::<String>().ok())
                .unwrap_or_else(|| "unknown".to_string())
        })
        .collect();
    let candidate_timestamps: Vec<f64> = candidate_dicts
        .iter()
        .map(|d| entry_timestamp_order(py, d))
        .collect();

    let overrides = manual_overrides
        .get(identity)
        .or_else(|| manual_overrides.get("*"))
        .cloned()
        .unwrap_or_default();

    let resolved = PyDict::new(py);
    resolved.set_item("answers", PyDict::new(py))?;
    let mut conflict_fields: Vec<String> = Vec::new();

    let skip_conflict: HashSet<&str> = ["_status_alias", "review_reason"].iter().copied().collect();

    for field in &resolvable_fields {
        let fp_index: HashMap<String, usize> = field_priority
            .get(field.as_str())
            .map(|fps| {
                fps.iter()
                    .enumerate()
                    .map(|(i, s)| (s.clone(), i))
                    .collect()
            })
            .unwrap_or_default();

        let value = pick_field_value(
            py,
            field,
            &candidate_dicts,
            &candidate_sources,
            &candidate_timestamps,
            &priorities,
            &fp_index,
            overrides.get(field.as_str()).map(|s| s.as_str()),
        )?;
        resolved.set_item(field.as_str(), &value)?;

        if !skip_conflict.contains(field.as_str()) {
            let mut distinct: HashSet<String> = HashSet::new();
            for d in &candidate_dicts {
                if let Ok(Some(v)) = d.get_item(field.as_str()) {
                    if !v.is_none() {
                        distinct.insert(v.repr()?.to_string());
                    }
                }
            }
            if distinct.len() > 1 {
                conflict_fields.push(field.clone());
            }
        }
    }

    // Resolve answers
    let answers_resolved = PyDict::new(py);

    struct AnswerEntry {
        source: String,
        value: PyObject,
        timestamp: f64,
    }

    let mut answers_by_key: HashMap<String, Vec<AnswerEntry>> = HashMap::new();

    for (idx, d) in candidate_dicts.iter().enumerate() {
        let source = &candidate_sources[idx];
        if let Ok(Some(answers_obj)) = d.get_item("answers") {
            if let Ok(answers_dict) = answers_obj.downcast::<PyDict>() {
                let ts = entry_timestamp_order(py, d);
                for (key, value) in answers_dict.iter() {
                    let key_str = key.extract::<String>()?;
                    answers_by_key
                        .entry(key_str)
                        .or_default()
                        .push(AnswerEntry {
                            source: source.clone(),
                            value: value.unbind(),
                            timestamp: ts,
                        });
                }
            }
        }
    }

    for (key, values) in &mut answers_by_key {
        if values.is_empty() {
            continue;
        }

        let unique: HashSet<String> = values
            .iter()
            .map(|e| {
                e.value
                    .bind(py)
                    .repr()
                    .map(|r| r.to_string())
                    .unwrap_or_default()
            })
            .collect();
        if unique.len() > 1 {
            conflict_fields.push(format!("answers.{}", key));
        }

        let answer_override = overrides
            .get(&format!("answers.{}", key))
            .or_else(|| overrides.get("answers"));

        if let Some(override_source) = answer_override {
            let mut found = false;
            for entry in values.iter() {
                if &entry.source == override_source && !entry.value.bind(py).is_none() {
                    answers_resolved.set_item(key.as_str(), entry.value.bind(py))?;
                    found = true;
                    break;
                }
            }
            if !found && !values.is_empty() {
                answers_resolved.set_item(key.as_str(), values[0].value.bind(py))?;
            }
            continue;
        }

        values.sort_by(|a, b| {
            let a_p = priorities.get(&a.source).copied().unwrap_or(10_000);
            let b_p = priorities.get(&b.source).copied().unwrap_or(10_000);
            a_p.cmp(&b_p)
                .then_with(|| {
                    b.timestamp
                        .partial_cmp(&a.timestamp)
                        .unwrap_or(std::cmp::Ordering::Equal)
                })
                .then_with(|| a.source.cmp(&b.source))
        });
        answers_resolved.set_item(key.as_str(), values[0].value.bind(py))?;
    }

    resolved.set_item("answers", answers_resolved)?;

    conflict_fields.sort();
    conflict_fields.dedup();

    Ok((resolved.unbind().into(), conflict_fields))
}
