//! Shared CI quality-gate engine.
//!
//! A small, framework-agnostic runner that executes an ordered list of
//! [`Step`]s, keeps going even when one fails, and prints a single summary
//! table with per-step status and timing. The exact same engine backs both
//! the native Rust gate (`lp-ci` binary, cargo steps) and the Python gate
//! (PyO3 adapter feeding ruff/mypy/pytest/... steps), so the summary UX and
//! exit semantics live in one place.
//!
//! The engine is deliberately ignorant of *what* the steps are — callers
//! build the step list. Secret-scan evaluation that needs to interpret the
//! tool's output (e.g. honouring a baseline) is delegated back to the caller
//! via [`SecretEvaluator`].

use std::io::IsTerminal;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::time::Instant;

/// Final state of a single step.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Status {
    Ok,
    Fail,
    Skip,
}

/// One unit of work in the gate.
#[derive(Debug, Clone)]
pub struct Step {
    /// Human-readable label shown in the summary (e.g. "Clippy").
    pub name: String,
    /// argv to execute; `command[0]` is the program.
    pub command: Vec<String>,
    /// When set, stdout is redirected to this file (used by report-producing
    /// tools such as detect-secrets). When `None`, stdout is inherited.
    pub stdout_path: Option<PathBuf>,
    /// When true, a zero exit code is not automatically "ok": the caller's
    /// [`SecretEvaluator`] decides based on the written report.
    pub is_secret_scan: bool,
}

impl Step {
    /// Convenience constructor for an ordinary inherited-stdout step.
    pub fn new(name: impl Into<String>, command: Vec<String>) -> Self {
        Self {
            name: name.into(),
            command,
            stdout_path: None,
            is_secret_scan: false,
        }
    }
}

/// Outcome of running one step.
#[derive(Debug, Clone)]
pub struct StepResult {
    pub name: String,
    pub status: Status,
    pub duration_secs: f64,
    pub detail: String,
}

/// Evaluates a finished secret-scan report file, returning
/// `(unbaselined_finding_count, human_summary)`. Supplied by the caller
/// because baseline handling is format-specific.
pub type SecretEvaluator<'a> = dyn Fn(&PathBuf) -> (usize, String) + 'a;

/// ANSI colour helper, transparently disabled when output is not a TTY.
pub struct Palette {
    enabled: bool,
}

impl Palette {
    /// Resolve colour support: `NO_COLOR` disables, `FORCE_COLOR` enables,
    /// otherwise follow whether stderr is a terminal — matching the Python
    /// runner exactly.
    pub fn auto() -> Self {
        let enabled = if std::env::var_os("NO_COLOR").is_some() {
            false
        } else if std::env::var_os("FORCE_COLOR").is_some() {
            true
        } else {
            std::io::stderr().is_terminal()
        };
        Self { enabled }
    }

    fn wrap(&self, code: &str, text: &str) -> String {
        if self.enabled {
            format!("\x1b[{code}m{text}\x1b[0m")
        } else {
            text.to_string()
        }
    }

    pub fn green(&self, text: &str) -> String {
        self.wrap("32", text)
    }
    pub fn red(&self, text: &str) -> String {
        self.wrap("31", text)
    }
    pub fn yellow(&self, text: &str) -> String {
        self.wrap("33", text)
    }
    pub fn dim(&self, text: &str) -> String {
        self.wrap("2", text)
    }
    pub fn bold(&self, text: &str) -> String {
        self.wrap("1", text)
    }
}

fn format_command(command: &[String]) -> String {
    command.join(" ")
}

