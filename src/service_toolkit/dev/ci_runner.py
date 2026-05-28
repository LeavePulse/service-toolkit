"""Local runner for the shared Python service CI quality gate.

Mirrors the ``code-quality`` GitHub workflow so the exact same checks can be
run locally and from a pre-commit hook. Unlike a naive shell script it:

* runs every step even when an earlier one fails, then reports a single
  summary table with per-step status and timing;
* treats ``detect-secrets`` findings as a real failure (honouring an optional
  ``.secrets.baseline`` for known false positives such as ``secrets: inherit``);
* supports a ``--changed`` mode that limits source-path steps to files changed
  against a base ref, for fast pre-commit feedback.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess  # nosec B404
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_DEFAULT_SOURCE_PATHS = ("src",)
_DEFAULT_TEST_PATHS = ("tests",)
_DEFAULT_SECRET_PATHS = ("src", ".github/workflows", "pyproject.toml")
_DEFAULT_BANDIT_SKIP = "B104,B105,B106"
_DEFAULT_BANDIT_EXCLUDE = "tests,migrations,alembic/versions"
_DEFAULT_SECRET_EXCLUDE = (
    r"(^|/)(node_modules|\.venv|venv|dist|build|\.git|migrations|"
    r"alembic/versions|tests?)/"
)
_SECRET_REPORT = ".secrets.scan.local.json"
_SECRET_BASELINE = ".secrets.baseline"
_DEFAULT_CHANGED_BASE = "HEAD"
logger = logging.getLogger(__name__)


def _supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return sys.stderr.isatty()


class _Palette:
    """ANSI colour codes, disabled transparently when output is not a TTY."""

    def __init__(self, *, enabled: bool) -> None:
        self._enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        if not self._enabled:
            return text
        return f"\033[{code}m{text}\033[0m"

    def green(self, text: str) -> str:
        return self._wrap("32", text)

    def red(self, text: str) -> str:
        return self._wrap("31", text)

    def yellow(self, text: str) -> str:
        return self._wrap("33", text)

    def dim(self, text: str) -> str:
        return self._wrap("2", text)

    def bold(self, text: str) -> str:
        return self._wrap("1", text)


@dataclass(frozen=True, slots=True)
class _Step:
    name: str
    command: tuple[str, ...]
    stdout_path: Path | None = None
    is_secret_scan: bool = False


@dataclass(slots=True)
class _StepResult:
    name: str
    status: str  # "ok" | "fail" | "skip"
    duration: float = 0.0
    detail: str = ""


@dataclass(frozen=True, slots=True)
class _CiConfig:
    source_paths: tuple[str, ...]
    test_paths: tuple[str, ...]
    secret_paths: tuple[str, ...]
    sync: bool
    run_tests: bool
    run_secrets: bool
    run_bandit: bool
    run_mypy: bool
    run_arch_lint: bool
    bandit_skip: str
    bandit_exclude: str
    secret_exclude: str
    changed_base: str | None


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the shared Python service CI quality gate locally.",
    )
    parser.add_argument("--no-sync", action="store_true", help="Skip uv sync.")
    parser.add_argument("--no-tests", action="store_true", help="Skip pytest.")
    parser.add_argument(
        "--no-secrets",
        action="store_true",
        help="Skip detect-secrets scan.",
    )
    parser.add_argument("--no-bandit", action="store_true", help="Skip bandit.")
    parser.add_argument("--no-mypy", action="store_true", help="Skip mypy.")
    parser.add_argument(
        "--no-arch-lint",
        action="store_true",
        help="Skip lp-arch-lint.",
    )
    parser.add_argument(
        "--source",
        action="append",
        dest="source_paths",
        help="Source path to check. May be passed multiple times.",
    )
    parser.add_argument(
        "--tests",
        action="append",
        dest="test_paths",
        help="Test path to run. May be passed multiple times.",
    )
    parser.add_argument(
        "--bandit-skip",
        help=f"Comma-separated bandit skip list. Default: {_DEFAULT_BANDIT_SKIP}.",
    )
    parser.add_argument(
        "--bandit-exclude",
        help=f"Comma-separated bandit exclude paths. Default: {_DEFAULT_BANDIT_EXCLUDE}.",
    )
    parser.add_argument(
        "--changed",
        nargs="?",
        const=_DEFAULT_CHANGED_BASE,
        dest="changed_base",
        metavar="BASE_REF",
        help=(
            "Limit ruff/mypy to source files changed against BASE_REF "
            f"(default {_DEFAULT_CHANGED_BASE}). Other steps still run in full."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands without executing them.",
    )
    return parser.parse_args(argv)


def _read_pyproject_config(path: Path = Path("pyproject.toml")) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    tool = data.get("tool")
    if not isinstance(tool, dict):
        return {}
    section = tool.get("service_toolkit")
    if isinstance(section, dict) and isinstance(section.get("ci"), dict):
        return section["ci"]
    section = tool.get("service-toolkit")
    if isinstance(section, dict) and isinstance(section.get("ci"), dict):
        return section["ci"]
    return {}


def _strings(value: object, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        items = tuple(str(item).strip() for item in value if str(item).strip())
        return items or default
    return default


def _bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _config_from_args(args: argparse.Namespace) -> _CiConfig:
    raw = _read_pyproject_config()
    source_paths = tuple(args.source_paths or ()) or _strings(
        raw.get("source_paths"),
        _DEFAULT_SOURCE_PATHS,
    )
    test_paths = tuple(args.test_paths or ()) or _strings(
        raw.get("test_paths"),
        _DEFAULT_TEST_PATHS,
    )
    return _CiConfig(
        source_paths=source_paths,
        test_paths=test_paths,
        secret_paths=_strings(raw.get("secret_paths"), _DEFAULT_SECRET_PATHS),
        sync=_bool(raw.get("sync"), True) and not args.no_sync,
        run_tests=_bool(raw.get("run_tests"), True) and not args.no_tests,
        run_secrets=_bool(raw.get("run_secrets"), True) and not args.no_secrets,
        run_bandit=_bool(raw.get("run_bandit"), True) and not args.no_bandit,
        run_mypy=_bool(raw.get("run_mypy"), True) and not args.no_mypy,
        run_arch_lint=_bool(raw.get("run_arch_lint"), True)
        and not args.no_arch_lint,
        bandit_skip=str(
            args.bandit_skip or raw.get("bandit_skip") or _DEFAULT_BANDIT_SKIP
        ),
        bandit_exclude=str(
            args.bandit_exclude
            or raw.get("bandit_exclude")
            or _DEFAULT_BANDIT_EXCLUDE
        ),
        secret_exclude=str(raw.get("secret_exclude") or _DEFAULT_SECRET_EXCLUDE),
        changed_base=getattr(args, "changed_base", None),
    )


def _existing_paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(path for path in paths if Path(path).exists())


def _changed_python_files(base_ref: str, roots: tuple[str, ...]) -> tuple[str, ...]:
    """Return tracked + staged ``*.py`` files under ``roots`` changed vs base_ref."""
    try:
        diff = subprocess.run(  # nosec B603 B607
            ("git", "diff", "--name-only", "--diff-filter=ACMR", base_ref),
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ()
    root_paths = tuple(Path(root) for root in roots)
    changed: list[str] = []
    for line in diff.stdout.splitlines():
        name = line.strip()
        if not name.endswith(".py"):
            continue
        candidate = Path(name)
        if not candidate.exists():
            continue
        if any(root in candidate.parents or root == candidate for root in root_paths):
            changed.append(name)
    return tuple(changed)


def _build_steps(config: _CiConfig) -> list[_Step]:
    sources = _existing_paths(config.source_paths)
    if not sources:
        msg = f"No source paths found from: {', '.join(config.source_paths)}"
        raise SystemExit(msg)

    lint_targets = sources
    if config.changed_base is not None:
        changed = _changed_python_files(config.changed_base, sources)
        # No changed files → nothing to lint incrementally; keep full sources for
        # the non-incremental steps but skip ruff/mypy targets entirely.
        lint_targets = changed or ()

    steps: list[_Step] = []
    if config.sync:
        steps.append(
            _Step("Install dependencies", ("uv", "sync", "--locked", "--no-sources"))
        )

    if config.changed_base is None or lint_targets:
        ruff_targets = lint_targets if config.changed_base is not None else sources
        steps.append(_Step("Ruff", ("uv", "run", "ruff", "check", *ruff_targets)))

    if config.run_arch_lint:
        steps.append(_Step("Architecture Linter", ("uv", "run", "lp-arch-lint", *sources)))

    if config.run_mypy and (config.changed_base is None or lint_targets):
        mypy_targets = lint_targets if config.changed_base is not None else sources
        steps.append(
            _Step(
                "MyPy",
                (
                    "uv",
                    "run",
                    "--with",
                    "mypy",
                    "mypy",
                    "--ignore-missing-imports",
                    "--check-untyped-defs",
                    *mypy_targets,
                ),
            )
        )

    if config.run_bandit:
        steps.append(
            _Step(
                "Bandit",
                (
                    "uv",
                    "run",
                    "--with",
                    "bandit",
                    "bandit",
                    "-r",
                    *sources,
                    "-q",
                    "-s",
                    config.bandit_skip,
                    "-x",
                    config.bandit_exclude,
                ),
            )
        )

    secret_paths = _existing_paths(config.secret_paths)
    if config.run_secrets and secret_paths:
        steps.append(
            _Step(
                "Detect secrets",
                (
                    "uv",
                    "run",
                    "--with",
                    "detect-secrets",
                    "detect-secrets",
                    "scan",
                    *secret_paths,
                    "--exclude-files",
                    config.secret_exclude,
                ),
                stdout_path=Path(_SECRET_REPORT),
                is_secret_scan=True,
            )
        )

    tests = _existing_paths(config.test_paths)
    if config.run_tests and tests:
        steps.append(_Step("Pytest", ("uv", "run", "pytest", *tests)))

    return steps


def _format_command(command: tuple[str, ...]) -> str:
    return " ".join(command)


def _load_baseline_hashes(path: Path = Path(_SECRET_BASELINE)) -> set[str]:
    """Hashes of secrets explicitly accepted in ``.secrets.baseline``."""
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    results = data.get("results", {})
    if not isinstance(results, dict):
        return set()
    hashes: set[str] = set()
    for items in results.values():
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                digest = item.get("hashed_secret")
                if isinstance(digest, str):
                    hashes.add(digest)
    return hashes


def _evaluate_secret_report(path: Path = Path(_SECRET_REPORT)) -> tuple[int, str]:
    """Return (unbaselined_finding_count, human_summary) for the scan report."""
    if not path.exists():
        return 0, "no report"
    data = json.loads(path.read_text(encoding="utf-8"))
    results = data.get("results", {})
    if not isinstance(results, dict):
        return 0, "0 findings"
    baseline = _load_baseline_hashes()
    total = 0
    unbaselined = 0
    per_file: list[str] = []
    for item_path, items in results.items():
        if not isinstance(items, list) or not items:
            continue
        total += len(items)
        flagged = [
            item
            for item in items
            if not (
                isinstance(item, dict)
                and item.get("hashed_secret") in baseline
            )
        ]
        if flagged:
            unbaselined += len(flagged)
            per_file.append(f"{item_path}: {len(flagged)}")
    if total == 0:
        return 0, "0 findings"
    baselined = total - unbaselined
    summary = f"{unbaselined} new finding(s)"
    if baselined:
        summary += f" ({baselined} baselined)"
    if per_file:
        summary += " — " + "; ".join(per_file)
    return unbaselined, summary


def _run_step(step: _Step, *, dry_run: bool, palette: _Palette) -> _StepResult:
    logger.info("")
    logger.info("%s %s", palette.bold("==>"), palette.bold(step.name))
    logger.info("%s", palette.dim(_format_command(step.command)))
    if dry_run:
        return _StepResult(step.name, "skip", detail="dry-run")

    start = time.monotonic()
    if step.stdout_path is None:
        completed = subprocess.run(step.command, check=False)  # nosec B603
        duration = time.monotonic() - start
        if completed.returncode != 0:
            return _StepResult(
                step.name, "fail", duration, f"exit {completed.returncode}"
            )
        return _StepResult(step.name, "ok", duration)

    with step.stdout_path.open("w", encoding="utf-8") as out:
        completed = subprocess.run(  # nosec B603
            step.command, check=False, stdout=out
        )
    duration = time.monotonic() - start
    if completed.returncode != 0:
        return _StepResult(
            step.name, "fail", duration, f"exit {completed.returncode}"
        )

    if step.is_secret_scan:
        findings, summary = _evaluate_secret_report(step.stdout_path)
        logger.info("detect-secrets: %s", summary)
        if findings > 0:
            return _StepResult(step.name, "fail", duration, summary)
        return _StepResult(step.name, "ok", duration, summary)

    return _StepResult(step.name, "ok", duration)


def _render_summary(results: list[_StepResult], palette: _Palette) -> None:
    if not results:
        return
    name_width = max(len(r.name) for r in results)
    logger.info("")
    logger.info("%s", palette.bold("CI summary"))
    logger.info("%s", palette.dim("-" * (name_width + 22)))
    for result in results:
        if result.status == "ok":
            mark = palette.green("✓ pass")
        elif result.status == "fail":
            mark = palette.red("✗ fail")
        else:
            mark = palette.yellow("• skip")
        timing = f"{result.duration:6.2f}s" if result.duration else "      "
        detail = palette.dim(f"  {result.detail}") if result.detail else ""
        logger.info(
            "  %s  %-*s  %s%s",
            mark,
            name_width,
            result.name,
            palette.dim(timing),
            detail,
        )
    logger.info("%s", palette.dim("-" * (name_width + 22)))


def run_ci(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    config = _config_from_args(args)
    palette = _Palette(enabled=_supports_color())
    results: list[_StepResult] = []
    for step in _build_steps(config):
        results.append(_run_step(step, dry_run=bool(args.dry_run), palette=palette))

    _render_summary(results, palette)
    failures = [r for r in results if r.status == "fail"]
    if failures:
        names = ", ".join(r.name for r in failures)
        logger.error("%s", palette.red(f"CI gate failed: {names}"))
        return 1
    logger.info("%s", palette.green("CI gate passed"))
    return 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(run_ci())


if __name__ == "__main__":
    main()
