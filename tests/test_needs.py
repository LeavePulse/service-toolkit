"""Declaring a dependency, with or without anything answering it.

The property that matters most here is the one that is easiest to lose: a
declaration must not require our control-plane. The same service has to run
under docker-compose with a hand-written `.env`, and under an orchestrator that
computes the same variables, without noticing the difference. A declaration that
only worked under one of them would be lock-in wearing a contract's clothes.
"""

from __future__ import annotations

import msgspec
import pytest
from msgspec_conf import BaseSettings, load_composed_settings

from service_toolkit.settings.needs import (
    MissingRequirement,
    check_configured,
    needs,
    prefixes_for,
    require_configured,
)


def test_the_prefix_defaults_from_the_capability() -> None:
    assert needs("postgres").env_prefix == "POSTGRES_"
    assert needs("postgres").host_key == "POSTGRES_HOST"


def test_an_explicit_prefix_lets_two_of_a_kind_coexist() -> None:
    """A primary and an analytics replica must not read each other's values."""
    assert needs("postgres").env_prefix == "POSTGRES_"
    assert needs("postgres", prefix="REPORTS").env_prefix == "REPORTS_"


def test_a_capability_that_is_not_a_variable_name_is_normalised() -> None:
    assert needs("click-house").env_prefix == "CLICK_HOUSE_"
    assert needs("s3.storage").env_prefix == "S3_STORAGE_"


def test_a_trailing_underscore_is_not_doubled() -> None:
    assert needs("postgres", prefix="POSTGRES_").env_prefix == "POSTGRES_"


def test_a_need_must_name_something() -> None:
    with pytest.raises(ValueError):
        needs("   ")


# ── failing at startup, not at first use ─────────────────────────────────────


def test_an_unconfigured_requirement_fails_at_startup() -> None:
    """A service that cannot reach its database is not healthy. Finding out at
    first use puts the error far from its cause — usually a timeout in somebody
    else's log."""
    with pytest.raises(MissingRequirement) as exc:
        require_configured([needs("postgres")], {})

    # The message says which variable would fix it: discovering these one
    # restart at a time is a bad way to spend an evening.
    assert "postgres" in str(exc.value)
    assert "POSTGRES_HOST" in str(exc.value)


def test_every_missing_dependency_is_named_at_once() -> None:
    with pytest.raises(MissingRequirement) as exc:
        require_configured([needs("postgres"), needs("nats")], {})

    assert "postgres" in str(exc.value)
    assert "nats" in str(exc.value)


def test_an_optional_dependency_may_be_absent() -> None:
    """Without this, declaring a need would mean "refuse to start" for anything
    a service merely takes advantage of — and nobody would declare those."""
    require_configured([needs("redis", optional=True)], {})


def test_only_the_host_is_required() -> None:
    """A port usually has a default and a password may legitimately be empty;
    an address cannot be guessed."""
    require_configured([needs("postgres")], {"POSTGRES_HOST": "db"})

    assert check_configured([needs("postgres")], {"POSTGRES_HOST": "db"}) == []


def test_a_blank_value_counts_as_unset() -> None:
    """An empty variable is how a half-written `.env` looks, and it fails the
    same way a missing one does."""
    assert check_configured([needs("postgres")], {"POSTGRES_HOST": "  "})


# ── the same declaration, with and without a control-plane ───────────────────


class _Db(BaseSettings):
    host: str = "localhost"
    port: int = 5432


class _Settings(msgspec.Struct, kw_only=True):
    database: _Db = msgspec.field(default_factory=_Db)


def _load(env: dict[str, str]) -> _Settings:
    requires = [needs("postgres")]
    require_configured(requires, env)
    return load_composed_settings(
        _Settings,
        env=env,
        prefixes=prefixes_for(requires, block_names={"postgres": "database"}),
    )


def test_a_hand_written_env_satisfies_a_declaration() -> None:
    """No control-plane involved: an operator filled these in by hand, and the
    service cannot tell."""
    settings = _load({"POSTGRES_HOST": "db.example.com", "POSTGRES_PORT": "6432"})

    assert settings.database.host == "db.example.com"
    assert settings.database.port == 6432


