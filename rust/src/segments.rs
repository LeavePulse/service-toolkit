use pyo3::prelude::*;
use std::collections::{BTreeSet, HashMap};

// ---------------------------------------------------------------------------
// Constants (mirror Python priorities)
// ---------------------------------------------------------------------------

const PRIORITY_MANUAL_CLEAR: i32 = 100;
const PRIORITY_MANUAL_SUSPECT: i32 = 90;
const PRIORITY_MANUAL_MAINTENANCE: i32 = 80;
const PRIORITY_PLUGIN_MAINTENANCE: i32 = 70;
const PRIORITY_MOTD_MAINTENANCE: i32 = 60;
const PRIORITY_AUTO_SUSPECT: i32 = 50;

const SUSPECT_TRIGGER_ABS_DELTA: i64 = 40;
const SUSPECT_LARGE_ABS_DELTA: i64 = 80;
const SUSPECT_RESET_ONLINE_MAX: i64 = 2;
const SUSPECT_RESET_HIGH_MIN: i64 = 30;
const SUSPECT_TRIGGER_WINDOW_SECS: f64 = 30.0 * 60.0;
const SUSPECT_CLOSE_ONLINE_THRESHOLD: i64 = 5;
const SUSPECT_STAGNANT_MIN_DURATION_SECS: f64 = 6.0 * 60.0 * 60.0;
const SUSPECT_STAGNANT_MAX_GAP_SECS: f64 = 30.0 * 60.0;
const SUSPECT_NEAR_FLAT_MIN_ONLINE: i64 = 100;
const SUSPECT_NEAR_FLAT_MIN_DURATION_SECS: f64 = 12.0 * 60.0 * 60.0;
const SUSPECT_NEAR_FLAT_MAX_GAP_SECS: f64 = 30.0 * 60.0;
const SUSPECT_NEAR_FLAT_MAX_RANGE_RATIO: f64 = 0.12;
const SUSPECT_NEAR_FLAT_MAX_MEAN_DELTA_RATIO: f64 = 0.015;
const SUSPECT_NEAR_FLAT_MIN_SMALL_DELTA_RATIO: f64 = 0.85;
const SUSPECT_NEAR_FLAT_SMALL_DELTA_ABS: i64 = 3;

pub const STATUS_SUSPECT: &str = "suspect";
pub const STATUS_MAINTENANCE: &str = "maintenance";

const MODE_FORCE_NORMAL: &str = "force_normal";
const MODE_FORCE_SUSPECT: &str = "force_suspect";
const MODE_FORCE_MAINTENANCE: &str = "force_maintenance";

// ---------------------------------------------------------------------------
// Data types — use f64 timestamps (seconds since epoch) for speed.
// Python converts datetime → f64 before calling, Rust returns f64 back.
// ---------------------------------------------------------------------------

/// Lightweight sample for segment computation.
#[derive(Clone, Debug)]
#[pyclass(get_all, from_py_object)]
pub struct Sample {
    pub ts: f64,
    pub online: Option<i64>,
    pub plugin_maintenance: bool,
    pub motd_maintenance: bool,
}

#[pymethods]
impl Sample {
    #[new]
    #[pyo3(signature = (ts, online=None, plugin_maintenance=false, motd_maintenance=false))]
    fn new(ts: f64, online: Option<i64>, plugin_maintenance: bool, motd_maintenance: bool) -> Self {
        Self {
            ts,
            online,
            plugin_maintenance,
            motd_maintenance,
        }
    }
}

/// Override from the database.
#[derive(Clone, Debug)]
#[pyclass(get_all, from_py_object)]
pub struct Override {
    pub starts_at: f64,
    pub ends_at: f64,
    pub mode: String,
}

#[pymethods]
impl Override {
    #[new]
    fn new(starts_at: f64, ends_at: f64, mode: String) -> Self {
        Self {
            starts_at,
            ends_at,
            mode,
        }
    }
}

/// A classified time interval.
#[derive(Clone, Debug)]
#[pyclass(get_all, from_py_object)]
pub struct Interval {
    pub starts_at: f64,
    pub ends_at: f64,
    pub priority: i32,
    pub source: String,
    pub status: Option<String>,
    pub exclude_from_score: bool,
    pub clears: bool,
}

