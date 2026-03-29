"""Projection-aware REST response helpers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, TypeVar, cast

_T = TypeVar("_T")


def _normalize_path(path: str) -> str:
    segments = [segment.strip().lower() for segment in str(path).split(".")]
    normalized = [segment for segment in segments if segment]
    return ".".join(normalized)


def _normalize_paths(raw: str | Iterable[str] | None) -> frozenset[str]:
    if raw is None:
        return frozenset()

    if isinstance(raw, str):
        candidates = raw.split(",")
    else:
        candidates = []
        for value in raw:
            candidates.extend(str(value).split(","))

    normalized = {_normalize_path(candidate) for candidate in candidates}
    normalized.discard("")
    return frozenset(normalized)


def _is_same_or_descendant(*, value: str, parent: str) -> bool:
    return value == parent or value.startswith(f"{parent}.")


def _paths_related(left: str, right: str) -> bool:
    return _is_same_or_descendant(value=left, parent=right) or _is_same_or_descendant(
        value=right,
        parent=left,
    )


@dataclass(frozen=True, slots=True)
class ProjectionSpec:
    """Normalized client projection query for one endpoint."""

    fields: frozenset[str] = field(default_factory=frozenset)
    include: frozenset[str] = field(default_factory=frozenset)
    exclude: frozenset[str] = field(default_factory=frozenset)
    field_restricted: bool = False

    @classmethod
    def from_query_params(
        cls,
        *,
        fields: str | Iterable[str] | None = None,
        include: str | Iterable[str] | None = None,
        exclude: str | Iterable[str] | None = None,
    ) -> ProjectionSpec:
        return cls(
            fields=_normalize_paths(fields),
            include=_normalize_paths(include),
            exclude=_normalize_paths(exclude),
            field_restricted=fields is not None,
        )

    @property
    def is_default(self) -> bool:
        return not self.field_restricted and not self.include and not self.exclude

    def excludes_path(self, path: str) -> bool:
        normalized = _normalize_path(path)
        if not normalized:
            return False
        return any(
            _is_same_or_descendant(value=normalized, parent=excluded)
            for excluded in self.exclude
        )

    def has(self, path: str) -> bool:
        normalized = _normalize_path(path)
        if not normalized:
            return False
        if self.excludes_path(normalized):
            return False
        if not self.field_restricted:
            return True
        return normalized in self.fields or normalized in self.include

    def has_any(self, *paths: str) -> bool:
        return any(self.has(path) for path in paths)

    def includes(self, path: str) -> bool:
        normalized = _normalize_path(path)
        if not normalized:
            return False
        return any(
            _paths_related(requested, normalized) and not self.excludes_path(requested)
            for requested in self.include
        )

    def needs(self, path: str) -> bool:
        normalized = _normalize_path(path)
        if not normalized:
            return False
        if self.excludes_path(normalized):
            return False
        if not self.field_restricted:
            return True
        requested = self.fields | self.include
        return any(
            _paths_related(entry, normalized) and not self.excludes_path(entry)
            for entry in requested
        )

    def restrict_to(self, allowed_paths: Iterable[str]) -> ProjectionSpec:
        normalized_allowed = _normalize_paths(allowed_paths)
        if not normalized_allowed:
            return ProjectionSpec(field_restricted=self.field_restricted)

        def _keep(path: str) -> bool:
            return any(_paths_related(path, allowed) for allowed in normalized_allowed)

        return ProjectionSpec(
            fields=frozenset(path for path in self.fields if _keep(path)),
            include=frozenset(path for path in self.include if _keep(path)),
            exclude=frozenset(path for path in self.exclude if _keep(path)),
            field_restricted=self.field_restricted,
        )

    def excluding(self, paths: str | Iterable[str] | None) -> ProjectionSpec:
        return ProjectionSpec(
            fields=self.fields,
            include=self.include,
            exclude=self.exclude | _normalize_paths(paths),
            field_restricted=self.field_restricted,
        )

    def child(self, prefix: str) -> ProjectionSpec:
        normalized_prefix = _normalize_path(prefix)
        if not normalized_prefix:
            return self
        if not self.field_restricted and not self.include and not self.exclude:
            return self

        child_fields: set[str] = set()
        child_include: set[str] = set()
        child_exclude: set[str] = set()
        child_full_fields = False

        for path in self.fields:
            if path == normalized_prefix:
                child_full_fields = True
                continue
            if _is_same_or_descendant(value=path, parent=normalized_prefix):
                child_fields.add(path.removeprefix(f"{normalized_prefix}."))

        for path in self.include:
            if path == normalized_prefix:
                child_include.add(path)
                continue
            if _is_same_or_descendant(value=path, parent=normalized_prefix):
                child_include.add(path.removeprefix(f"{normalized_prefix}."))

        for path in self.exclude:
            if path == normalized_prefix:
                child_exclude.add(path)
                continue
            if _is_same_or_descendant(value=path, parent=normalized_prefix):
                child_exclude.add(path.removeprefix(f"{normalized_prefix}."))

        return ProjectionSpec(
            fields=frozenset(filter(None, child_fields)),
            include=frozenset(filter(None, child_include)),
            exclude=frozenset(filter(None, child_exclude)),
            field_restricted=self.field_restricted and not child_full_fields,
        )


@dataclass(frozen=True, slots=True)
class ResponsePolicy:
    """Permission-aware visibility policy for response paths."""

    allowed_fields: frozenset[str] = field(default_factory=frozenset)
    denied_fields: frozenset[str] = field(default_factory=frozenset)
    allow_all: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_fields", _normalize_paths(self.allowed_fields))
        object.__setattr__(self, "denied_fields", _normalize_paths(self.denied_fields))

    def can_view(self, path: str) -> bool:
        normalized = _normalize_path(path)
        if not normalized:
            return False
        if any(
            _is_same_or_descendant(value=normalized, parent=denied)
            for denied in self.denied_fields
        ):
            return False
        if self.allow_all:
            return True
        return any(
            _paths_related(allowed, normalized) for allowed in self.allowed_fields
        )

    def filter_allowed(self, paths: Iterable[str]) -> list[str]:
        return [path for path in paths if self.can_view(path)]

    def child(self, prefix: str) -> ResponsePolicy:
        normalized_prefix = _normalize_path(prefix)
        if not normalized_prefix:
            return self

        def _trim(paths: frozenset[str]) -> frozenset[str]:
            trimmed: set[str] = set()
            for path in paths:
                if path == normalized_prefix:
                    trimmed.add(path)
                    continue
                if _is_same_or_descendant(value=path, parent=normalized_prefix):
                    trimmed.add(path.removeprefix(f"{normalized_prefix}."))
            return frozenset(filter(None, trimmed))

        return ResponsePolicy(
            allowed_fields=_trim(self.allowed_fields),
            denied_fields=_trim(self.denied_fields),
            allow_all=self.allow_all and self.can_view(normalized_prefix),
        )


class ExpansionLoader:
    """Request-scoped lazy loader with memoized async lookups."""

    __slots__ = ("_cache",)

    def __init__(self) -> None:
        self._cache: dict[str, Any] = {}

    async def get_or_load(
        self,
        key: str,
        loader: Callable[[], Awaitable[_T]],
    ) -> _T:
        if key in self._cache:
            return cast("_T", self._cache[key])

        value = await loader()
        self._cache[key] = value
        return value

    def prime(self, key: str, value: Any) -> None:
        self._cache[key] = value


__all__ = [
    "ExpansionLoader",
    "ProjectionSpec",
    "ResponsePolicy",
]
