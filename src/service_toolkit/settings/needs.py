"""Declaring what a service needs, in the service's own code.

A service knows it needs a PostgreSQL. It does not know — and must not have to
know — which row in some registry answers that, which machine the answer runs on,
or what address it currently has. Today that knowledge leaks into the service as
`POSTGRES_HOST=…` in a `.env` somebody maintains by hand, which is how a value
ends up naming a machine the database is not on.

So the service declares the need and reads the answer:

    settings = load_settings(
        Settings,
        requires=[
            needs("postgres", prefix="POSTGRES_"),
            needs("nats", prefix="NATS_"),
            needs("redis", prefix="REDIS_", optional=True),
            needs("nats", address="servers"),
        ],
    )

Not every dependency is a host and a port. NATS is given a list of URLs, MinIO
and a gRPC peer one "host:port" string — so a need says which shape it is, and
the check follows it to the variable that actually carries the address. Without
that, everything but the databases sat outside the mechanism.

**This does not require a control-plane.** A declaration is only a statement of
what the service reads; the values arrive as ordinary environment variables. Run
the same service under docker-compose, under systemd, or from a shell with a
hand-written `.env`, and it behaves identically — the operator fills
`POSTGRES_HOST` themselves and nothing here notices the difference. Under a
control-plane the same variables are computed and injected instead, and the
service still does not notice. That symmetry is the point: a declaration that
only worked under our own orchestrator would be a lock-in, not a contract.

What the declaration buys, in ascending order of how much infrastructure is
involved:

* on its own — a service that fails at startup naming the dependency it could
  not configure, instead of at first use with a connection error;
* with a control-plane — the values stop being written by hand at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


class MissingRequirement(RuntimeError):
    """A declared dependency has no configuration.

    Raised at startup rather than at first use. A service that cannot reach its
    database is not healthy, and finding that out when the first request arrives
    means the error surfaces far from its cause — usually as a timeout in
    somebody else's log.
    """


#: How a dependency's address is written down.
#:
#: * ``host_port`` — separate ``HOST`` and ``PORT`` (a database, a cache)
#: * ``endpoint``  — one ``ENDPOINT`` holding "host:port" (MinIO)
#: * ``target``    — one ``TARGET`` holding "host:port" (a gRPC peer)
#: * ``servers``   — one ``SERVERS`` holding a URL list (NATS)
#:
#: ``endpoint`` and ``target`` differ only in the variable they read. Both
#: exist because the fleet already uses both spellings, and a mechanism that
#: renamed live variables to suit itself would be a migration, not a
#: declaration.
AddressShape = Literal["host_port", "endpoint", "target", "servers"]

#: The variable each shape reads its address from.
_ADDRESS_KEYS: dict[str, str] = {
    "host_port": "HOST",
    "endpoint": "ENDPOINT",
    "target": "TARGET",
    "servers": "SERVERS",
}


@dataclass(frozen=True, slots=True)
class Need:
    """One declared dependency.

    ``capability`` says what kind of thing this is ("postgres", "nats"). Under a
    control-plane it is what gets matched against what other services offer;
    standalone it is simply the name in the error message, which is still worth
    having — "postgres is not configured" beats "None is not a valid host".

    ``prefix`` is where the values are read from, and defaults from the
    capability: ``postgres`` → ``POSTGRES_``. Explicit when one service needs
    two of a kind, since ``POSTGRES_`` and ``REPORTS_`` must not collide.

    ``optional`` marks a dependency the service works without. Without it,
    declaring a need would mean "refuse to start" for everything a service can
    merely take advantage of — a cache, a metrics sink — and nobody would
    declare those at all.
    """

    capability: str
    prefix: str = ""
    optional: bool = False
    #: How the address is written down. Not every dependency is a host/port
    #: pair: NATS is given a list of URLs, MinIO and a gRPC peer are given one
    #: "host:port" string. Declaring the shape is what lets those be checked at
    #: startup like everything else, instead of being left out of the mechanism.
    address: AddressShape = "host_port"
    #: Which of the provider's roles this wants: "" (primary) | "ro" | "<n>".
    #: Ignored standalone; meaningful once something resolves it.
    role: str = ""
    #: Extra constraints a resolver may honour (version, TLS, database name).
    #: Free-form on purpose: the service states what it requires, and what a
    #: given control-plane can enforce is that control-plane's business.
    constraints: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.capability.strip():
            msg = "a need must name a capability"
            raise ValueError(msg)

    @property
    def env_prefix(self) -> str:
        """Prefix the values are read under, always ending in an underscore."""
        raw = self.prefix or self.capability
        cleaned = raw.strip().rstrip("_").upper()
        for char in "-./":
            cleaned = cleaned.replace(char, "_")
        return f"{cleaned}_"

    @property
    def host_key(self) -> str:
        """The variable that must be set for this need to be configured.

        Whichever one carries the address: ``HOST`` for a host/port pair,
        ``ENDPOINT`` for a single "host:port", ``SERVERS`` for a URL list.
        """
        return f"{self.env_prefix}{_ADDRESS_KEYS[self.address]}"

    @property
    def address_keys(self) -> tuple[str, ...]:
        """Every variable this need reads its address from, address first.

        A host/port pair also reads a PORT; the other shapes carry the port
        inside the one value, so there is nothing else to read.
        """
        if self.address == "host_port":
            return (self.host_key, f"{self.env_prefix}PORT")
        return (self.host_key,)


#: Near-misses of this function's own keywords. A constraint by one of these
#: names is far more likely a typo than a deliberate constraint.
_KEYWORD_NAMES = frozenset(
    {
        "adress",
        "adresses",
        "addres",
        "addresses",
        "capabilities",
        "capabilty",
        "optionnal",
        "optionl",
        "prefixes",
        "prefx",
        "roles",
    }
)


def needs(
    capability: str,
    *,
    prefix: str = "",
    optional: bool = False,
    role: str = "",
    address: AddressShape = "host_port",
    **constraints: str,
) -> Need:
    """Declare a dependency. See the module docstring for what it does and does
    not imply.

    ``address`` says how the address is written down — ``"host_port"`` (the
    default), ``"endpoint"`` for one "host:port" string, or ``"servers"`` for a
    URL list. It changes which variable is checked, nothing else.
    """
    if address not in _ADDRESS_KEYS:
        known = ", ".join(sorted(_ADDRESS_KEYS))
        msg = f"unknown address shape {address!r}; expected one of: {known}"
        raise ValueError(msg)
    # `**constraints` is free-form on purpose, which makes it a trap: a typo in
    # a real keyword (`adress=`, `optionnal=`) would land there silently and the
    # need would be checked against the wrong variable. Reject the near-misses.
    mistaken = sorted(constraints.keys() & _KEYWORD_NAMES)
    if mistaken:
        msg = f"misspelled keyword(s) passed as constraints: {', '.join(mistaken)}"
        raise ValueError(msg)
    return Need(
        capability=capability,
        prefix=prefix,
        optional=optional,
        role=role,
        address=address,
        constraints=dict(constraints),
    )


def prefixes_for(requires: list[Need], *, block_names: dict[str, str]) -> dict[str, str]:
    """Turn declarations into the ``prefixes`` mapping the loader wants.

    ``block_names`` maps a capability to the settings field that holds it, which
    the caller knows and this module cannot guess.
    """
    return {
        block_names[need.capability]: need.env_prefix
        for need in requires
        if need.capability in block_names
    }


def check_configured(
    requires: list[Need],
    env: dict[str, str],
) -> list[Need]:
    """Return the non-optional needs whose address is not configured.

    Checks the address variable only — whichever one the need's shape uses. A
    port usually has a sensible default and a password may legitimately be
    empty, but an address cannot be guessed: if nothing said where the
    dependency is, nothing else will make the connection work.
    """
    return [
        need
        for need in requires
        if not need.optional and not (env.get(need.host_key) or "").strip()
    ]


def require_configured(requires: list[Need], env: dict[str, str]) -> None:
    """Fail startup when a required dependency is unconfigured.

    The message names every missing one at once and says which variable would
    satisfy it: discovering these one restart at a time is a bad way to spend an
    evening.
    """
    missing = check_configured(requires, env)
    if not missing:
        return
    detail = ", ".join(f"{n.capability} (set {n.host_key})" for n in missing)
    msg = f"unconfigured dependencies: {detail}"
    raise MissingRequirement(msg)


__all__ = [
    "AddressShape",
    "MissingRequirement",
    "Need",
    "check_configured",
    "needs",
    "prefixes_for",
    "require_configured",
]