/// Run a single step, mirroring the Python `_run_step` behaviour.
fn run_step(
    step: &Step,
    dry_run: bool,
    palette: &Palette,
    secret_evaluator: Option<&SecretEvaluator<'_>>,
) -> StepResult {
    eprintln!();
    eprintln!("{} {}", palette.bold("==>"), palette.bold(&step.name));
    eprintln!("{}", palette.dim(&format_command(&step.command)));

    if dry_run {
        return StepResult {
            name: step.name.clone(),
            status: Status::Skip,
            duration_secs: 0.0,
            detail: "dry-run".to_string(),
        };
    }

    let start = Instant::now();
    let mut cmd = Command::new(&step.command[0]);
    cmd.args(&step.command[1..]);

    let exit_code: Option<i32> = match &step.stdout_path {
        None => cmd.status().ok().and_then(|s| s.code()),
        Some(path) => match std::fs::File::create(path) {
            Ok(file) => cmd
                .stdout(Stdio::from(file))
                .status()
                .ok()
                .and_then(|s| s.code()),
            Err(err) => {
                let duration = start.elapsed().as_secs_f64();
                return StepResult {
                    name: step.name.clone(),
                    status: Status::Fail,
                    duration_secs: duration,
                    detail: format!("cannot write report: {err}"),
                };
            }
        },
    };
    let duration = start.elapsed().as_secs_f64();

    // A missing program / spawn failure surfaces as no exit code.
    let code = match exit_code {
        Some(code) => code,
        None => {
            return StepResult {
                name: step.name.clone(),
                status: Status::Fail,
                duration_secs: duration,
                detail: "failed to launch".to_string(),
            };
        }
    };

    if code != 0 {
        return StepResult {
            name: step.name.clone(),
            status: Status::Fail,
            duration_secs: duration,
            detail: format!("exit {code}"),
        };
    }

    if step.is_secret_scan
        && let (Some(eval), Some(path)) = (secret_evaluator, &step.stdout_path)
    {
        let (findings, summary) = eval(path);
        eprintln!("detect-secrets: {summary}");
        let status = if findings > 0 {
            Status::Fail
        } else {
            Status::Ok
        };
        return StepResult {
            name: step.name.clone(),
            status,
            duration_secs: duration,
            detail: summary,
        };
    }

    StepResult {
        name: step.name.clone(),
        status: Status::Ok,
        duration_secs: duration,
        detail: String::new(),
    }
}

fn render_summary(results: &[StepResult], palette: &Palette) {
    if results.is_empty() {
        return;
    }
    let name_width = results.iter().map(|r| r.name.len()).max().unwrap_or(0);
    eprintln!();
    eprintln!("{}", palette.bold("CI summary"));
    eprintln!("{}", palette.dim(&"-".repeat(name_width + 22)));
    for result in results {
        let mark = match result.status {
            Status::Ok => palette.green("✓ pass"),
            Status::Fail => palette.red("✗ fail"),
            Status::Skip => palette.yellow("• skip"),
        };
        let timing = if result.duration_secs > 0.0 {
            format!("{:6.2}s", result.duration_secs)
        } else {
            "      ".to_string()
        };
        let detail = if result.detail.is_empty() {
            String::new()
        } else {
            palette.dim(&format!("  {}", result.detail))
        };
        eprintln!(
            "  {}  {:<width$}  {}{}",
            mark,
            result.name,
            palette.dim(&timing),
            detail,
            width = name_width,
        );
    }
    eprintln!("{}", palette.dim(&"-".repeat(name_width + 22)));
}

/// Run all `steps` in order (always running every one), print the summary,
/// and return a process exit code: `0` when all passed/skipped, `1` when any
/// failed. `secret_evaluator` is consulted for steps flagged
/// [`Step::is_secret_scan`].
pub fn run(steps: &[Step], dry_run: bool, secret_evaluator: Option<&SecretEvaluator<'_>>) -> i32 {
    let palette = Palette::auto();
    let results: Vec<StepResult> = steps
        .iter()
        .map(|step| run_step(step, dry_run, &palette, secret_evaluator))
        .collect();

    render_summary(&results, &palette);

    let failures: Vec<&StepResult> = results
        .iter()
        .filter(|r| r.status == Status::Fail)
        .collect();
    if !failures.is_empty() {
        let names: Vec<&str> = failures.iter().map(|r| r.name.as_str()).collect();
        eprintln!(
            "{}",
            palette.red(&format!("CI gate failed: {}", names.join(", ")))
        );
        return 1;
    }
    eprintln!("{}", palette.green("CI gate passed"));
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn passing_step_is_ok() {
        let steps = vec![Step::new("True", vec!["true".to_string()])];
        assert_eq!(run(&steps, false, None), 0);
    }

    #[test]
    fn failing_step_fails_gate_but_runs_all() {
        let steps = vec![
            Step::new("False", vec!["false".to_string()]),
            Step::new("True", vec!["true".to_string()]),
        ];
        // Gate fails, yet both steps run (engine never short-circuits).
        assert_eq!(run(&steps, false, None), 1);
    }

    #[test]
    fn dry_run_skips_everything() {
        let steps = vec![Step::new("False", vec!["false".to_string()])];
        assert_eq!(run(&steps, true, None), 0);
    }

    #[test]
    fn missing_program_is_failure() {
        let steps = vec![Step::new(
            "Nope",
            vec!["definitely-not-a-real-binary-xyz".to_string()],
        )];
        assert_eq!(run(&steps, false, None), 1);
    }
}
