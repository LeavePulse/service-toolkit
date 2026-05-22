from __future__ import annotations

from pathlib import Path

import pytest

from service_toolkit.dev.ci_runner import _build_steps, _config_from_args, _parse_args


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
