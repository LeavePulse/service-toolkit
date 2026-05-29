//! Native CI quality gate for LeavePulse Rust repositories.
//!
//! Mirrors the Python `lp-ci` UX (single summary table, runs every step even
//! when one fails) but with cargo steps. Build/install with:
//!
//! ```text
//! cargo build --no-default-features --features cli --bin lp-ci
//! ```
//!
//! Steps: fmt → clippy → test → build → audit → machete. `audit` and
//! `machete` are optional cargo subcommands; when not installed they are
//! reported as skipped rather than failing the gate. Each step can be turned
//! off with a `--no-<step>` flag.

use std::process::Command;

use service_toolkit_rust::ci::{self, Step};

struct Config {
    fmt: bool,
    clippy: bool,
    test: bool,
    build: bool,
    audit: bool,
    machete: bool,
    dry_run: bool,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            fmt: true,
            clippy: true,
            test: true,
            build: true,
            audit: true,
            machete: true,
            dry_run: false,
        }
    }
}

const HELP: &str = "\
lp-ci — native Rust quality gate

USAGE:
    lp-ci [OPTIONS]

OPTIONS:
    --no-fmt        Skip `cargo fmt --check`
    --no-clippy     Skip `cargo clippy -D warnings`
    --no-test       Skip `cargo test`
    --no-build      Skip `cargo build`
    --no-audit      Skip `cargo audit` (RustSec advisories)
    --no-machete    Skip `cargo machete` (unused dependencies)
    --dry-run       Print the steps without running them
    -h, --help      Show this help
";

fn parse_args(args: &[String]) -> Result<Config, String> {
    let mut config = Config::default();
    for arg in args {
        match arg.as_str() {
            "--no-fmt" => config.fmt = false,
            "--no-clippy" => config.clippy = false,
            "--no-test" => config.test = false,
            "--no-build" => config.build = false,
            "--no-audit" => config.audit = false,
            "--no-machete" => config.machete = false,
            "--dry-run" => config.dry_run = true,
            "-h" | "--help" => return Err(HELP.to_string()),
            other => return Err(format!("unknown argument: {other}\n\n{HELP}")),
        }
    }
    Ok(config)
}

/// Whether `cargo <subcommand>` is available (e.g. cargo-audit, cargo-machete
/// installed via `cargo install`). Optional steps are skipped when missing
/// instead of failing the gate.
fn cargo_subcommand_available(subcommand: &str) -> bool {
    Command::new("cargo")
        .args([subcommand, "--version"])
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

fn cargo(args: &[&str]) -> Vec<String> {
    let mut command = vec!["cargo".to_string()];
    command.extend(args.iter().map(|s| s.to_string()));
    command
}

fn build_steps(config: &Config) -> Vec<Step> {
    let mut steps = Vec::new();
    if config.fmt {
        steps.push(Step::new("Format", cargo(&["fmt", "--all", "--check"])));
    }
    if config.clippy {
        steps.push(Step::new(
            "Clippy",
            cargo(&[
                "clippy",
                "--all-targets",
                "--all-features",
                "--",
                "-D",
                "warnings",
            ]),
        ));
    }
    if config.test {
        steps.push(Step::new("Test", cargo(&["test", "--all-features"])));
    }
    if config.build {
        steps.push(Step::new("Build", cargo(&["build", "--all-targets"])));
    }
    if config.audit {
        if cargo_subcommand_available("audit") {
            steps.push(Step::new("Audit", cargo(&["audit"])));
        } else {
            eprintln!(
                "note: cargo-audit not installed; skipping Audit \
                 (install with `cargo install cargo-audit`)"
            );
        }
    }
    if config.machete {
        if cargo_subcommand_available("machete") {
            steps.push(Step::new("Machete", cargo(&["machete"])));
        } else {
            eprintln!(
                "note: cargo-machete not installed; skipping Machete \
                 (install with `cargo install cargo-machete`)"
            );
        }
    }
    steps
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let config = match parse_args(&args) {
        Ok(config) => config,
        Err(message) => {
            // `--help` and errors both print here; help exits 0, errors exit 2.
            let is_help = args.iter().any(|a| a == "-h" || a == "--help");
            print!("{message}");
            std::process::exit(if is_help { 0 } else { 2 });
        }
    };

    let steps = build_steps(&config);
    let code = ci::run(&steps, config.dry_run, None);
    std::process::exit(code);
}
