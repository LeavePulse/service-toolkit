"""Runtime helpers for Prometheus multiprocess metrics."""

from __future__ import annotations

import argparse
import os
from collections.abc import MutableMapping, Sequence
from pathlib import Path

DEFAULT_MULTIPROC_DIRECTORY = "/tmp/metrics"  # nosec B108 - prometheus_client multiprocess dir, overridable via env


def resolve_multiprocess_directory(
    *,
    directory: str | os.PathLike[str] | None = None,
    default_directory: str | os.PathLike[str] = DEFAULT_MULTIPROC_DIRECTORY,
    env: MutableMapping[str, str] | None = None,
) -> Path:
    """Resolve the Prometheus multiprocess directory using env or a default."""
    env_map = env if env is not None else os.environ
    raw_value = (
        directory
        or env_map.get("PROMETHEUS_MULTIPROC_DIR")
        or env_map.get("prometheus_multiproc_dir")
        or default_directory
    )
    return Path(str(raw_value))


def prepare_multiprocess_directory(
    directory: str | os.PathLike[str] | None = None,
    *,
    default_directory: str | os.PathLike[str] = DEFAULT_MULTIPROC_DIRECTORY,
    env: MutableMapping[str, str] | None = None,
) -> Path:
    """Set the multiprocess env vars and clean the target directory once."""
    env_map = env if env is not None else os.environ
    target = resolve_multiprocess_directory(
        directory=directory,
        default_directory=default_directory,
        env=env_map,
    )
    resolved = str(target)
    env_map["PROMETHEUS_MULTIPROC_DIR"] = resolved
    env_map["prometheus_multiproc_dir"] = resolved

    target.mkdir(parents=True, exist_ok=True)
    for item in target.iterdir():
        if item.is_file():
            item.unlink(missing_ok=True)
    return target


def exec_with_prepared_multiprocess_directory(
    command: Sequence[str],
    *,
    directory: str | os.PathLike[str] | None = None,
    default_directory: str | os.PathLike[str] = DEFAULT_MULTIPROC_DIRECTORY,
    env: MutableMapping[str, str] | None = None,
) -> None:
    """Prepare multiprocess metrics storage and replace the process."""
    if not command:
        raise ValueError("Command must not be empty")

    env_map = env if env is not None else os.environ
    prepare_multiprocess_directory(
        directory=directory,
        default_directory=default_directory,
        env=env_map,
    )
    os.execvpe(command[0], list(command), dict(env_map))  # nosec B606 - CLI entrypoint exec'ing its own argv (e.g. gunicorn)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lp-prometheus-runtime",
        description="Prepare Prometheus multiprocess storage and exec a command.",
    )
    parser.add_argument(
        "--directory",
        help="Override the multiprocess directory. Defaults to env or /tmp/metrics.",
    )
    parser.add_argument(
        "--default-directory",
        default=DEFAULT_MULTIPROC_DIRECTORY,
        help="Fallback directory when PROMETHEUS_MULTIPROC_DIR is unset.",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Command to execute. Prefix with -- to separate wrapper flags.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("missing command to execute")

    exec_with_prepared_multiprocess_directory(
        command,
        directory=args.directory,
        default_directory=args.default_directory,
    )
    return 0


__all__ = [
    "DEFAULT_MULTIPROC_DIRECTORY",
    "exec_with_prepared_multiprocess_directory",
    "main",
    "prepare_multiprocess_directory",
    "resolve_multiprocess_directory",
]


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
