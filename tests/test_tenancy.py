"""Tests for row scoping."""

from __future__ import annotations

import pytest
from sqlalchemy import BigInteger, String, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from service_toolkit.security.tenancy import (
    ScopeSubject,
    UnscopedModelError,
    register_owner_scope,
    register_tenant_scope,
    scoped,
)


class _Base(DeclarativeBase):
    pass


class _Device(_Base):
    """Owned via a (kind, id) pair — ids stay numeric."""

    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner_kind: Mapped[str] = mapped_column(String(16), default="account")
    owner_id: Mapped[int] = mapped_column(BigInteger, default=0)


class _Allocation(_Base):
    """Rented capacity — a single tenant column."""

    __tablename__ = "allocations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigInteger, default=0)


class _Unregistered(_Base):
    __tablename__ = "unregistered"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigInteger, default=0)


register_owner_scope(_Device)
register_tenant_scope(_Allocation)


def _sql(stmt: object) -> str:
    return str(stmt).replace("\n", " ")


def test_owner_axis_filters_by_kind_and_id() -> None:
    subject = ScopeSubject.for_accounts([7])

    sql = _sql(scoped(select(_Device), _Device, subject))

    assert "devices.owner_id IN" in sql
    assert "devices.owner_kind =" in sql


def test_tenant_axis_filters_by_tenant_only() -> None:
    subject = ScopeSubject.for_accounts([7])

    sql = _sql(scoped(select(_Allocation), _Allocation, subject))

    assert "allocations.tenant_id IN" in sql
    assert "owner_kind" not in sql


def test_multiple_accounts_are_all_included() -> None:
    stmt = scoped(select(_Device), _Device, ScopeSubject.for_accounts([9, 7, 8]))

    sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "IN (7, 8, 9)" in sql.replace("\n", " ")


def test_subject_without_accounts_matches_nothing() -> None:
    """Fail closed: no membership must not fall through to an open query."""
    sql = _sql(scoped(select(_Device), _Device, ScopeSubject()))

    assert "false" in sql.lower()
    assert "owner_id IN" not in sql


def test_operator_bypasses_scoping() -> None:
    subject = ScopeSubject.for_accounts([], is_platform_operator=True)

    sql = _sql(scoped(select(_Device), _Device, subject))

    assert "WHERE" not in sql


def test_unregistered_model_raises_instead_of_leaking() -> None:
    """A forgotten registration must fail loudly, not return every row."""
    with pytest.raises(UnscopedModelError, match="_Unregistered"):
        scoped(select(_Unregistered), _Unregistered, ScopeSubject.for_accounts([7]))


def test_scoping_preserves_existing_filters() -> None:
    stmt = select(_Device).where(_Device.id == 42)

    sql = _sql(scoped(stmt, _Device, ScopeSubject.for_accounts([7])))

    assert "devices.id =" in sql
    assert "devices.owner_id IN" in sql


def test_subject_is_hashable_and_normalises_ids() -> None:
    subject = ScopeSubject.for_accounts(["7", 7, 8])

    assert subject.account_ids == frozenset({7, 8})
    assert hash(subject) is not None
