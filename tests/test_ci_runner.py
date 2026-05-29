"""Tests for the thin ``lp-ci`` Python shim.

The gate logic (step building, pyproject parsing, secret-baseline evaluation)
now lives in the Rust crate and is covered by its own ``cargo test``. The shim
only has one job: forward ``argv`` to ``service_toolkit_rust.run_ci`` and return
its exit code. We assert exactly that, without requiring the compiled extension
to be installed (it is stubbed).
"""

from __future__ import annotations

import sys
import types

import pytest

from service_toolkit.dev import ci_runner


def _install_stub(monkeypatch: pytest.MonkeyPatch, recorder: list[list[str]]) -> None:
    """Install a fake ``service_toolkit_rust`` whose ``run_ci`` records argv."""

    def fake_run_ci(argv: list[str]) -> int:
        recorder.append(list(argv))
        return 0

    stub = types.ModuleType("service_toolkit_rust")
    stub.run_ci = fake_run_ci  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "service_toolkit_rust", stub)


def test_forwards_explicit_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    _install_stub(monkeypatch, seen)

    code = ci_runner.run_ci(["--no-sync", "--changed", "HEAD"])

    assert code == 0
    assert seen == [["--no-sync", "--changed", "HEAD"]]


def test_defaults_to_sys_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    _install_stub(monkeypatch, seen)
    monkeypatch.setattr(sys, "argv", ["lp-ci", "--no-tests"])

    code = ci_runner.run_ci()

    assert code == 0
    assert seen == [["--no-tests"]]


def test_returns_rust_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = types.ModuleType("service_toolkit_rust")
    stub.run_ci = lambda _argv: 1  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "service_toolkit_rust", stub)

    assert ci_runner.run_ci([]) == 1


def test_missing_extension_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    # Simulate the 'rust' extra not being installed.
    monkeypatch.setitem(sys.modules, "service_toolkit_rust", None)

    with pytest.raises(SystemExit) as excinfo:
        ci_runner.run_ci([])
    assert excinfo.value.code == 2
