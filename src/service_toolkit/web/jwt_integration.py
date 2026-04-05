"""Generic JWT/Litestar integration primitives."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class JWTAuthIntegration:
    """Provider-owned JWT wiring consumed by ``create_service_app()``."""

    jwt_verifier: object
    middleware_class: type[Any]
    dependencies: Mapping[str, Any] = field(default_factory=dict)
    middleware_kwargs: Mapping[str, Any] = field(default_factory=dict)


__all__ = ["JWTAuthIntegration"]