def test_the_service_cannot_tell_where_the_values_came_from() -> None:
    """The symmetry is the point. A hand-written `.env` names a machine; an
    orchestrator injects an internal DNS name it computed from placement. Both
    are just strings by the time the service reads them, so the same code has to
    accept either without a branch."""
    by_hand = _load({"POSTGRES_HOST": "10.200.0.102", "POSTGRES_PORT": "5549"})
    injected = _load({"POSTGRES_HOST": "auth-db.lp.internal", "POSTGRES_PORT": "5549"})

    assert by_hand.database.host == "10.200.0.102"
    assert injected.database.host == "auth-db.lp.internal"
    assert by_hand.database.port == injected.database.port


def test_defaults_survive_a_partially_filled_env() -> None:
    """Only the host was given; the port keeps the service's own default rather
    than becoming an error."""
    settings = _load({"POSTGRES_HOST": "db"})

    assert settings.database.host == "db"
    assert settings.database.port == 5432


def test_a_capability_with_no_matching_block_is_simply_not_wired() -> None:
    """A service may declare a need it reads itself — a queue it opens by hand,
    say — without a settings block for it. That must not break composition."""
    mapping = prefixes_for(
        [needs("postgres"), needs("nats")], block_names={"postgres": "database"}
    )

    assert mapping == {"database": "POSTGRES_"}


def test_constraints_are_carried_without_being_interpreted() -> None:
    """The service states what it requires; what a given control-plane can
    enforce is that control-plane's business."""
    need = needs("postgres", version=">=16", tls="required")

    assert need.constraints == {"version": ">=16", "tls": "required"}

# ── dependencies that are not a host/port pair ───────────────────────────────


def test_a_url_list_is_checked_where_it_actually_lives() -> None:
    """NATS is configured as NATS_SERVERS, a list of URLs. Checking for a HOST
    that nothing sets would report every service as unconfigured."""
    need = needs("nats", address="servers")

    assert need.host_key == "NATS_SERVERS"
    require_configured([need], {"NATS_SERVERS": "nats://nats:4222"})


def test_a_single_endpoint_is_checked_as_one_value() -> None:
    """MinIO takes "host:port" in one variable; there is no MINIO_HOST."""
    need = needs("minio", address="endpoint")

    assert need.host_key == "MINIO_ENDPOINT"
    require_configured([need], {"MINIO_ENDPOINT": "minio:9000"})


def test_a_grpc_peer_keeps_the_spelling_the_fleet_already_uses() -> None:
    """The variable is AUTH_GRPC_TARGET. A mechanism that insisted on its own
    name would be a migration of every service, not a declaration."""
    need = needs("auth-service", prefix="AUTH_GRPC", address="target")

    assert need.host_key == "AUTH_GRPC_TARGET"


def test_an_unset_url_list_still_fails_at_startup() -> None:
    """The point of declaring is the failure, so it must survive the shape."""
    with pytest.raises(MissingRequirement) as exc:
        require_configured([needs("nats", address="servers")], {})

    assert "NATS_SERVERS" in str(exc.value)


def test_a_host_port_pair_names_both_of_its_variables() -> None:
    assert needs("postgres").address_keys == ("POSTGRES_HOST", "POSTGRES_PORT")


def test_a_one_value_shape_has_nothing_else_to_read() -> None:
    """The port lives inside the value, so there is no separate PORT."""
    assert needs("minio", address="endpoint").address_keys == ("MINIO_ENDPOINT",)


def test_the_default_shape_is_still_a_host_port_pair() -> None:
    """Every existing declaration keeps working untouched."""
    assert needs("postgres").address == "host_port"
    assert needs("postgres").host_key == "POSTGRES_HOST"


def test_an_unknown_address_shape_is_refused() -> None:
    """Silently keeping an unrecognised shape would check the wrong variable,
    and the need would read as configured when nothing is."""
    with pytest.raises(ValueError, match="unknown address shape"):
        needs("minio", address="endpont")  # type: ignore[arg-type]


def test_a_misspelled_keyword_does_not_become_a_constraint() -> None:
    """`**constraints` accepts anything, so a typo in a real keyword used to
    land there and change nothing — the quietest possible failure."""
    with pytest.raises(ValueError, match="misspelled keyword"):
        needs("minio", adress="endpoint")


def test_a_deliberate_constraint_still_passes_through() -> None:
    """Rejecting near-misses must not close the door on real constraints."""
    need = needs("postgres", version="16", tls="required")

    assert need.constraints == {"version": "16", "tls": "required"}
