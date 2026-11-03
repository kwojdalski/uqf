"""A backfill rehearsal - `--dry-run`, `--mode plan`, `--mode validate` - never
bootstraps the data directory (#1902).

`uqs job backfill --dry-run` used to go through run_torq_sh, which bootstraps
first: on a fresh tree that wrote 1,094 paths - process.csv, database.q, the
DQE copy and filled HDB partitions - from a command promising to change
nothing. A rehearsal now starts the worker against the data directory as the
fleet's last start left it, and refuses, naming `uqs start`, when there is
nothing to rehearse against. Only setenv.sh carries the call's own
environment, so it is written to a temporary directory that is gone once the
launcher returns.

test_dry_run_tree.py holds the CLI to "changes nothing" on an empty data root;
this file covers the bootstrapped case, which a fixture there cannot reach
without a running fleet.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from uqs.paths import UqsError, UqsPaths
from uqs.stack import backfill, rehearsal, runtime
from uqs.stack.run_modes import MODES, REHEARSAL_MODES

REPO = Path(__file__).resolve().parents[3]
WORKER = "demo_deals_backfill"
PROC = backfill.procname_for(WORKER)


def _paths(data: Path) -> UqsPaths:
    return UqsPaths(
        repo_root=REPO,
        torqhome=REPO / "lib" / "torq",
        torqapphome=REPO / "lib" / "torq-finance-starter-pack",
        torqdata=data,
        scripts_dir=REPO / "scripts",
        orchestrator_dir=REPO / "python" / "uqs",
    )


def _bootstrapped(data: Path, *procnames: str) -> None:
    """What a start leaves that a rehearsal reads: the HDB and process.csv."""
    (data / "hdb").mkdir(parents=True)
    rows = "".join(f"localhost,9000,bounded,{p},,,,,,,q\n" for p in procnames)
    (data / "process.csv").write_text(
        "host,port,proctype,procname,U,localtime,g,T,w,load,qcmd\n" + rows
    )


def _tree(root: Path) -> dict[str, float]:
    return {str(p.relative_to(root)): p.stat().st_mtime for p in root.rglob("*")}


@pytest.fixture
def launcher(tmp_path, monkeypatch) -> Path:
    """A site launcher that records what it was run with, outside the data
    directory: its arguments, $SETENV, whether that file existed, the port."""
    seen = tmp_path / "seen"
    script = tmp_path / "bin" / "torq.sh"
    script.parent.mkdir()
    script.write_text(
        "#!/bin/sh\n"
        f'{{ echo "$*"; echo "$SETENV"; test -f "$SETENV" && echo yes || echo no; '
        f'echo "$KDBBASEPORT"; }} > {seen}\n'
    )
    script.chmod(0o755)
    monkeypatch.setenv("UQS_TORQ_LAUNCHER", str(script))
    # What refuse_start asks - the q version, the ports, the attachments - is
    # test_start_policy.py's; here the data directory is under test.
    monkeypatch.setattr(rehearsal.start_policy, "refuse_start", lambda *a, **k: None)
    return seen


def _bounds() -> tuple[datetime, datetime]:
    return datetime(2026, 9, 13), datetime(2026, 9, 14)


def _start(paths: UqsPaths, mode: str = "dry-run", base_port: int | None = None):
    return backfill.start(paths, WORKER, "v1", *_bounds(), base_port=base_port, mode=mode)


def test_a_rehearsal_on_a_fresh_tree_is_refused_and_writes_nothing(tmp_path, launcher):
    data = tmp_path / "data"
    with pytest.raises(UqsError, match=r"is not bootstrapped.*`uqs start` first"):
        _start(_paths(data))
    assert not data.exists() and not launcher.exists()


def test_a_rehearsal_on_a_bootstrapped_tree_writes_nothing_there(tmp_path, launcher):
    data = tmp_path / "data"
    _bootstrapped(data, PROC)
    before = _tree(data)
    assert _start(_paths(data), base_port=7300).returncode == 0
    assert _tree(data) == before, "no bootstrap, no deployment record, no setenv.sh"
    argv, setenv, existed, port = launcher.read_text().splitlines()
    assert argv.startswith(f"start {PROC} -extras ") and argv.endswith("-mode dry_run")
    assert existed == "yes" and not Path(setenv).exists(), "a temporary setenv.sh, removed"
    assert not Path(setenv).is_relative_to(data)
    assert port == "7300", "the call's own base port, not the last start's"


def test_a_worker_process_csv_does_not_list_is_refused_naming_it(tmp_path, launcher):
    data = tmp_path / "data"
    _bootstrapped(data, "rdb1")
    with pytest.raises(UqsError, match=f"does not list {PROC}.*`uqs start` first"):
        _start(_paths(data))
    assert not launcher.exists()


def test_a_deployment_set_missing_what_it_reads_is_refused(tmp_path, launcher, monkeypatch):
    data = tmp_path / "data"
    _bootstrapped(data, PROC)
    (data / "deployment.txt").write_text("rdb1\n")
    monkeypatch.setattr(rehearsal.deployment, "planned", lambda p, a: ("rdb1", "hdb1", PROC))
    with pytest.raises(UqsError, match=r"leaves out hdb1.*`uqs start hdb1`"):
        _start(_paths(data))
    assert not launcher.exists()


@pytest.mark.parametrize("mode", [*MODES, None])
def test_only_a_run_bootstraps(mode, monkeypatch):
    """None is a run: `uqs job backfill` without --mode."""
    called: list[str] = []
    monkeypatch.setattr(rehearsal, "run_bootstrapped", lambda *a, **k: called.append("rehearsal"))
    monkeypatch.setattr(runtime, "run_torq_sh", lambda *a, **k: called.append("bootstrap"))
    backfill.start(_paths(Path("/nonexistent")), WORKER, "v1", *_bounds(), mode=mode)
    assert called == ["rehearsal" if mode in REHEARSAL_MODES else "bootstrap"]


def test_the_rehearsal_keeps_the_inherited_environment(tmp_path, launcher, monkeypatch):
    """As run_torq_sh: subprocess env= replaces the environment, so PATH must survive."""
    data = tmp_path / "data"
    _bootstrapped(data, PROC)
    monkeypatch.setenv("UQS_PROBE", "kept")
    seen: dict = {}
    real = rehearsal.subprocess.run
    monkeypatch.setattr(
        rehearsal.subprocess, "run", lambda cmd, **kw: seen.update(kw) or real(cmd, **kw)
    )
    _start(_paths(data))
    assert seen["env"]["UQS_PROBE"] == "kept" and seen["env"]["PATH"] == os.environ["PATH"]


@pytest.mark.parametrize(("flag", "noted"), [("--dry-run", True), ("--mode=plan", False)])
def test_a_started_dry_run_ends_with_the_note(monkeypatch, flag, noted):
    """As every dry run does (#1880): test_dry_run_tree.py sees only the refusal."""
    from rich.text import Text
    from typer.testing import CliRunner

    from uqs import cli
    from uqs.cli.safety import dry_run_note

    monkeypatch.setattr(
        backfill, "start", lambda *a, **k: subprocess.CompletedProcess([], 0, "", "")
    )
    argv = ["job", "backfill", WORKER, "--source-version", "v1", "--date", "2026-09-13", flag]
    result = CliRunner().invoke(cli.app, argv)
    assert result.exit_code == 0, result.output
    note = Text.from_markup(dry_run_note()).plain
    lines = result.stdout.strip().splitlines()
    assert (bool(lines) and lines[-1] == note) is noted
