"""Test-session setup shared by every test module.

The suite runs from the pre-push hook (`.pre-commit-config.yaml`,
`lp-ci-tests`), and git runs a hook with its repository-local environment set:
GIT_DIR, GIT_INDEX_FILE and the rest. A test that drives git in a temporary
repository inherits them, so its `git init`, `git add` and `git commit` act on
the repository being pushed instead: `init` re-initialises it as bare and the
index gains staged deletions. git itself names the variables that tie a
process to one repository (`git rev-parse --local-env-vars`); the session
drops exactly those before any test runs.
"""

from __future__ import annotations

import os
import subprocess


def _drop_repository_environment() -> None:
    names = subprocess.run(
        ["git", "rev-parse", "--local-env-vars"],
        check=True,
        capture_output=True,
        text=True,
        env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    ).stdout.split()
    for name in names:
        os.environ.pop(name, None)


_drop_repository_environment()
