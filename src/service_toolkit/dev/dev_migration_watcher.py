"""Development helper: auto-apply Alembic migrations on file changes.

This is intended to be run in DEBUG mode inside service containers.
"""
# noqa: archlint=env-access — DEBUG-only dev helper: reads its own env knobs
# directly; not part of any service's request path.

from __future__ import annotations

import logging
import os
import subprocess  # nosec B404 - DEBUG-only dev helper shelling out to Alembic
import sys
import time

try:
    from watchfiles import watch
except ModuleNotFoundError as exc:  # pragma: no cover
    if exc.name == "watchfiles":
        raise ModuleNotFoundError(
            "dev_migration_watcher requires 'watchfiles'. "
            "It's typically provided by 'uvicorn[standard]'."
        ) from exc
    raise

logger = logging.getLogger(__name__)


def _run_upgrade(*, cwd: str = "/app") -> None:
    started = time.perf_counter()
    try:
        proc = subprocess.run(  # noqa: S603  # nosec B603 - fixed argv, no shell, no user input
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=cwd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        logger.exception("[migrations] upgrade failed")
        return

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    out = (proc.stdout or "").strip()
    if proc.returncode != 0:
        logger.error(
            "[migrations] alembic upgrade head failed (exit=%s, %sms):\n%s",
            proc.returncode,
            elapsed_ms,
            out,
        )
        return

    if out:
        logger.info("[migrations] applied (%sms):\n%s", elapsed_ms, out)


def main() -> None:
    """Watch migrations and run `alembic upgrade head` automatically (dev only)."""

    logging.basicConfig(level=logging.INFO)
    watch_path = os.getenv("MIGRATIONS_WATCH_PATH", "/app/migrations/versions")
    debounce_ms = int(os.getenv("MIGRATIONS_WATCH_DEBOUNCE_MS", "800"))
    cwd = os.getenv("MIGRATIONS_WATCH_CWD", "/app")

    logger.info("[migrations] watching: %s (debounce=%sms)", watch_path, debounce_ms)
    for _changes in watch(watch_path, debounce=debounce_ms):
        _run_upgrade(cwd=cwd)


__all__ = ["main"]


if __name__ == "__main__":  # pragma: no cover
    main()
