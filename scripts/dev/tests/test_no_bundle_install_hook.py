"""Tests for the Claude Code hook that blocks bundle installs into this checkout (#2731)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[3] / ".claude" / "hooks" / "no_bundle_install.py"
_spec = importlib.util.spec_from_file_location("no_bundle_install", HOOK)
assert _spec and _spec.loader
hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hook)


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    (tmp_path / "sidecars" / "pb").mkdir(parents=True)
    (tmp_path / "sidecars" / "pb" / "bundle.json").write_text('{"name": "pb", "version": "1"}')
    (tmp_path / "jobs").mkdir()
    return tmp_path


@pytest.mark.parametrize(
    "command",
    [
        "uqs job install sidecars/pb --yes",
        "uv run uqs job install sidecars/pb",
        "unset UQF_PEACHQ && uqs job install sidecars/pb --copy --yes",
        "uqs --runtime crypto runtime prepare",
        "uv run uqs --runtime peachq-etl runtime prepare --bundle sidecars/pb",
        ".venv/bin/uqs runtime prepare",
        "uv run python -m uqs job install sidecars/pb --yes",
    ],
)
def test_an_install_into_the_checkout_is_refused(tree: Path, command: str) -> None:
    assert hook.refusal(command, tree) is not None


@pytest.mark.parametrize(
    "command",
    [
        "uqs job install sidecars/pb --dry-run",
        "uqs --runtime crypto runtime prepare -n",
        "uqs job install jobs --copy --yes",  # not a bundle: a job added to the tree
        "uqs runtime list",
        "uqs runtime diff uqf torq",
        "grep -rn 'runtime prepare' docs",
        "uv run python scripts/dev/bundle_suite.py sidecars/pb",
        "uqs deploy build --bundle sidecars/pb",
    ],
)
def test_everything_else_is_allowed(tree: Path, command: str) -> None:
    assert hook.refusal(command, tree) is None


def test_an_instance_may_install(tree: Path) -> None:
    (tree / hook.INSTANCE_MARKER).touch()
    assert hook.refusal("uqs runtime prepare", tree) is None


def test_the_hook_blocks_with_exit_2_and_says_why(tree: Path) -> None:
    event = {"tool_input": {"command": "uqs runtime prepare"}, "cwd": str(tree)}
    run = subprocess.run(
        [sys.executable, str(HOOK)], input=json.dumps(event), capture_output=True, text=True
    )
    assert run.returncode == 2
    assert "bundle_suite.py" in run.stderr


def test_the_hook_passes_an_unrelated_command(tree: Path) -> None:
    event = {"tool_input": {"command": "ls"}, "cwd": str(tree)}
    run = subprocess.run(
        [sys.executable, str(HOOK)], input=json.dumps(event), capture_output=True, text=True
    )
    assert run.returncode == 0, run.stderr
