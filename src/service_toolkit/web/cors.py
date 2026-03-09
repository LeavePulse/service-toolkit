"""CORS helper utilities shared across services."""

from __future__ import annotations

from collections.abc import Sequence


def resolve_cors_origins(
    *,
    debug: bool,
    allow_origins: Sequence[str] | None = None,
    allow_origins_debug: Sequence[str] | None = None,
) -> list[str]:
    """Resolve allowed CORS origins based on debug mode.

    In debug mode, prefer `allow_origins_debug` (fallback to `allow_origins`).
    In non-debug mode, prefer `allow_origins` (fallback to `allow_origins_debug`).
    """

    if debug:
        origins = allow_origins_debug or allow_origins or ()
    else:
        origins = allow_origins or allow_origins_debug or ()
    return [str(origin).strip() for origin in origins if str(origin).strip()]


__all__ = ["resolve_cors_origins"]
