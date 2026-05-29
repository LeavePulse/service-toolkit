//! Python service CI gate, ported from the former `ci_runner.py`.
//!
//! Reads `[tool.service_toolkit.ci]` (or `service-toolkit`) from pyproject,
//! merges CLI flags, builds the ordered step list (uv sync / ruff / arch-lint /
//! mypy / bandit / detect-secrets / vulture / pytest) and runs it through the
//! shared [`crate::ci`] engine. Exposed to Python as `run_ci(argv)`.

use std::path::{Path, PathBuf};
use std::process::Command;

use crate::ci::{self, Step};

const DEFAULT_SOURCE_PATHS: &[&str] = &["src"];
const DEFAULT_TEST_PATHS: &[&str] = &["tests"];
const DEFAULT_SECRET_PATHS: &[&str] = &["src", ".github/workflows", "pyproject.toml"];
const DEFAULT_BANDIT_SKIP: &str = "B104,B105,B106";
const DEFAULT_BANDIT_EXCLUDE: &str = "tests,migrations,alembic/versions";
const DEFAULT_SECRET_EXCLUDE: &str =
    r"(^|/)(node_modules|\.venv|venv|dist|build|\.git|migrations|alembic/versions|tests?)/";
const SECRET_REPORT: &str = ".secrets.scan.local.json";
const SECRET_BASELINE: &str = ".secrets.baseline";
const DEFAULT_CHANGED_BASE: &str = "HEAD";
const DEFAULT_VULTURE_MIN_CONFIDENCE: u32 = 80;

/// Resolved gate configuration after merging pyproject + CLI flags.
struct Config {
    source_paths: Vec<String>,
    test_paths: Vec<String>,
    secret_paths: Vec<String>,
    sync: bool,
    run_tests: bool,
    run_secrets: bool,
    run_bandit: bool,
    run_mypy: bool,
    run_arch_lint: bool,
    run_vulture: bool,
    bandit_skip: String,
    bandit_exclude: String,
    secret_exclude: String,
    vulture_min_confidence: u32,
    changed_base: Option<String>,
    dry_run: bool,
}

fn s(values: &[&str]) -> Vec<String> {
    values.iter().map(|v| v.to_string()).collect()
}

// ── pyproject config ─────────────────────────────────────────────────────

/// Raw `[tool.service_toolkit.ci]` table (either dotted spelling), if present.
fn read_pyproject_ci() -> toml::value::Table {
    let path = Path::new("pyproject.toml");
    let Ok(text) = std::fs::read_to_string(path) else {
        return Default::default();
    };
    let Ok(doc) = text.parse::<toml::Table>() else {
        return Default::default();
    };
    let Some(tool) = doc.get("tool").and_then(|t| t.as_table()) else {
        return Default::default();
    };
    for key in ["service_toolkit", "service-toolkit"] {
        if let Some(section) = tool.get(key).and_then(|v| v.as_table())
            && let Some(ci) = section.get("ci").and_then(|v| v.as_table())
        {
            return ci.clone();
        }
    }
    Default::default()
}

/// Coerce a toml value into a string list, falling back to `default`.
fn cfg_strings(raw: &toml::value::Table, key: &str, default: &[&str]) -> Vec<String> {
    match raw.get(key) {
        Some(toml::Value::String(s)) => vec![s.clone()],
        Some(toml::Value::Array(items)) => {
            let collected: Vec<String> = items
                .iter()
                .filter_map(|v| v.as_str())
                .map(|s| s.trim().to_string())
                .filter(|s| !s.is_empty())
                .collect();
            if collected.is_empty() {
                s(default)
            } else {
                collected
            }
        }
        _ => s(default),
    }
}

fn cfg_bool(raw: &toml::value::Table, key: &str, default: bool) -> bool {
    raw.get(key).and_then(|v| v.as_bool()).unwrap_or(default)
}

fn cfg_str(raw: &toml::value::Table, key: &str, default: &str) -> String {
    raw.get(key)
        .and_then(|v| v.as_str())
        .map(str::to_string)
        .unwrap_or_else(|| default.to_string())
}

fn cfg_u32(raw: &toml::value::Table, key: &str, default: u32) -> u32 {
    raw.get(key)
        .and_then(|v| v.as_integer())
        .and_then(|n| u32::try_from(n).ok())
        .unwrap_or(default)
}

// ── arg parsing + config merge ───────────────────────────────────────────

#[derive(Default)]
struct Args {
    no_sync: bool,
    no_tests: bool,
    no_secrets: bool,
    no_bandit: bool,
    no_mypy: bool,
    no_arch_lint: bool,
    no_vulture: bool,
    source_paths: Vec<String>,
    test_paths: Vec<String>,
    bandit_skip: Option<String>,
    bandit_exclude: Option<String>,
    changed_base: Option<String>,
    dry_run: bool,
}

