"""A test's scratch `git init` never reaches the repository being committed
to (#996): git's hook-exported location variables are gone before any test
runs (conftest.py at the repository root)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def test_no_git_location_variable_survives_into_the_tests():
    leaked = [v for v in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE") if v in os.environ]
    assert not leaked, f"exported to the tests: {leaked}"


def test_a_scratch_git_init_leaves_this_repositorys_config_alone(tmp_path):
    def bare() -> str:
        return subprocess.run(
            ["git", "-C", str(REPO), "config", "--get", "core.bare"],
            capture_output=True, text=True, check=False,
        ).stdout.strip()  # fmt: skip

    before = bare()
    subprocess.run(["git", "init", "-q", str(tmp_path / "scratch")], check=True)
    assert (tmp_path / "scratch" / ".git").is_dir(), "the scratch repository was created"
    assert bare() == before
