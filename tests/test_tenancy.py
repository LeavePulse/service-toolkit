"""Tests for row scoping."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import BigInteger, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from service_toolkit.security.tenancy import (
    ScopeSubject,
    UnscopedModelError,
    UnscopedQueryError,
    install_scope_guard,
    register_owner_scope,
    register_tenant_scope,
    scoped,
    unscoped,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


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


# ── guard: forgetting the call must fail, not leak ──────────────────────────


@pytest.fixture
def guarded_session() -> Iterator[Session]:
    """A session class guarded in isolation, so the hook cannot leak between tests."""

    class _Guarded(Session):
        pass

    engine = create_engine("sqlite://")
    _Base.metadata.create_all(engine)
    install_scope_guard(session_class=_Guarded)
    with _Guarded(bind=engine) as session:
        yield session
    _Base.metadata.drop_all(engine)


def test_guard_rejects_unscoped_query_on_registered_model(
    guarded_session: Session,
) -> None:
    with pytest.raises(UnscopedQueryError, match="_Device"):
        guarded_session.execute(select(_Device)).all()


def test_guard_allows_scoped_query(guarded_session: Session) -> None:
    stmt = scoped(select(_Device), _Device, ScopeSubject.for_accounts([7]))

    assert guarded_session.execute(stmt).all() == []


def test_guard_allows_operator_query(guarded_session: Session) -> None:
    """Operator reads are unfiltered but stamped, so they are not oversights."""
    subject = ScopeSubject.for_accounts([], is_platform_operator=True)
    stmt = scoped(select(_Device), _Device, subject)

    assert guarded_session.execute(stmt).all() == []


def test_guard_allows_explicit_waiver(guarded_session: Session) -> None:
    stmt = unscoped(select(_Device), reason="reconciler sweeps every host")

    assert guarded_session.execute(stmt).all() == []


def test_guard_ignores_unregistered_models(guarded_session: Session) -> None:
    """Only models that opted into scoping are guarded."""
    assert guarded_session.execute(select(_Unregistered)).all() == []


def test_guard_catches_registered_model_in_a_join(guarded_session: Session) -> None:
    """A scoped model reached through a join is still a leak if unscoped."""
    stmt = select(_Allocation).join(_Device, _Device.id == _Allocation.id)

    with pytest.raises(UnscopedQueryError):
        guarded_session.execute(stmt).all()


def test_guard_does_not_block_writes(guarded_session: Session) -> None:
    """Writes address rows the caller already resolved; guarding them would fire
    on every flush."""
    guarded_session.add(_Device(id=1, owner_kind="account", owner_id=7))
    guarded_session.flush()

    assert (
        guarded_session.execute(
            scoped(select(_Device), _Device, ScopeSubject.for_accounts([7]))
        )
        .scalars()
        .all()
        != []
    )


def test_install_scope_guard_is_idempotent() -> None:
    class _Once(Session):
        pass

    install_scope_guard(session_class=_Once)
    install_scope_guard(session_class=_Once)

    engine = create_engine("sqlite://")
    _Base.metadata.create_all(engine)
    with _Once(bind=engine) as session, pytest.raises(UnscopedQueryError):
        session.execute(select(_Device)).all()


def test_unscoped_requires_a_reason() -> None:
    with pytest.raises(ValueError, match="requires a reason"):
        unscoped(select(_Device), reason="  ")


def test_async_sessions_share_the_guarded_sync_class() -> None:
    """Why listening on the sync Session is enough: AsyncSession delegates to it.

    Pinned by a test because the guard silently covering nothing in async code
    would look identical to it working.
    """
    from sqlalchemy.ext.asyncio import AsyncSession

    assert AsyncSession.sync_session_class is Session