#[pymethods]
impl Interval {
    #[new]
    #[pyo3(signature = (starts_at, ends_at, priority, source, status=None, exclude_from_score=false, clears=false))]
    fn new(
        starts_at: f64,
        ends_at: f64,
        priority: i32,
        source: String,
        status: Option<String>,
        exclude_from_score: bool,
        clears: bool,
    ) -> Self {
        Self {
            starts_at,
            ends_at,
            priority,
            source,
            status,
            exclude_from_score,
            clears,
        }
    }
}

/// Resolved state at a point in time.
#[derive(Clone, Debug)]
#[pyclass(get_all, from_py_object)]
pub struct State {
    pub status: Option<String>,
    pub source: Option<String>,
    pub exclude_from_score: bool,
}

#[pymethods]
impl State {
    #[new]
    #[pyo3(signature = (status=None, source=None, exclude_from_score=false))]
    fn new(status: Option<String>, source: Option<String>, exclude_from_score: bool) -> Self {
        Self {
            status,
            source,
            exclude_from_score,
        }
    }
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

fn clamp(starts_at: f64, ends_at: f64, window_start: f64, window_end: f64) -> Option<(f64, f64)> {
    let s = starts_at.max(window_start);
    let e = ends_at.min(window_end);
    if e <= s { None } else { Some((s, e)) }
}

fn merge_intervals(intervals: &mut Vec<Interval>) -> Vec<Interval> {
    if intervals.is_empty() {
        return Vec::new();
    }

    intervals.sort_by(|a, b| {
        a.priority
            .cmp(&b.priority)
            .then_with(|| a.source.cmp(&b.source))
            .then_with(|| {
                a.status
                    .as_deref()
                    .unwrap_or("")
                    .cmp(b.status.as_deref().unwrap_or(""))
            })
            .then_with(|| a.clears.cmp(&b.clears))
            .then_with(|| a.starts_at.partial_cmp(&b.starts_at).unwrap())
            .then_with(|| a.ends_at.partial_cmp(&b.ends_at).unwrap())
    });

    let mut merged: Vec<Interval> = Vec::with_capacity(intervals.len());
    for iv in intervals.iter() {
        let can_merge = merged.last().is_some_and(|prev: &Interval| {
            prev.priority == iv.priority
                && prev.source == iv.source
                && prev.status == iv.status
                && prev.exclude_from_score == iv.exclude_from_score
                && prev.clears == iv.clears
                && iv.starts_at <= prev.ends_at
        });

        if can_merge {
            let prev = merged.last_mut().unwrap();
            if iv.ends_at > prev.ends_at {
                prev.ends_at = iv.ends_at;
            }
        } else {
            merged.push(iv.clone());
        }
    }
    merged
}

fn build_stateful_intervals(
    samples: &[Sample],
    window_start: f64,
    window_end: f64,
    active_fn: impl Fn(&Sample) -> bool,
    source: &str,
    priority: i32,
    status: &str,
) -> Vec<Interval> {
    if samples.is_empty() {
        return Vec::new();
    }

    let mut intervals = Vec::new();
    let mut active = false;
    let mut current_start: f64 = 0.0;

    for sample in samples {
        let enabled = active_fn(sample);
        let t = sample.ts;
        if enabled && !active {
            active = true;
            current_start = t.max(window_start);
            continue;
        }
        if active && !enabled {
            if let Some((s, e)) = clamp(current_start, t, window_start, window_end) {
                intervals.push(Interval {
                    starts_at: s,
                    ends_at: e,
                    priority,
                    source: source.to_string(),
                    status: Some(status.to_string()),
                    exclude_from_score: true,
                    clears: false,
                });
            }
            active = false;
        }
    }

    if active {
        if let Some((s, e)) = clamp(current_start, window_end, window_start, window_end) {
            intervals.push(Interval {
                starts_at: s,
                ends_at: e,
                priority,
                source: source.to_string(),
                status: Some(status.to_string()),
                exclude_from_score: true,
                clears: false,
            });
        }
    }

    merge_intervals(&mut intervals)
}

fn is_suspect_trigger(prev: &Sample, curr: &Sample) -> bool {
    let (Some(prev_online), Some(curr_online)) = (prev.online, curr.online) else {
        return false;
    };
    let gap = curr.ts - prev.ts;
    if gap <= 0.0 || gap > SUSPECT_TRIGGER_WINDOW_SECS {
        return false;
    }
    let delta = (curr_online - prev_online).abs();
    if delta < SUSPECT_TRIGGER_ABS_DELTA {
        return false;
    }
    let low = prev_online.min(curr_online);
    let high = prev_online.max(curr_online);
    let reset_swing = low <= SUSPECT_RESET_ONLINE_MAX && high >= SUSPECT_RESET_HIGH_MIN;
    reset_swing || delta >= SUSPECT_LARGE_ABS_DELTA
}

fn build_auto_suspect_intervals(
    samples: &[Sample],
    window_start: f64,
    window_end: f64,
) -> Vec<Interval> {
    if samples.len() < 2 {
        return Vec::new();
    }

    let mut intervals = Vec::new();
    let mut recent_triggers: Vec<f64> = Vec::new();
    let mut open_start: Option<f64> = None;
    let mut previous = &samples[0];

    for current in &samples[1..] {
        let current_time = current.ts;
        if current_time < window_start {
            previous = current;
            continue;
        }

        if let Some(start) = open_start {
            if let Some(online) = current.online {
                if online <= SUSPECT_CLOSE_ONLINE_THRESHOLD {
                    if let Some((s, e)) = clamp(start, current_time, window_start, window_end) {
                        intervals.push(Interval {
                            starts_at: s,
                            ends_at: e,
                            priority: PRIORITY_AUTO_SUSPECT,
                            source: "auto".to_string(),
                            status: Some(STATUS_SUSPECT.to_string()),
                            exclude_from_score: true,
                            clears: false,
                        });
                    }
                    open_start = None;
                    recent_triggers.clear();
                    previous = current;
                    continue;
                }
            }
        }

        if is_suspect_trigger(previous, current) {
            recent_triggers.push(previous.ts);
            recent_triggers.retain(|&t| current_time - t <= SUSPECT_TRIGGER_WINDOW_SECS);

            if open_start.is_none() && recent_triggers.len() >= 2 {
                open_start = Some(recent_triggers[0].max(window_start));
            }
        }

        previous = current;
    }

    if let Some(start) = open_start {
        if let Some((s, e)) = clamp(start, window_end, window_start, window_end) {
            intervals.push(Interval {
                starts_at: s,
                ends_at: e,
                priority: PRIORITY_AUTO_SUSPECT,
                source: "auto".to_string(),
                status: Some(STATUS_SUSPECT.to_string()),
                exclude_from_score: true,
                clears: false,
            });
        }
    }

    merge_intervals(&mut intervals)
}

fn push_auto_suspect_interval(
    intervals: &mut Vec<Interval>,
    starts_at: f64,
    ends_at: f64,
    window_start: f64,
    window_end: f64,
) {
    if let Some((s, e)) = clamp(starts_at, ends_at, window_start, window_end) {
        intervals.push(Interval {
            starts_at: s,
            ends_at: e,
            priority: PRIORITY_AUTO_SUSPECT,
            source: "auto".to_string(),
            status: Some(STATUS_SUSPECT.to_string()),
            exclude_from_score: true,
            clears: false,
        });
    }
}

fn push_stagnant_suspect_run(
    intervals: &mut Vec<Interval>,
    run_value: Option<i64>,
    run_start: f64,
    run_end: f64,
    window_start: f64,
    window_end: f64,
    stagnant_min_online: i64,
) {
    let Some(online) = run_value else {
        return;
    };
    if online < stagnant_min_online {
        return;
    }
    if run_end - run_start < SUSPECT_STAGNANT_MIN_DURATION_SECS {
        return;
    }
    push_auto_suspect_interval(intervals, run_start, run_end, window_start, window_end);
}

fn build_stagnant_suspect_intervals(
    samples: &[Sample],
    window_start: f64,
    window_end: f64,
    stagnant_min_online: i64,
) -> Vec<Interval> {
    if samples.len() < 2 || stagnant_min_online <= 0 {
        return Vec::new();
    }

    let mut intervals = Vec::new();
    let mut run_value: Option<i64> = None;
    let mut run_start = 0.0;
    let mut previous_ts = samples[0].ts;

    if let Some(online) = samples[0].online {
        if online >= stagnant_min_online {
            run_value = Some(online);
            run_start = samples[0].ts;
        }
    }

    for current in &samples[1..] {
        let current_time = current.ts;
        let current_online = current.online;
        let same_value = matches!(
            (run_value, current_online),
            (Some(run_online), Some(online))
                if run_online == online
                    && current_time - previous_ts <= SUSPECT_STAGNANT_MAX_GAP_SECS
        );

        if same_value {
            previous_ts = current_time;
            continue;
        }

        let run_end =
            if run_value.is_some() && current_time - previous_ts > SUSPECT_STAGNANT_MAX_GAP_SECS {
                previous_ts
            } else {
                current_time
            };
        push_stagnant_suspect_run(
            &mut intervals,
            run_value,
            run_start,
            run_end,
            window_start,
            window_end,
            stagnant_min_online,
        );

        match current_online {
            Some(online) if online >= stagnant_min_online => {
                run_value = Some(online);
                run_start = current_time;
            }
            _ => {
                run_value = None;
                run_start = 0.0;
            }
        }
        previous_ts = current_time;
    }

    push_stagnant_suspect_run(
        &mut intervals,
        run_value,
        run_start,
        window_end,
        window_start,
        window_end,
        stagnant_min_online,
    );

    merge_intervals(&mut intervals)
}

#[derive(Clone, Debug)]
struct NearFlatRun {
    start: f64,
    previous_ts: f64,
    previous_online: i64,
    min_online: i64,
    max_online: i64,
    sum_online: f64,
    sample_count: i64,
    sum_delta: f64,
    small_delta_count: i64,
    transition_count: i64,
}

impl NearFlatRun {
    fn new(ts: f64, online: i64) -> Self {
        Self {
            start: ts,
            previous_ts: ts,
            previous_online: online,
            min_online: online,
            max_online: online,
            sum_online: online as f64,
            sample_count: 1,
            sum_delta: 0.0,
            small_delta_count: 0,
            transition_count: 0,
        }
    }

