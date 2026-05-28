from __future__ import annotations

import json
from pathlib import Path

import pytest

from service_toolkit.dev.ci_runner import (
    _build_steps,
    _config_from_args,
    _evaluate_secret_report,
    _load_baseline_hashes,
    _parse_args,
)


def test_ci_runner_matches_python_service_quality_steps(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'example'\n")
    monkeypatch.chdir(tmp_path)

    config = _config_from_args(_parse_args([]))
    commands = [step.command for step in _build_steps(config)]

    assert commands == [
        ("uv", "sync", "--locked", "--no-sources"),
        ("uv", "run", "ruff", "check", "src"),
        ("uv", "run", "lp-arch-lint", "src"),
        (
            "uv",
            "run",
            "--with",
            "mypy",
            "mypy",
            "--ignore-missing-imports",
            "--check-untyped-defs",
            "src",
        ),
        (
            "uv",
            "run",
            "--with",
            "bandit",
            "bandit",
            "-r",
            "src",
            "-q",
            "-s",
            "B104,B105,B106",
            "-x",
            "tests,migrations,alembic/versions",
        ),
        (
            "uv",
            "run",
            "--with",
            "detect-secrets",
            "detect-secrets",
            "scan",
            "src",
            ".github/workflows",
            "pyproject.toml",
            "--exclude-files",
            (
                r"(^|/)(node_modules|\.venv|venv|dist|build|\.git|migrations|"
                r"alembic/versions|tests?)/"
            ),
        ),
        ("uv", "run", "pytest", "tests"),
    ]


def test_ci_runner_uses_pyproject_overrides(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "specs").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        """
[tool.service_toolkit.ci]
source_paths = ["app"]
test_paths = ["specs"]
sync = false
run_secrets = false
bandit_skip = "B104,B608"
bandit_exclude = "specs,migrations"
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    config = _config_from_args(_parse_args([]))
    commands = [step.command for step in _build_steps(config)]

    assert commands[0] == ("uv", "run", "ruff", "check", "app")
    assert ("uv", "sync", "--locked", "--no-sources") not in commands
    assert not any("detect-secrets" in command for command in commands)
    assert (
        "uv",
        "run",
        "--with",
        "bandit",
        "bandit",
        "-r",
        "app",
        "-q",
        "-s",
        "B104,B608",
        "-x",
        "specs,migrations",
    ) in commands
    assert commands[-1] == ("uv", "run", "pytest", "specs")


def test_changed_mode_limits_lint_to_changed_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "touched.py").write_text("x = 1\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'example'\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "service_toolkit.dev.ci_runner._changed_python_files",
        lambda base, roots: ("src/touched.py",),
    )

    config = _config_from_args(_parse_args(["--no-sync", "--changed"]))
    commands = [step.command for step in _build_steps(config)]

    assert ("uv", "run", "ruff", "check", "src/touched.py") in commands
    mypy_cmd = next(c for c in commands if "mypy" in c)
    assert mypy_cmd[-1] == "src/touched.py"
    # Bandit still scans the whole source root, not just changed files.
    assert ("uv", "run", "--with", "bandit", "bandit", "-r", "src",
            "-q", "-s", "B104,B105,B106", "-x",
            "tests,migrations,alembic/versions") in commands


def test_changed_mode_skips_lint_when_no_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'example'\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "service_toolkit.dev.ci_runner._changed_python_files",
        lambda base, roots: (),
    )

    config = _config_from_args(_parse_args(["--no-sync", "--no-bandit", "--changed"]))
    names = [step.name for step in _build_steps(config)]

    assert "Ruff" not in names
    assert "MyPy" not in names


def test_evaluate_secret_report_flags_new_findings(tmp_path: Path) -> None:
    report = tmp_path / "scan.json"
    report.write_text(
        json.dumps(
            {"results": {"src/app.py": [{"hashed_secret": "deadbeef", "type": "X"}]}}
        )
    )
    count, summary = _evaluate_secret_report(report)
    assert count == 1
    assert "1 new finding" in summary


def test_evaluate_secret_report_ignores_baselined(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    report = tmp_path / "scan.json"
    report.write_text(
        json.dumps(
            {"results": {"src/app.py": [{"hashed_secret": "deadbeef", "type": "X"}]}}
        )
    )
    monkeypatch.setattr(
        "service_toolkit.dev.ci_runner._load_baseline_hashes",
        lambda *a, **k: {"deadbeef"},
    )
    count, summary = _evaluate_secret_report(report)
    assert count == 0
    assert "baselined" in summary


def test_load_baseline_hashes_reads_results(tmp_path: Path) -> None:
    baseline = tmp_path / ".secrets.baseline"
    baseline.write_text(
        json.dumps(
            {
                "results": {
                    "a.yml": [{"hashed_secret": "aaa"}, {"hashed_secret": "bbb"}],
                }
            }
        )
    )
    assert _load_baseline_hashes(baseline) == {"aaa", "bbb"}


def test_load_baseline_hashes_missing_file(tmp_path: Path) -> None:
    assert _load_baseline_hashes(tmp_path / "nope.baseline") == set()
