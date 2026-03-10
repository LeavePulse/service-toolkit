"""Database helpers shared across services."""

from __future__ import annotations

import importlib
import sys

__all__ = [
    "Base",
    "DBConfig",
    "TimestampMixin",
    "build_db_config",
    "install_slow_query_logging",
    "utcnow",
]

_EXPORT_MODULES = {
    "Base": ".sqlalchemy",
    "DBConfig": ".litestar",
    "TimestampMixin": ".sqlalchemy",
    "build_db_config": ".litestar",
    "install_slow_query_logging": ".observability",
    "utcnow": ".sqlalchemy",
}
_SUBMODULES = {
    "litestar": ".litestar",
    "observability": ".observability",
    "sqlalchemy": ".sqlalchemy",
}


def __getattr__(name: str):
    module_name = _EXPORT_MODULES.get(name) or _SUBMODULES.get(name)
    if module_name is None:
        raise AttributeError(name)

    try:
        module = importlib.import_module(module_name, __name__)
    except ModuleNotFoundError as exc:  # pragma: no cover - runtime guard
        if (exc.name or "") in {"advanced_alchemy", "sqlalchemy"}:
            raise ModuleNotFoundError(
                "Database helpers require the optional 'sqlalchemy' extra. "
                "Install with 'pip install service-toolkit[sqlalchemy]'."
            ) from exc
        raise

    if name in _SUBMODULES:
        value = module
    else:
        value = getattr(module, name)
    setattr(sys.modules[__name__], name, value)
    return value


def __dir__() -> list[str]:  # pragma: no cover - reflection helper
    return sorted(set(__all__))
