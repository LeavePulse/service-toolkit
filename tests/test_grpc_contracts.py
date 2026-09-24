"""Pinned proto contracts: one resolver materialises them, codegen only reads."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from service_toolkit.grpc import codegen, contracts

REPOSITORY = "https://github.com/LeavePulse/agent-contract.git"

_AGENT = """syntax = "proto3";
package leavepulse.control.v1;
message Command { string command_id = 1; }
"""

_INVENTORY = """syntax = "proto3";
package leavepulse.control.v1;
import "leavepulse/control/v1/agent.proto";
message Inventory { Command last = 1; }
"""


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo: Path, message: str) -> None:
    _git("add", ".", cwd=repo)
    _git(
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.com",
        "commit",
        "-q",
        "-m",
        message,
        cwd=repo,
    )


@pytest.fixture
def contract_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    """A local repository standing in for agent-contract, tagged v1.0.0.

    The manifest still names the approved GitHub URL; only the clone source is
    redirected, so repository validation runs on the real value.
    """
    monkeypatch.delenv("CI", raising=False)
    repo = tmp_path / "agent-contract"
    proto = repo / "proto" / "leavepulse" / "control" / "v1"
    proto.mkdir(parents=True)
    (proto / "agent.proto").write_text(_AGENT)
    _git("init", "-q", "-b", "main", cwd=repo)
    _commit(repo, "contract")
    _git("tag", "v1.0.0", cwd=repo)
    monkeypatch.setattr(
        contracts,
        "_clone_source",
        lambda repository: str(repo) if repository == REPOSITORY else repository,
    )
    return repo, _git("rev-parse", "HEAD", cwd=repo)


def _manifest(
    tmp_path: Path,
    commit: str,
    *,
    repository: str = REPOSITORY,
    tag: str = "v1.0.0",
    cargo: bool = False,
) -> Path:
    consumer = tmp_path / "consumer"
    proto = consumer / "proto" / "leavepulse" / "control" / "v1"
    proto.mkdir(parents=True, exist_ok=True)
    (proto / "inventory.proto").write_text(_INVENTORY)
    pin = f"""
repository = "{repository}"
tag = "{tag}"
commit = "{commit}"
proto_dir = "proto"
"""
    if cargo:
        manifest = consumer / "Cargo.toml"
        manifest.write_text(
            '[package]\nname = "x"\nversion = "0.1.0"\n\n'
            "[package.metadata.contracts.agent-contract]" + pin
        )
        return manifest
    manifest = consumer / "pyproject.toml"
    manifest.write_text(
        """
[[tool.service_toolkit.grpc_proto_codegen.targets]]
proto_dir = "proto"
out_dir = "generated"
import_prefix = "consumer.generated.leavepulse"
contracts = ["agent-contract"]

[tool.service_toolkit.contracts.agent-contract]"""
        + pin
    )
    return manifest


def _materialise(manifest: Path) -> Path:
    [pin] = contracts.read_pins(manifest).values()
    return contracts.materialise(pin, base_dir=manifest.parent)


# ── the resolver ──────────────────────────────────────────────────────────────


def test_a_pin_is_materialised_at_its_commit(tmp_path: Path, contract_repo) -> None:
    _, commit = contract_repo
    manifest = _manifest(tmp_path, commit)

    directory = _materialise(manifest)

    assert directory == manifest.parent / ".contracts" / "agent-contract" / commit
    assert (directory / "proto/leavepulse/control/v1/agent.proto").read_text() == _AGENT


_AGENT_IN_CACHE = "proto/leavepulse/control/v1/agent.proto"


def test_a_verified_copy_is_reused_without_the_repository(
    tmp_path: Path, contract_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The commit pin is authoritative: a copy proven to be it needs no fetch."""
    _, commit = contract_repo
    manifest = _manifest(tmp_path, commit)
    directory = _materialise(manifest)
    monkeypatch.setattr(contracts, "_clone_source", lambda _: "/nonexistent/repo")

    assert _materialise(manifest) == directory


@pytest.mark.parametrize(
    "corrupt",
    [
        pytest.param(
            lambda d: (d / _AGENT_IN_CACHE).write_text(_AGENT.replace("= 1", "= 9")),
            id="a file changed",
        ),
        pytest.param(
            lambda d: (d / "proto/leavepulse/control/v1/extra.proto").write_text(
                _AGENT
            ),
            id="a file added",
        ),
        pytest.param(lambda d: (d / _AGENT_IN_CACHE).unlink(), id="a file deleted"),
        pytest.param(
            lambda d: shutil.rmtree(d / ".git"),
            id="no git metadata",
        ),
    ],
)
def test_a_copy_that_is_not_the_pinned_commit_is_fetched_again(
    tmp_path: Path, contract_repo, corrupt
) -> None:
    """A cached directory is never trusted for existing: what codegen compiles
    has to be the pinned commit, file for file."""
    _, commit = contract_repo
    manifest = _manifest(tmp_path, commit)
    directory = _materialise(manifest)
    corrupt(directory)

    assert _materialise(manifest) == directory
    assert (directory / _AGENT_IN_CACHE).read_text() == _AGENT
    assert not (directory / "proto/leavepulse/control/v1/extra.proto").exists()
    # Fetched again, not repaired in place: the copy can be verified once more.
    assert _git("rev-parse", "HEAD", cwd=directory) == commit


def test_a_copy_of_another_commit_under_the_pinned_name_is_fetched_again(
    tmp_path: Path, contract_repo
) -> None:
    repo, commit = contract_repo
    manifest = _manifest(tmp_path, commit)
    directory = _materialise(manifest)
    _git(
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.com",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "elsewhere",
        cwd=directory,
    )

    assert _materialise(manifest) == directory
    assert _git("rev-parse", "HEAD", cwd=directory) == commit


