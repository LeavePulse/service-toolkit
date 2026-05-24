"""Local runner for the shared Python service CI quality gate."""

from __future__ import annotations

import argparse
import json
import logging
import subprocess  # nosec B404
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
_SECRET_REPORT = ".secrets.scan.json"
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _Step:
    name: str
    command: tuple[str, ...]
    stdout_path: Path | None = None


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
    )


def _existing_paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(path for path in paths if Path(path).exists())


def _build_steps(config: _CiConfig) -> list[_Step]:
    sources = _existing_paths(config.source_paths)
    if not sources:
        msg = f"No source paths found from: {', '.join(config.source_paths)}"
        raise SystemExit(msg)

    steps: list[_Step] = []
    if config.sync:
        steps.append(_Step("Install dependencies", ("uv", "sync", "--locked", "--no-sources")))

    steps.append(_Step("Ruff", ("uv", "run", "ruff", "check", *sources)))

    if config.run_arch_lint:
        steps.append(_Step("Architecture Linter", ("uv", "run", "lp-arch-lint", *sources)))

    if config.run_mypy:
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
                    *sources,
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
            )
        )

    tests = _existing_paths(config.test_paths)
    if config.run_tests and tests:
        steps.append(_Step("Pytest", ("uv", "run", "pytest", *tests)))

    return steps


def _format_command(command: tuple[str, ...]) -> str:
    return " ".join(command)


def _run_step(step: _Step, *, dry_run: bool) -> None:
    logger.info("")
    logger.info("==> %s", step.name)
    logger.info("%s", _format_command(step.command))
    if dry_run:
        return
    if step.stdout_path is None:
        subprocess.run(step.command, check=True)  # nosec B603
        return
    with step.stdout_path.open("w", encoding="utf-8") as out:
        subprocess.run(step.command, check=True, stdout=out)  # nosec B603


def _summarize_secret_report(path: Path = Path(_SECRET_REPORT)) -> None:
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    results = data.get("results", {})
    if not isinstance(results, dict):
        logger.info("detect-secrets findings: 0")
        return
    total = sum(len(items) for items in results.values() if isinstance(items, list))
    logger.info("detect-secrets findings: %s", total)
    for item_path, items in results.items():
        if isinstance(items, list) and items:
            logger.info("- %s: %s", item_path, len(items))


def run_ci(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    config = _config_from_args(args)
    for step in _build_steps(config):
        _run_step(step, dry_run=bool(args.dry_run))
        if step.stdout_path is not None and not args.dry_run:
            _summarize_secret_report(step.stdout_path)
    return 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        raise SystemExit(run_ci())
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode) from exc


if __name__ == "__main__":
    main()
