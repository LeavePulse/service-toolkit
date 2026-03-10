"""Error integration helpers."""

from __future__ import annotations

import importlib
import sys

__all__ = [
    "ErrorResponseFormat",
    "ErrorTranslator",
    "apply_problem_details",
    "build_error_translator_with_defaults",
    "build_standard_exception_handlers",
]

_EXPORT_MODULES = {
    "ErrorResponseFormat": ".awesome_errors",
    "ErrorTranslator": ".awesome_errors",
    "apply_problem_details": ".awesome_errors",
    "build_error_translator_with_defaults": ".awesome_errors",
    "build_standard_exception_handlers": ".awesome_errors",
}
_SUBMODULES = {
    "awesome_errors": ".awesome_errors",
}


def __getattr__(name: str):
    module_name = _EXPORT_MODULES.get(name) or _SUBMODULES.get(name)
    if module_name is None:
        raise AttributeError(name)

    try:
        module = importlib.import_module(module_name, __name__)
    except ModuleNotFoundError as exc:  # pragma: no cover - runtime guard
        if exc.name == "awesome_errors":
            raise ModuleNotFoundError(
                "Error helpers require the optional 'errors' extra. "
                "Install with 'pip install service-toolkit[errors]'."
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
