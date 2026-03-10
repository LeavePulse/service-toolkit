"""Auth helpers shared across services.

These utilities intentionally focus on mechanics: JWT validation, JWKS caching,
and standard token claims. Domain-specific authorization logic stays in each
service.
"""

from __future__ import annotations

import importlib
import sys

__all__ = [
    "AuthUser",
    "JWKSCache",
    "JWTPayload",
    "JWTVerificationError",
    "JWTVerifier",
    "build_shared_jwt_verifier",
    "build_user",
]

_EXPORT_MODULES = {
    "AuthUser": ".types",
    "JWKSCache": ".jwks",
    "JWTPayload": ".schemas",
    "JWTVerificationError": ".verifier",
    "JWTVerifier": ".verifier",
    "build_shared_jwt_verifier": ".verifier",
    "build_user": ".types",
}
_SUBMODULES = {
    "jwks": ".jwks",
    "schemas": ".schemas",
    "types": ".types",
    "verifier": ".verifier",
}


def __getattr__(name: str):
    module_name = _EXPORT_MODULES.get(name) or _SUBMODULES.get(name)
    if module_name is None:
        raise AttributeError(name)

    try:
        module = importlib.import_module(module_name, __name__)
    except ModuleNotFoundError as exc:  # pragma: no cover - runtime guard
        if (exc.name or "") in {"httpx", "jose", "msgspec"}:
            raise ModuleNotFoundError(
                "Auth helpers require the optional 'auth' extra. "
                "Install with 'pip install service-toolkit[auth]'."
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
