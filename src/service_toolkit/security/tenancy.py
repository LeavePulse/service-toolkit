"""Row scoping: restrict a query to what the calling subject owns.

Mechanism only — no product knowledge. Like the bitset helpers next door, this
module knows *how* to narrow a query, never *which* permissions or products
exist; permission catalogs stay in the owning service's SDK.

Two axes exist because "is this mine?" has two shapes:

* **owner** — physical things a subject owns (``owner_kind`` + ``owner_id``);
  ids stay numeric and the pair is indexable, unlike a packed ``"kind:id"``
  string.
* **tenant** — capacity or workloads rented by a subject (a single
  ``tenant_id`` column).

Register each model once, then every call site reads the same:

    stmt = scoped(select(Device), Device, subject)

A model that was never registered raises :class:`UnscopedModelError` instead of
silently returning everything — a forgotten registration must fail loudly, since
the failure mode is leaking another subject's rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sqlalchemy.sql import Select

_S = TypeVar("_S", bound="Select[Any]")

__all__ = [
    "ScopeAxis",
    "ScopeSubject",
    "UnscopedModelError",
    "register_owner_scope",
    "register_tenant_scope",
    "scoped",
]


class ScopeAxis(StrEnum):
    """Which shape of ownership a model carries."""

    OWNER = "owner"
    TENANT = "tenant"


class UnscopedModelError(LookupError):
    """Raised when scoping a model that has no registered axis."""

    def __init__(self, model: type) -> None:
        super().__init__(
            f"{model.__name__} has no registered scope axis; call "
            "register_owner_scope/register_tenant_scope at import time"
        )


@dataclass(frozen=True, slots=True)
class ScopeSubject:
    """Who is asking, resolved server-side.

    ``account_ids`` comes from membership (auth's ``ResolveAccounts``), never
    from a client-supplied claim — otherwise a caller could widen its own scope.
    ``is_platform_operator`` bypasses scoping for operator tooling, where seeing
    the whole fleet is the point.
    """

    account_ids: frozenset[int] = field(default_factory=frozenset)
    is_platform_operator: bool = False

    @classmethod
    def for_accounts(
        cls, account_ids: Iterable[int], *, is_platform_operator: bool = False
    ) -> ScopeSubject:
        return cls(
            account_ids=frozenset(int(a) for a in account_ids),
            is_platform_operator=is_platform_operator,
        )


@dataclass(frozen=True, slots=True)
class _OwnerScope:
    """Owned via a (kind, id) pair.

    Column *names* rather than instrumented attributes, so registration does not
    depend on mapper configuration order at import time.
    """

    kind_column: str
    id_column: str
    owner_kind: str

    axis = ScopeAxis.OWNER


@dataclass(frozen=True, slots=True)
class _TenantScope:
    """Belongs to a subject via a single tenant column."""

    id_column: str

    axis = ScopeAxis.TENANT


_REGISTRY: dict[type, _OwnerScope | _TenantScope] = {}


def register_owner_scope(
    model: type,
    *,
    kind_column: str = "owner_kind",
    id_column: str = "owner_id",
    owner_kind: str = "account",
) -> None:
    """Declare that *model* is owned via a (kind, id) pair."""
    _REGISTRY[model] = _OwnerScope(
        kind_column=kind_column, id_column=id_column, owner_kind=owner_kind
    )


def register_tenant_scope(model: type, *, id_column: str = "tenant_id") -> None:
    """Declare that *model* belongs to a subject via a single tenant column."""
    _REGISTRY[model] = _TenantScope(id_column=id_column)


def scoped(stmt: _S, model: type, subject: ScopeSubject) -> _S:
    """Narrow *stmt* to the rows of *model* that *subject* may see.

    Operators pass through unchanged. A subject with no accounts gets a query
    that matches nothing — fail closed, not open.
    """
    registration = _REGISTRY.get(model)
    if registration is None:
        raise UnscopedModelError(model)

    if subject.is_platform_operator:
        return stmt

    if not subject.account_ids:
        # No membership resolved: deny rather than fall through to an
        # unfiltered query.
        return stmt.where(_false())

    id_column = getattr(model, registration.id_column)
    narrowed = stmt.where(id_column.in_(sorted(subject.account_ids)))

    if isinstance(registration, _OwnerScope):
        kind_column = getattr(model, registration.kind_column)
        narrowed = narrowed.where(kind_column == registration.owner_kind)

    return narrowed


def _false() -> Any:  # noqa: ANN401 - SQLAlchemy expression
    from sqlalchemy import false

    return false()
