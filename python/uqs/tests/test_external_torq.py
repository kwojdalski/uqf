"""An existing TorQ, starter pack and data directory in place of the vendored
ones (#773): TORQHOME, TORQAPPHOME and UQS_DATA_ROOT.

What a deployment relies on: the generated environment and the launcher both
use the configured trees, the data lives where UQS_DATA_ROOT says, unset
means the vendored defaults, and a value that is set but wrong is refused
rather than quietly replaced by the default.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from uqs import paths as stack_paths
from uqs.paths import UqsError
from uqs.stack import runtime
from uqs.stack.env import build_env


@pytest.fixture
def external(tmp_path, monkeypatch):
    """An external TorQ, starter pack and data root, named in the environment."""
    torq = tmp_path / "opt" / "torq"
    app = tmp_path / "opt" / "torq-finance-starter-pack"
    data = tmp_path / "srv" / "uqf-data"
    for d in (torq, app, data):
        d.mkdir(parents=True)
    (torq / "torq.q").write_text("")
    (torq / "torq.sh").write_text("#!/bin/sh\n")
    (app / "database.q").write_text("")
    monkeypatch.setenv("TORQHOME", str(torq))
    monkeypatch.setenv("TORQAPPHOME", str(app))
    monkeypatch.setenv("UQS_DATA_ROOT", str(data))
    return torq, app, data


def test_unset_means_the_vendored_trees_and_the_checkouts_output(tmp_path, monkeypatch):
    for var in ("TORQHOME", "TORQAPPHOME", "UQS_DATA_ROOT"):
        monkeypatch.delenv(var, raising=False)
    p = stack_paths.paths_for_root(tmp_path, "uqf")
    assert p.torqhome == tmp_path / "lib" / "torq"
    assert p.torqapphome == tmp_path / "lib" / "torq-finance-starter-pack"
    assert p.torqdata == tmp_path / "output" / "uqs"


def test_the_generated_environment_uses_the_configured_trees(tmp_path, external):
    torq, app, data = external
    env = build_env(stack_paths.paths_for_root(tmp_path / "release", "uqf"))
    assert env["TORQHOME"] == str(torq) and env["KDBCONFIG"] == str(torq / "config")
    assert env["TORQAPPHOME"] == str(app) and env["KDBAPPCONFIG"] == str(app / "appconfig")
    assert env["TORQDATA"] == str(data / "uqs") and env["KDBHDB"] == str(data / "uqs" / "hdb")


def test_each_runtime_keeps_its_own_directory_under_the_data_root(tmp_path, external):
    _, _, data = external
    assert stack_paths.paths_for_root(tmp_path, "torq").torqdata == data / "uqs-torq"


def test_the_launcher_runs_the_configured_torq_sh(tmp_path, external, monkeypatch):
    torq, _, _ = external
    seen = {}
    monkeypatch.setattr(runtime, "bootstrap", lambda paths, base_port=None: {})

    def fake_run(cmd, **_):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    runtime.run_torq_sh(stack_paths.paths_for_root(tmp_path, "uqf"), ["summary"])
    assert seen["cmd"][0] == str(torq / "torq.sh")


@pytest.mark.parametrize(
    ("var", "value", "complaint"),
    [
        ("TORQHOME", "relative/torq", "must be an absolute path"),
        ("TORQHOME", "/no/such/torq", "is not a directory"),
        ("TORQAPPHOME", "/no/such/pack", "is not a directory"),
        ("UQS_DATA_ROOT", "/no/such/data", "is not a directory"),
    ],
)
def test_a_configured_path_that_is_wrong_is_refused(
    tmp_path, external, monkeypatch, var, value, complaint
):
    monkeypatch.setenv(var, value)
    with pytest.raises(UqsError, match=complaint):
        stack_paths.paths_for_root(tmp_path, "uqf")


def test_a_directory_that_is_not_torq_is_refused_by_name(tmp_path, external, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("TORQHOME", str(empty))
    with pytest.raises(UqsError, match="TORQHOME=.* has no torq.q"):
        stack_paths.paths_for_root(tmp_path, "uqf")


def test_the_repository_root_is_found_without_lib_torq():
    """A deployed release ships no lib/; its root is still found."""
    assert stack_paths.ETL_DIR in stack_paths._ROOT_MARKERS
    assert Path("lib") / "torq" not in stack_paths._ROOT_MARKERS
    assert (stack_paths.repo_root() / stack_paths.PACKAGE_DIR).is_dir()
