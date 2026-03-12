from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from service_toolkit.observability.prometheus_runtime import (
    DEFAULT_MULTIPROC_DIRECTORY,
    prepare_multiprocess_directory,
    resolve_multiprocess_directory,
)


def test_prepare_multiprocess_directory_uses_default_and_cleans_files(
    tmp_path: Path,
) -> None:
    env: dict[str, str] = {}
    stale = tmp_path / "stale.db"
    stale.write_text("old")

    target = prepare_multiprocess_directory(
        default_directory=tmp_path,
        env=env,
    )

    assert target == tmp_path
    assert env["PROMETHEUS_MULTIPROC_DIR"] == str(tmp_path)
    assert env["prometheus_multiproc_dir"] == str(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_resolve_multiprocess_directory_prefers_explicit_env(tmp_path: Path) -> None:
    env = {"PROMETHEUS_MULTIPROC_DIR": str(tmp_path)}

    resolved = resolve_multiprocess_directory(env=env)

    assert resolved == tmp_path


def test_prometheus_runtime_exec_sets_env_and_cleans_directory(tmp_path: Path) -> None:
    stale = tmp_path / "stale.db"
    stale.write_text("old")
    env = os.environ.copy()
    python_path = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        "src" if not python_path else f"src{os.pathsep}{python_path}"
    )
    command = [
        sys.executable,
        "-m",
        "service_toolkit.observability.prometheus_runtime",
        "--default-directory",
        str(tmp_path),
        "--",
        sys.executable,
        "-c",
        (
            "import json, os, pathlib; "
            "path = pathlib.Path(os.environ['PROMETHEUS_MULTIPROC_DIR']); "
            "print(json.dumps({'dir': str(path), 'files': sorted(p.name for p in path.iterdir())}))"
        ),
    ]

    result = subprocess.run(
        command,
        cwd=Path(__file__).resolve().parent.parent,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout.strip())
    assert payload == {"dir": str(tmp_path), "files": []}


def test_default_directory_constant_is_stable() -> None:
    assert DEFAULT_MULTIPROC_DIRECTORY == "/tmp/metrics"