/// Parse argv. Returns `Err(message)` for unknown args or a missing value.
fn parse_args(argv: &[String]) -> Result<Args, String> {
    let mut a = Args::default();
    let mut i = 0;
    while i < argv.len() {
        let arg = argv[i].as_str();
        // `--key=value` form support for value flags.
        let (flag, inline) = match arg.split_once('=') {
            Some((k, v)) => (k, Some(v.to_string())),
            None => (arg, None),
        };
        let mut take_value = |label: &str| -> Result<String, String> {
            if let Some(v) = inline.clone() {
                return Ok(v);
            }
            i += 1;
            argv.get(i)
                .cloned()
                .ok_or_else(|| format!("{label} requires a value"))
        };
        match flag {
            "--no-sync" => a.no_sync = true,
            "--no-tests" => a.no_tests = true,
            "--no-secrets" => a.no_secrets = true,
            "--no-bandit" => a.no_bandit = true,
            "--no-mypy" => a.no_mypy = true,
            "--no-arch-lint" => a.no_arch_lint = true,
            "--no-vulture" => a.no_vulture = true,
            "--dry-run" => a.dry_run = true,
            "--source" => a.source_paths.push(take_value("--source")?),
            "--tests" => a.test_paths.push(take_value("--tests")?),
            "--bandit-skip" => a.bandit_skip = Some(take_value("--bandit-skip")?),
            "--bandit-exclude" => a.bandit_exclude = Some(take_value("--bandit-exclude")?),
            // `--changed` takes an OPTIONAL base ref: consume the next token only
            // when it isn't another flag (mirrors argparse nargs="?").
            "--changed" => {
                if let Some(v) = inline.clone() {
                    a.changed_base = Some(v);
                } else if let Some(next) = argv.get(i + 1) {
                    if next.starts_with("--") {
                        a.changed_base = Some(DEFAULT_CHANGED_BASE.to_string());
                    } else {
                        a.changed_base = Some(next.clone());
                        i += 1;
                    }
                } else {
                    a.changed_base = Some(DEFAULT_CHANGED_BASE.to_string());
                }
            }
            other => return Err(format!("unknown argument: {other}")),
        }
        i += 1;
    }
    Ok(a)
}

fn config_from(args: Args) -> Config {
    let raw = read_pyproject_ci();
    let source_paths = if args.source_paths.is_empty() {
        cfg_strings(&raw, "source_paths", DEFAULT_SOURCE_PATHS)
    } else {
        args.source_paths
    };
    let test_paths = if args.test_paths.is_empty() {
        cfg_strings(&raw, "test_paths", DEFAULT_TEST_PATHS)
    } else {
        args.test_paths
    };
    Config {
        source_paths,
        test_paths,
        secret_paths: cfg_strings(&raw, "secret_paths", DEFAULT_SECRET_PATHS),
        sync: cfg_bool(&raw, "sync", true) && !args.no_sync,
        run_tests: cfg_bool(&raw, "run_tests", true) && !args.no_tests,
        run_secrets: cfg_bool(&raw, "run_secrets", true) && !args.no_secrets,
        run_bandit: cfg_bool(&raw, "run_bandit", true) && !args.no_bandit,
        run_mypy: cfg_bool(&raw, "run_mypy", true) && !args.no_mypy,
        run_arch_lint: cfg_bool(&raw, "run_arch_lint", true) && !args.no_arch_lint,
        // Vulture defaults OFF (high false-positive rate); opt in via pyproject.
        run_vulture: cfg_bool(&raw, "run_vulture", false) && !args.no_vulture,
        bandit_skip: args
            .bandit_skip
            .unwrap_or_else(|| cfg_str(&raw, "bandit_skip", DEFAULT_BANDIT_SKIP)),
        bandit_exclude: args
            .bandit_exclude
            .unwrap_or_else(|| cfg_str(&raw, "bandit_exclude", DEFAULT_BANDIT_EXCLUDE)),
        secret_exclude: cfg_str(&raw, "secret_exclude", DEFAULT_SECRET_EXCLUDE),
        vulture_min_confidence: cfg_u32(
            &raw,
            "vulture_min_confidence",
            DEFAULT_VULTURE_MIN_CONFIDENCE,
        ),
        changed_base: args.changed_base,
        dry_run: args.dry_run,
    }
}

// ── step building ────────────────────────────────────────────────────────

fn existing_paths(paths: &[String]) -> Vec<String> {
    paths
        .iter()
        .filter(|p| Path::new(p).exists())
        .cloned()
        .collect()
}

