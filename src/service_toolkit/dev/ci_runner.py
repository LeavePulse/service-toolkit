"""Entry point for the shared CI quality gate.

The gate logic (pyproject parsing, step building, secret-baseline evaluation,
the summary table and exit semantics) lives in the Rust crate
``service_toolkit_rust`` and is shared with the native ``lp-ci`` binary used by
Rust repos. This module is a thin shim that forwards ``sys.argv`` to it, so the
behaviour can never drift between the Python and Rust entry points.

Requires the ``rust`` extra (``service-toolkit[rust]``), which ships the
compiled extension.
"""

from __future__ import annotations

import sys


def run_ci(argv: list[str] | None = None) -> int:
    """Run the gate; return its process exit code."""
    try:
        from service_toolkit_rust import run_ci as _run_ci
    except ImportError as exc:  # pragma: no cover - misconfigured environment
        sys.stderr.write(
            "lp-ci requires the 'rust' extra: install "
            "service-toolkit[rust] in your dev dependencies.\n"
        )
        raise SystemExit(2) from exc
    args = list(sys.argv[1:] if argv is None else argv)
    return int(_run_ci(args))


def main() -> None:
    raise SystemExit(run_ci())


if __name__ == "__main__":
    main()
