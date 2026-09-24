"""Materialise proto contracts that live in their own repositories.

A contract shared by several services (the AgentGateway API between the agent
and the control-plane, say) is kept in one repository and nowhere else. Each
consumer pins it in its own manifest and this module is the only thing that
turns that pin into files on disk. Code generation then reads the materialised
directory and never fetches anything itself.

A pin is four keys, in ``pyproject.toml`` or ``Cargo.toml``::

    [tool.service_toolkit.contracts.agent-contract]      # pyproject.toml
    [package.metadata.contracts.agent-contract]          # Cargo.toml
    repository = "https://github.com/LeavePulse/agent-contract.git"
    tag = "v1.0.0"
    commit = "<full 40-character commit>"
    proto_dir = "proto"

The commit is the pin and it is authoritative. The tag is the human label for
it and must name that commit when the contract is fetched, so a tag moved after
review cannot change what a service is built against. The result lands in
``.contracts/<name>/<commit>/`` beside the manifest with its git metadata, and
every later sync verifies it against the pin before reusing it: HEAD must be the
pinned commit and the tree must match that commit exactly, no file changed and
none added. A copy that fails is discarded and fetched again. A verified copy is
reused without contacting the repository, so a tag moved after the first fetch
is noticed on the next fresh fetch (a new machine, a CI run), not on reuse.

Only repositories under the approved GitHub organisation are fetched, over
HTTPS. CI rewrites GitHub URLs to carry its token, so an arbitrary repository
in a manifest would otherwise be fetched with that token.

``LP_GRPC_CONTRACT_<NAME>`` (the name upper-cased, other characters as
underscores) points a contract at a local checkout, for a contract change
developed together with its consumer before it is tagged. It is refused in CI,
where a build must use exactly what is pinned.

Run as ``lp-sync-contract [--manifest PATH]``: materialises every pinned
contract and prints ``<name> <directory>`` per line.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess  # nosec B404 - fetching a pinned contract is a git clone
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: The only repositories a pin may name: HTTPS, GitHub, the LeavePulse
#: organisation, one repository.
_APPROVED_REPOSITORY = re.compile(
    r"^https://github\.com/LeavePulse/[A-Za-z0-9][A-Za-z0-9._-]*\.git$"
)
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
#: Release labels only: vMAJOR.MINOR.PATCH. Anything else is not a tag this
#: mechanism fetches, and a leading dash could be read by git as an option.
_TAG = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
#: A relative path inside the contract repository, never leaving it.
_PROTO_DIR = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._/-]*$")


@dataclass(frozen=True, slots=True)
class ContractPin:
    """A contract repository pinned to one commit."""

    name: str
    repository: str
    tag: str
    commit: str
    proto_dir: str


class ContractError(SystemExit):
    """A pin or a fetch that cannot be trusted; the message says which."""


def override_variable(name: str) -> str:
    return "LP_GRPC_CONTRACT_" + re.sub(r"[^A-Za-z0-9]", "_", name).upper()


def _pin_tables(manifest: Path) -> dict[str, Any]:
    data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    if manifest.name == "Cargo.toml":
        section = data.get("package", {}).get("metadata", {}).get("contracts", {})
    else:
        section = data.get("tool", {}).get("service_toolkit", {}).get("contracts", {})
    if not isinstance(section, dict):
        msg = f"{manifest}: contracts must be a table of pins."
        raise ContractError(msg)
    return section


def read_pins(manifest: Path) -> dict[str, ContractPin]:
    """Every contract pinned in *manifest*, validated."""
    pins: dict[str, ContractPin] = {}
    for name, raw in _pin_tables(manifest).items():
        if not isinstance(raw, dict):
            msg = f"{manifest}: contract {name} must be a table."
            raise ContractError(msg)
        pins[name] = _validated(manifest, name, raw)
    return pins


def _validated(manifest: Path, name: str, raw: dict[str, Any]) -> ContractPin:
    def value(key: str) -> str:
        found = raw.get(key)
        if not isinstance(found, str) or not found.strip():
            msg = f"{manifest}: contract {name} is missing {key}."
            raise ContractError(msg)
        return found.strip()

    pin = ContractPin(
        name=name,
        repository=value("repository"),
        tag=value("tag"),
        commit=value("commit"),
        proto_dir=value("proto_dir"),
    )
    problems = []
    if not _NAME.fullmatch(pin.name):
        problems.append(
            f"name {pin.name!r} is not lower-case letters, digits and dashes"
        )
    if not _APPROVED_REPOSITORY.fullmatch(pin.repository):
        problems.append(
            f"repository {pin.repository!r} is not an HTTPS repository of the "
            "LeavePulse GitHub organisation"
        )
    if not _TAG.fullmatch(pin.tag):
        problems.append(f"tag {pin.tag!r} is not a vMAJOR.MINOR.PATCH release")
    if not _COMMIT.fullmatch(pin.commit):
        problems.append(
            f"commit {pin.commit!r} is not a full 40-character sha; a short or "
            "symbolic ref is not a pin"
        )
    if not _PROTO_DIR.fullmatch(pin.proto_dir) or ".." in Path(pin.proto_dir).parts:
        problems.append(
            f"proto_dir {pin.proto_dir!r} is not a path inside the repository"
        )
    if problems:
        msg = f"{manifest}: contract {name}: " + "; ".join(problems)
        raise ContractError(msg)
    return pin


def materialised_dir(pin: ContractPin, *, base_dir: Path) -> Path:
    """Where *pin* is on disk: the override checkout, or its commit's directory."""
    override = os.environ.get(override_variable(pin.name))
    if override:
        if os.environ.get("CI"):
            msg = (
                f"{override_variable(pin.name)} is set in CI. A CI build uses "
                f"exactly the pinned {pin.name} commit; unset it."
            )
            raise ContractError(msg)
        return Path(override).resolve()
    return base_dir / ".contracts" / pin.name / pin.commit