    fn push(&mut self, ts: f64, online: i64) {
        let delta = (online - self.previous_online).abs();
        self.min_online = self.min_online.min(online);
        self.max_online = self.max_online.max(online);
        self.sum_online += online as f64;
        self.sample_count += 1;
        self.sum_delta += delta as f64;
        if delta <= SUSPECT_NEAR_FLAT_SMALL_DELTA_ABS {
            self.small_delta_count += 1;
        }
        self.transition_count += 1;
        self.previous_ts = ts;
        self.previous_online = online;
    }

    fn qualifies(&self, run_end: f64) -> bool {
        if run_end - self.start < SUSPECT_NEAR_FLAT_MIN_DURATION_SECS {
            return false;
        }
        if self.sample_count < 2 || self.transition_count <= 0 {
            return false;
        }

        let mean_online = self.sum_online / self.sample_count as f64;
        if mean_online <= 0.0 {
            return false;
        }

        let range_ratio = (self.max_online - self.min_online) as f64 / mean_online;
        if range_ratio > SUSPECT_NEAR_FLAT_MAX_RANGE_RATIO {
            return false;
        }

        let mean_delta_ratio = (self.sum_delta / self.transition_count as f64) / mean_online;
        if mean_delta_ratio > SUSPECT_NEAR_FLAT_MAX_MEAN_DELTA_RATIO {
            return false;
        }

        let small_delta_ratio = self.small_delta_count as f64 / self.transition_count as f64;
        small_delta_ratio >= SUSPECT_NEAR_FLAT_MIN_SMALL_DELTA_RATIO
    }
}

fn push_near_flat_suspect_run(
    intervals: &mut Vec<Interval>,
    run: Option<NearFlatRun>,
    run_end: f64,
    window_start: f64,
    window_end: f64,
) {
    let Some(active_run) = run else {
        return;
    };
    if !active_run.qualifies(run_end) {
        return;
    }
    push_auto_suspect_interval(
        intervals,
        active_run.start,
        run_end,
        window_start,
        window_end,
    );
}

fn build_near_flat_suspect_intervals(
    samples: &[Sample],
    window_start: f64,
    window_end: f64,
    stagnant_min_online: i64,
) -> Vec<Interval> {
    if samples.len() < 2 || stagnant_min_online <= 0 {
        return Vec::new();
    }

    let min_online = stagnant_min_online.max(SUSPECT_NEAR_FLAT_MIN_ONLINE);
    let mut intervals = Vec::new();
    let mut run: Option<NearFlatRun> = None;

    for sample in samples {
        let Some(online) = sample.online else {
            push_near_flat_suspect_run(
                &mut intervals,
                run.take(),
                sample.ts,
                window_start,
                window_end,
            );
            continue;
        };
        if online < min_online {
            push_near_flat_suspect_run(
                &mut intervals,
                run.take(),
                sample.ts,
                window_start,
                window_end,
            );
            continue;
        }

        match run.as_mut() {
            None => {
                run = Some(NearFlatRun::new(sample.ts, online));
            }
            Some(active_run) => {
                if sample.ts <= active_run.previous_ts {
                    continue;
                }
                if sample.ts - active_run.previous_ts > SUSPECT_NEAR_FLAT_MAX_GAP_SECS {
                    let run_end = active_run.previous_ts;
                    push_near_flat_suspect_run(
                        &mut intervals,
                        run.take(),
                        run_end,
                        window_start,
                        window_end,
                    );
                    run = Some(NearFlatRun::new(sample.ts, online));
                } else {
                    active_run.push(sample.ts, online);
                }
            }
        }
    }

    if let Some(active_run) = run {
        let run_end = if window_end - active_run.previous_ts > SUSPECT_NEAR_FLAT_MAX_GAP_SECS {
            active_run.previous_ts
        } else {
            window_end
        };
        push_near_flat_suspect_run(
            &mut intervals,
            Some(active_run),
            run_end,
            window_start,
            window_end,
        );
    }

    merge_intervals(&mut intervals)
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// Build all segment intervals from samples and overrides.
#[pyfunction]
#[pyo3(signature = (samples, overrides, window_start, window_end, stagnant_min_online=10))]
pub fn build_segment_intervals(
    samples: Vec<Sample>,
    overrides: Vec<Override>,
    window_start: f64,
    window_end: f64,
    stagnant_min_online: i64,
) -> Vec<Interval> {
    let mut intervals: Vec<Interval> = Vec::new();

    // Manual overrides
    for ov in &overrides {
        if let Some((s, e)) = clamp(ov.starts_at, ov.ends_at, window_start, window_end) {
            let iv = match ov.mode.as_str() {
                MODE_FORCE_NORMAL => Interval {
                    starts_at: s,
                    ends_at: e,
                    priority: PRIORITY_MANUAL_CLEAR,
                    source: "manual".to_string(),
                    status: None,
                    exclude_from_score: false,
                    clears: true,
                },
                MODE_FORCE_SUSPECT => Interval {
                    starts_at: s,
                    ends_at: e,
                    priority: PRIORITY_MANUAL_SUSPECT,
                    source: "manual".to_string(),
                    status: Some(STATUS_SUSPECT.to_string()),
                    exclude_from_score: true,
                    clears: false,
                },
                MODE_FORCE_MAINTENANCE => Interval {
                    starts_at: s,
                    ends_at: e,
                    priority: PRIORITY_MANUAL_MAINTENANCE,
                    source: "manual".to_string(),
                    status: Some(STATUS_MAINTENANCE.to_string()),
                    exclude_from_score: true,
                    clears: false,
                },
                _ => continue,
            };
            intervals.push(iv);
        }
    }

    // Plugin maintenance
    intervals.extend(build_stateful_intervals(
        &samples,
        window_start,
        window_end,
        |s| s.plugin_maintenance,
        "plugin",
        PRIORITY_PLUGIN_MAINTENANCE,
        STATUS_MAINTENANCE,
    ));

    // MOTD maintenance
    intervals.extend(build_stateful_intervals(
        &samples,
        window_start,
        window_end,
        |s| s.motd_maintenance,
        "motd",
        PRIORITY_MOTD_MAINTENANCE,
        STATUS_MAINTENANCE,
    ));

    // Auto suspect
    intervals.extend(build_auto_suspect_intervals(
        &samples,
        window_start,
        window_end,
    ));
    intervals.extend(build_stagnant_suspect_intervals(
        &samples,
        window_start,
        window_end,
        stagnant_min_online.max(0),
    ));
    intervals.extend(build_near_flat_suspect_intervals(
        &samples,
        window_start,
        window_end,
        stagnant_min_online.max(0),
    ));

    merge_intervals(&mut intervals)
}

fn resolve_state_at_inner(ts: f64, intervals: &[Interval]) -> State {
    let active: Vec<&Interval> = intervals
        .iter()
        .filter(|iv| iv.starts_at <= ts && ts < iv.ends_at)
        .collect();

    if active.is_empty() {
        return State {
            status: None,
            source: None,
            exclude_from_score: false,
        };
    }

    if active.iter().any(|iv| iv.clears) {
        return State {
            status: None,
            source: None,
            exclude_from_score: false,
        };
    }

    let best = active.iter().max_by_key(|iv| iv.priority).unwrap();
    State {
        status: best.status.clone(),
        source: Some(best.source.clone()),
        exclude_from_score: best.exclude_from_score,
    }
}

fn resolve_state_for_range_inner(starts_at: f64, ends_at: f64, intervals: &[Interval]) -> State {
    let active: Vec<&Interval> = intervals
        .iter()
        .filter(|iv| iv.starts_at < ends_at && iv.ends_at > starts_at)
        .collect();

    if active.is_empty() {
        return State {
            status: None,
            source: None,
            exclude_from_score: false,
        };
    }

    if active.iter().any(|iv| iv.clears) {
        return State {
            status: None,
            source: None,
            exclude_from_score: false,
        };
    }

    let best = active.iter().max_by_key(|iv| iv.priority).unwrap();
    State {
        status: best.status.clone(),
        source: Some(best.source.clone()),
        exclude_from_score: best.exclude_from_score,
    }
}

/// Resolve the segment state at a single timestamp.
#[pyfunction]
pub fn resolve_state_at(ts: f64, intervals: Vec<Interval>) -> State {
    resolve_state_at_inner(ts, &intervals)
}

/// Resolve the segment state for a time range.
#[pyfunction]
pub fn resolve_state_for_range(starts_at: f64, ends_at: f64, intervals: Vec<Interval>) -> State {
    resolve_state_for_range_inner(starts_at, ends_at, &intervals)
}

/// Build a map from bucket timestamps to segment states.
#[pyfunction]
pub fn build_bucket_state_map(
    bucket_times: Vec<f64>,
    bucket_seconds: f64,
    intervals: Vec<Interval>,
) -> HashMap<u64, State> {
    if bucket_seconds <= 0.0 {
        return HashMap::new();
    }

    bucket_times
        .iter()
        .map(|&t| {
            let key = t.to_bits();
            let state = resolve_state_for_range_inner(t, t + bucket_seconds, &intervals);
            (key, state)
        })
        .collect()
}

/// Compute time-weighted average excluding flagged intervals.
#[pyfunction]
pub fn compute_effective_average(
    sample_pairs: Vec<(f64, Option<i64>)>,
    window_start: f64,
    window_end: f64,
    intervals: Vec<Interval>,
) -> f64 {
    _compute_effective_average_stats(sample_pairs, window_start, window_end, &intervals).0
}

/// Compute time-weighted average and trusted coverage excluding flagged intervals.
#[pyfunction]
pub fn compute_effective_average_stats(
    sample_pairs: Vec<(f64, Option<i64>)>,
    window_start: f64,
    window_end: f64,
    intervals: Vec<Interval>,
) -> (f64, f64) {
    _compute_effective_average_stats(sample_pairs, window_start, window_end, &intervals)
}

fn _compute_effective_average_stats(
    sample_pairs: Vec<(f64, Option<i64>)>,
    window_start: f64,
    window_end: f64,
    intervals: &[Interval],
) -> (f64, f64) {
    if window_end <= window_start {
        return (0.0, 0.0);
    }

    let mut current_online: Option<i64> = None;
    let mut future_samples: HashMap<u64, Vec<Option<i64>>> = HashMap::new();
    let mut boundaries: BTreeSet<u64> = BTreeSet::new();
    boundaries.insert(window_start.to_bits());
    boundaries.insert(window_end.to_bits());

    for &(ts, online) in &sample_pairs {
        if ts <= window_start {
            current_online = online;
            continue;
        }
        if ts > window_end {
            continue;
        }
        boundaries.insert(ts.to_bits());
        future_samples.entry(ts.to_bits()).or_default().push(online);
    }

    for iv in intervals {
        if iv.starts_at > window_start {
            boundaries.insert(iv.starts_at.to_bits());
        }
        if iv.ends_at < window_end {
            boundaries.insert(iv.ends_at.to_bits());
        }
    }

    let ordered: Vec<f64> = boundaries.iter().map(|&b| f64::from_bits(b)).collect();
    if ordered.len() < 2 {
        return (0.0, 0.0);
    }

    let mut weighted_total: f64 = 0.0;
    let mut trusted_seconds: f64 = 0.0;
    let total_seconds = (window_end - window_start).max(0.0);

    for window in ordered.windows(2) {
        let current_time = window[0];
        let next_boundary = window[1];
        let state = resolve_state_at_inner(current_time, intervals);
        let delta = (next_boundary - current_time).max(0.0);

        if delta > 0.0 && !state.exclude_from_score {
            if let Some(online) = current_online {
                weighted_total += online as f64 * delta;
                trusted_seconds += delta;
            }
        }

        if let Some(samples) = future_samples.get(&next_boundary.to_bits()) {
            for &online in samples {
                current_online = online;
            }
        }
    }

    let average = if trusted_seconds <= 0.0 {
        0.0
    } else {
        weighted_total / trusted_seconds
    };
    let coverage = if total_seconds <= 0.0 {
        0.0
    } else {
        (trusted_seconds / total_seconds).clamp(0.0, 1.0)
    };

    (average, coverage)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample(total_minutes: i64, online: i64) -> Sample {
        Sample {
            ts: (total_minutes * 60) as f64,
            online: Some(online),
            plugin_maintenance: false,
            motd_maintenance: false,
        }
    }

    #[test]
    fn near_flat_high_online_plateau_is_auto_suspect() {
        let plateau = [234, 235, 236, 237, 238, 237, 236, 235];
        let samples = (0..=(13 * 4))
            .map(|index| sample(index * 15, plateau[index as usize % plateau.len()]))
            .collect();

        let intervals = build_segment_intervals(samples, Vec::new(), 0.0, 13.5 * 60.0 * 60.0, 10);
        let state = resolve_state_at(12.5 * 60.0 * 60.0, intervals);

        assert_eq!(state.status.as_deref(), Some(STATUS_SUSPECT));
        assert_eq!(state.source.as_deref(), Some("auto"));
        assert!(state.exclude_from_score);
    }

    #[test]
    fn near_flat_wide_swing_is_not_auto_suspect() {
        let samples = (0..=(13 * 4))
            .map(|index| {
                let online = if index % 2 == 0 { 180 } else { 220 };
                sample(index * 15, online)
            })
            .collect();

        let intervals = build_segment_intervals(samples, Vec::new(), 0.0, 13.5 * 60.0 * 60.0, 10);
        let state = resolve_state_at(12.5 * 60.0 * 60.0, intervals);

        assert_eq!(state.status, None);
        assert!(!state.exclude_from_score);
    }
}