def test_a_cargo_manifest_pins_the_same_way(tmp_path: Path, contract_repo) -> None:
    _, commit = contract_repo
    manifest = _manifest(tmp_path, commit, cargo=True)

    assert _materialise(manifest).name == commit


def test_a_tag_that_moved_is_refused(tmp_path: Path, contract_repo) -> None:
    """The commit is the pin; a tag naming something else is another contract."""
    repo, commit = contract_repo
    (repo / "proto/leavepulse/control/v1/agent.proto").write_text(
        _AGENT.replace("command_id = 1", "command_id = 2")
    )
    _commit(repo, "moved")
    _git("tag", "-f", "v1.0.0", cwd=repo)
    manifest = _manifest(tmp_path, commit)

    with pytest.raises(contracts.ContractError, match="tag v1.0.0 names"):
        _materialise(manifest)
    assert not (manifest.parent / ".contracts" / "agent-contract" / commit).exists()


@pytest.mark.parametrize(
    "repository",
    [
        "https://github.com/someone-else/agent-contract.git",
        "http://github.com/LeavePulse/agent-contract.git",
        "git@github.com:LeavePulse/agent-contract.git",
        "https://github.com.evil.example/LeavePulse/agent-contract.git",
        "https://github.com/LeavePulse/../other.git",
        "/srv/any/local/repo",
    ],
)
def test_only_the_approved_organisation_over_https_is_fetched(
    tmp_path: Path, repository: str
) -> None:
    """CI rewrites GitHub URLs to carry its token; a manifest must not be able
    to point that at a repository of its choosing."""
    manifest = _manifest(tmp_path, "a" * 40, repository=repository)

    with pytest.raises(contracts.ContractError, match="LeavePulse GitHub organisation"):
        contracts.read_pins(manifest)


@pytest.mark.parametrize("tag", ["main", "-u", "v1.0", "v1.0.0; rm -rf /", "HEAD"])
def test_a_malformed_tag_is_refused(tmp_path: Path, tag: str) -> None:
    manifest = _manifest(tmp_path, "a" * 40, tag=tag)

    with pytest.raises(contracts.ContractError, match="vMAJOR.MINOR.PATCH"):
        contracts.read_pins(manifest)


@pytest.mark.parametrize("commit", ["a" * 12, "HEAD", "v1.0.0", "A" * 40, "g" * 40])
def test_a_commit_that_is_not_a_full_sha_is_refused(
    tmp_path: Path, commit: str
) -> None:
    manifest = _manifest(tmp_path, commit)

    with pytest.raises(contracts.ContractError, match="full 40-character sha"):
        contracts.read_pins(manifest)


def test_a_local_checkout_overrides_the_pin_outside_ci(
    tmp_path: Path, contract_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, commit = contract_repo
    manifest = _manifest(tmp_path, commit)
    monkeypatch.setenv("LP_GRPC_CONTRACT_AGENT_CONTRACT", str(repo))

    assert _materialise(manifest) == repo.resolve()
    assert not (manifest.parent / ".contracts").exists()


def test_ci_refuses_an_override(
    tmp_path: Path, contract_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A CI build uses exactly the pinned commit, whatever the environment says."""
    repo, commit = contract_repo
    manifest = _manifest(tmp_path, commit)
    monkeypatch.setenv("LP_GRPC_CONTRACT_AGENT_CONTRACT", str(repo))
    monkeypatch.setenv("CI", "true")

    with pytest.raises(contracts.ContractError, match="set in CI"):
        _materialise(manifest)


# ── generation reads, never fetches ───────────────────────────────────────────


def test_generation_refuses_a_contract_that_is_not_on_disk(
    tmp_path: Path, contract_repo
) -> None:
    _, commit = contract_repo
    manifest = _manifest(tmp_path, commit)

    with pytest.raises(SystemExit, match="run lp-sync-contract"):
        codegen._targets_from_config(manifest)
    assert not (manifest.parent / ".contracts").exists()


def test_a_target_naming_an_undeclared_contract_is_refused(
    tmp_path: Path, contract_repo
) -> None:
    _, commit = contract_repo
    manifest = _manifest(tmp_path, commit)
    _materialise(manifest)
    manifest.write_text(manifest.read_text().replace('["agent-contract"]', '["other"]'))

    with pytest.raises(SystemExit, match="undeclared contracts: other"):
        codegen._targets_from_config(manifest)


def test_the_contract_is_generated_beside_the_protos_that_import_it(
    tmp_path: Path, contract_repo
) -> None:
    """inventory.proto imports agent.proto from the contract; both land in one
    package with one import prefix, as they did when both were local copies."""
    pytest.importorskip("grpc_tools")
    _, commit = contract_repo
    manifest = _manifest(tmp_path, commit)
    _materialise(manifest)
    [target] = codegen._targets_from_config(manifest)

    codegen._generate_target(target)

    out = manifest.parent / "generated" / "leavepulse" / "control" / "v1"
    assert (out / "agent_pb2.py").exists()
    inventory = (out / "inventory_pb2.py").read_text()
    assert "from consumer.generated.leavepulse.control.v1 import agent_pb2" in inventory


def test_a_local_copy_beside_the_contract_does_not_generate(
    tmp_path: Path, contract_repo
) -> None:
    """A consumer that kept its own agent.proto has two sources of one file;
    protoc refuses it rather than picking one."""
    pytest.importorskip("grpc_tools")
    _, commit = contract_repo
    manifest = _manifest(tmp_path, commit)
    _materialise(manifest)
    (manifest.parent / "proto/leavepulse/control/v1/agent.proto").write_text(_AGENT)
    [target] = codegen._targets_from_config(manifest)

    with pytest.raises(SystemExit):
        codegen._generate_target(target)