/// Tracked+staged `*.py` files under `roots` changed vs `base_ref`. Empty when
/// git is unavailable or errors (mirrors the Python fallback).
fn changed_python_files(base_ref: &str, roots: &[String]) -> Vec<String> {
    let output = Command::new("git")
        .args(["diff", "--name-only", "--diff-filter=ACMR", base_ref])
        .output();
    let Ok(out) = output else {
        return Vec::new();
    };
    if !out.status.success() {
        return Vec::new();
    }
    let stdout = String::from_utf8_lossy(&out.stdout);
    let root_paths: Vec<&Path> = roots.iter().map(Path::new).collect();
    let mut changed = Vec::new();
    for line in stdout.lines() {
        let name = line.trim();
        if !name.ends_with(".py") {
            continue;
        }
        let candidate = Path::new(name);
        if !candidate.exists() {
            continue;
        }
        let under_root = root_paths
            .iter()
            .any(|root| *root == candidate || candidate.ancestors().any(|anc| anc == *root));
        if under_root {
            changed.push(name.to_string());
        }
    }
    changed
}

fn uv_run(extra: &[&str]) -> Vec<String> {
    let mut cmd = s(&["uv", "run"]);
    cmd.extend(extra.iter().map(|x| x.to_string()));
    cmd
}

fn build_steps(config: &Config) -> Result<Vec<Step>, String> {
    let sources = existing_paths(&config.source_paths);
    if sources.is_empty() {
        return Err(format!(
            "No source paths found from: {}",
            config.source_paths.join(", ")
        ));
    }

    // Changed-mode narrows ruff/mypy to changed files; other steps stay full.
    let lint_targets: Vec<String> = match &config.changed_base {
        Some(base) => changed_python_files(base, &sources),
        None => sources.clone(),
    };

    let mut steps: Vec<Step> = Vec::new();

    if config.sync {
        steps.push(Step::new(
            "Install dependencies",
            s(&["uv", "sync", "--locked", "--no-sources"]),
        ));
    }

    // Ruff: skipped entirely in changed-mode when nothing changed.
    if config.changed_base.is_none() || !lint_targets.is_empty() {
        let targets = if config.changed_base.is_some() {
            &lint_targets
        } else {
            &sources
        };
        let mut cmd = uv_run(&["ruff", "check"]);
        cmd.extend(targets.iter().cloned());
        steps.push(Step::new("Ruff", cmd));
    }

    if config.run_arch_lint {
        let mut cmd = uv_run(&["lp-arch-lint"]);
        cmd.extend(sources.iter().cloned());
        steps.push(Step::new("Architecture Linter", cmd));
    }

    if config.run_mypy && (config.changed_base.is_none() || !lint_targets.is_empty()) {
        let targets = if config.changed_base.is_some() {
            &lint_targets
        } else {
            &sources
        };
        let mut cmd = uv_run(&[
            "--with",
            "mypy",
            "mypy",
            "--ignore-missing-imports",
            "--check-untyped-defs",
        ]);
        cmd.extend(targets.iter().cloned());
        steps.push(Step::new("MyPy", cmd));
    }

    if config.run_bandit {
        let mut cmd = uv_run(&["--with", "bandit", "bandit", "-r"]);
        cmd.extend(sources.iter().cloned());
        cmd.extend(s(&[
            "-q",
            "-s",
            &config.bandit_skip,
            "-x",
            &config.bandit_exclude,
        ]));
        steps.push(Step::new("Bandit", cmd));
    }

    if config.run_vulture {
        let mut cmd = uv_run(&["--with", "vulture", "vulture"]);
        cmd.extend(sources.iter().cloned());
        cmd.extend(s(&[
            "--min-confidence",
            &config.vulture_min_confidence.to_string(),
        ]));
        steps.push(Step::new("Vulture", cmd));
    }

    let secret_paths = existing_paths(&config.secret_paths);
    if config.run_secrets && !secret_paths.is_empty() {
        let mut cmd = uv_run(&["--with", "detect-secrets", "detect-secrets", "scan"]);
        cmd.extend(secret_paths.iter().cloned());
        cmd.extend(s(&["--exclude-files", &config.secret_exclude]));
        let mut step = Step::new("Detect secrets", cmd);
        step.stdout_path = Some(PathBuf::from(SECRET_REPORT));
        step.is_secret_scan = true;
        steps.push(step);
    }

    let tests = existing_paths(&config.test_paths);
    if config.run_tests && !tests.is_empty() {
        let mut cmd = uv_run(&["pytest"]);
        cmd.extend(tests.iter().cloned());
        steps.push(Step::new("Pytest", cmd));
    }

    Ok(steps)
}

// ── secret-scan evaluation (baseline-aware) ──────────────────────────────