def proto_root(pin: ContractPin, *, base_dir: Path) -> Path:
    """The proto directory of an already materialised *pin*."""
    root = materialised_dir(pin, base_dir=base_dir) / pin.proto_dir
    if not root.is_dir():
        msg = (
            f"contract {pin.name} {pin.tag} ({pin.commit[:12]}) is not on disk at "
            f"{root}; run lp-sync-contract first."
        )
        raise ContractError(msg)
    return root


def _clone_source(repository: str) -> str:
    """The URL git clones *repository* from. A seam for tests only."""
    return repository


def _git(*args: str, cwd: Path | None = None) -> str:
    # Fixed argv, no shell: every value has been validated above, and the
    # "--" before positional arguments keeps any of them from reading as an
    # option. git comes from PATH, as every developer and runner has it.
    result = subprocess.run(  # noqa: S603, S607  # nosec B603 B607
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        msg = f"git {args[0]} failed: {result.stderr.strip()}"
        raise ContractError(msg)
    return result.stdout.strip()


def materialise(pin: ContractPin, *, base_dir: Path) -> Path:
    """Put *pin* on disk if it is not there yet, and return its directory."""
    destination = materialised_dir(pin, base_dir=base_dir)
    if os.environ.get(override_variable(pin.name)):
        print(  # noqa: archlint=print
            f"contract {pin.name}: using local checkout {destination} instead of "
            f"{pin.tag} ({pin.commit[:12]})"
        )
        return destination
    if destination.exists():
        if _is_intact(destination, pin):
            return destination
        print(  # noqa: archlint=print
            f"contract {pin.name}: {destination} is not the pinned commit "
            f"{pin.commit[:12]} as fetched; discarding it and fetching again"
        )
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as scratch:
        checkout = Path(scratch) / "checkout"
        _git(
            "-c",
            "advice.detachedHead=false",
            "clone",
            "--quiet",
            "--depth",
            "1",
            "--branch",
            pin.tag,
            "--",
            _clone_source(pin.repository),
            str(checkout),
        )
        resolved = _git("rev-parse", "HEAD", cwd=checkout)
        if resolved != pin.commit:
            msg = (
                f"contract {pin.name}: tag {pin.tag} names {resolved}, but the pin "
                f"is {pin.commit}. A tag that moved is not the contract this "
                "consumer was built against; pin the new commit deliberately or "
                "restore the tag."
            )
            raise ContractError(msg)
        # The git metadata stays: it is what lets a later sync prove this copy
        # is still exactly the pinned commit.
        checkout.rename(destination)
    return destination


def _is_intact(directory: Path, pin: ContractPin) -> bool:
    """Whether *directory* is exactly the pinned commit, as git itself hashes it.

    HEAD has to be the pinned commit and the working tree has to match it: no
    tracked file changed, none deleted, and nothing untracked, since codegen
    compiles every proto it finds.
    """
    if not (directory / ".git").is_dir():
        return False
    try:
        head = _git("rev-parse", "HEAD", cwd=directory)
        changes = _git("status", "--porcelain", "--untracked-files=all", cwd=directory)
    except ContractError:
        return False
    return head == pin.commit and not changes


def _default_manifest() -> Path:
    for candidate in ("pyproject.toml", "Cargo.toml"):
        path = Path(candidate)
        if path.is_file():
            return path
    msg = "no pyproject.toml or Cargo.toml here; pass --manifest."
    raise ContractError(msg)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Materialise the proto contracts pinned in a manifest.",
    )
    parser.add_argument("--manifest", type=Path, help="pyproject.toml or Cargo.toml")
    args = parser.parse_args()
    manifest = (args.manifest or _default_manifest()).resolve()
    # Nothing pinned is nothing to do, so the same CI step serves services that
    # generate only from their own protos.
    for pin in read_pins(manifest).values():
        directory = materialise(pin, base_dir=manifest.parent)
        print(f"{pin.name} {directory}")  # noqa: archlint=print


if __name__ == "__main__":
    main()
