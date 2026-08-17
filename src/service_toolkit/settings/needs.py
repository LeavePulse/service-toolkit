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
        ],
    )

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


class MissingRequirement(RuntimeError):
    """A declared dependency has no configuration.

    Raised at startup rather than at first use. A service that cannot reach its
    database is not healthy, and finding that out when the first request arrives
    means the error surfaces far from its cause — usually as a timeout in
    somebody else's log.
    """


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
        return f"{self.env_prefix}HOST"


def needs(
    capability: str,
    *,
    prefix: str = "",
    optional: bool = False,
    role: str = "",
    **constraints: str,
) -> Need:
    """Declare a dependency. See the module docstring for what it does and does
    not imply."""
    return Need(
        capability=capability,
        prefix=prefix,
        optional=optional,
        role=role,
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
    """Return the non-optional needs that have no host configured.

    Checks the HOST key only. A port usually has a sensible default and a
    password may legitimately be empty, but an address cannot be guessed — if
    nothing said where the dependency is, nothing else will make the connection
    work.
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
    "MissingRequirement",
    "Need",
    "check_configured",
    "needs",
    "prefixes_for",
    "require_configured",
]