/// Hashes explicitly accepted in `.secrets.baseline`.
fn load_baseline_hashes() -> std::collections::HashSet<String> {
    let mut hashes = std::collections::HashSet::new();
    let Ok(text) = std::fs::read_to_string(SECRET_BASELINE) else {
        return hashes;
    };
    let Ok(data) = serde_json::from_str::<serde_json::Value>(&text) else {
        return hashes;
    };
    if let Some(results) = data.get("results").and_then(|r| r.as_object()) {
        for items in results.values() {
            if let Some(arr) = items.as_array() {
                for item in arr {
                    if let Some(h) = item.get("hashed_secret").and_then(|v| v.as_str()) {
                        hashes.insert(h.to_string());
                    }
                }
            }
        }
    }
    hashes
}

/// Returns `(unbaselined_count, human_summary)` for the scan report, matching
/// the Python `_evaluate_secret_report` wording exactly.
fn evaluate_secret_report(path: &PathBuf) -> (usize, String) {
    let Ok(text) = std::fs::read_to_string(path) else {
        return (0, "no report".to_string());
    };
    let Ok(data) = serde_json::from_str::<serde_json::Value>(&text) else {
        return (0, "0 findings".to_string());
    };
    let Some(results) = data.get("results").and_then(|r| r.as_object()) else {
        return (0, "0 findings".to_string());
    };
    let baseline = load_baseline_hashes();
    let mut total = 0usize;
    let mut unbaselined = 0usize;
    let mut per_file: Vec<String> = Vec::new();
    for (file, items) in results {
        let Some(arr) = items.as_array() else {
            continue;
        };
        if arr.is_empty() {
            continue;
        }
        total += arr.len();
        let flagged = arr
            .iter()
            .filter(|item| {
                let hashed = item.get("hashed_secret").and_then(|v| v.as_str());
                !matches!(hashed, Some(h) if baseline.contains(h))
            })
            .count();
        if flagged > 0 {
            unbaselined += flagged;
            per_file.push(format!("{file}: {flagged}"));
        }
    }
    if total == 0 {
        return (0, "0 findings".to_string());
    }
    let baselined = total - unbaselined;
    let mut summary = format!("{unbaselined} new finding(s)");
    if baselined > 0 {
        summary.push_str(&format!(" ({baselined} baselined)"));
    }
    if !per_file.is_empty() {
        summary.push_str(&format!(" — {}", per_file.join("; ")));
    }
    (unbaselined, summary)
}

// ── entry point ──────────────────────────────────────────────────────────

/// Parse argv, build steps, run the gate. Returns a process exit code:
/// 0 = passed, 1 = a step failed, 2 = usage error.
pub fn run_python_gate(argv: Vec<String>) -> i32 {
    let args = match parse_args(&argv) {
        Ok(args) => args,
        Err(message) => {
            eprintln!("lp-ci: {message}");
            return 2;
        }
    };
    let config = config_from(args);
    let steps = match build_steps(&config) {
        Ok(steps) => steps,
        Err(message) => {
            eprintln!("lp-ci: {message}");
            return 2;
        }
    };
    let evaluator: &ci::SecretEvaluator<'_> = &|path: &PathBuf| evaluate_secret_report(path);
    ci::run(&steps, config.dry_run, Some(evaluator))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn no_mypy_flag_drops_mypy_step() {
        let args = parse_args(&s(&["--no-mypy", "--no-sync"])).unwrap();
        assert!(args.no_mypy && args.no_sync);
        let cfg = config_from(args);
        assert!(!cfg.run_mypy);
        assert!(!cfg.sync);
    }

    #[test]
    fn changed_consumes_optional_base() {
        let args = parse_args(&s(&["--changed", "origin/main"])).unwrap();
        assert_eq!(args.changed_base.as_deref(), Some("origin/main"));
        // bare --changed before another flag → default base.
        let args2 = parse_args(&s(&["--changed", "--no-tests"])).unwrap();
        assert_eq!(args2.changed_base.as_deref(), Some("HEAD"));
        assert!(args2.no_tests);
    }

    #[test]
    fn vulture_defaults_off_and_min_confidence_default() {
        let cfg = config_from(parse_args(&[]).unwrap());
        assert!(!cfg.run_vulture);
        assert_eq!(cfg.vulture_min_confidence, 80);
    }

    #[test]
    fn unknown_arg_is_error() {
        assert!(parse_args(&s(&["--bogus"])).is_err());
    }

    #[test]
    fn bandit_skip_inline_value() {
        let args = parse_args(&s(&["--bandit-skip=B101,B102"])).unwrap();
        assert_eq!(args.bandit_skip.as_deref(), Some("B101,B102"));
    }
}
