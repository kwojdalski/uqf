"""One stop policy, and one `--kill`, on every command that stops a process (#2689).

`uqs stop`, `uqs connector stop`, `uqs job receiver stop` and `uqs job
attachment remove` each stop by uqs/process_stop.py - SIGTERM, wait until
the process is confirmed gone, SIGKILL only with `--kill` - and each takes
that `--kill` from cli/stop_options.py, so it means the same everywhere.
This holds the option on every one of them, then what each does with it -
with a real child that ignores SIGTERM where the command signals a pid itself.
"""

from __future__ import annotations

import socket
import subprocess
from types import SimpleNamespace
from typing import Any, cast

import pytest
import typer
from stop_children import IGNORES, reap, spawn
from typer.testing import CliRunner

from uqs import cli
from uqs.attach import backfill_owner, backfill_remote, publisher
from uqs.external import crypto
from uqs.paths import UqsError, default_paths
from uqs.stack import runtime

#: Every command that stops a process, as the words that reach it.
STOPPERS = (
    ("stop",),
    ("connector", "stop"),
    ("job", "receiver", "stop"),
    ("job", "attachment", "remove"),
)

runner = CliRunner()


def _options(*path: str) -> dict[str, Any]:
    command: Any = typer.main.get_command(cli.app)
    for word in path:
        command = command.commands[word]
    return {o: p for p in command.params for o in getattr(p, "opts", [])}


@pytest.mark.parametrize("path", STOPPERS, ids=" ".join)
def test_every_stop_command_takes_kill(path):
    assert "--kill" in _options(*path), f"`uqs {' '.join(path)}` lacks --kill: take KillOpt"


def test_kill_means_the_same_on_every_stop_command():
    helps = {" ".join(p): _options(*p)["--kill"].help for p in STOPPERS}
    assert len(set(helps.values())) == 1, f"--kill drifted: {helps}"


# ------------------------------------------------------------- connector stop


def test_a_connector_that_survives_is_not_reported_stopped(monkeypatch):
    """#2688: it printed "connector stopped" for one still running."""
    survivor = UqsError("the crypto recorder: pid 7 is still running 15s after SIGTERM")
    monkeypatch.setattr(crypto, "stop_crypto_recorder", _raises(survivor))
    result = runner.invoke(cli.app, ["connector", "stop", "crypto"])
    assert result.exit_code == 1 and "connector stopped" not in result.stdout


def test_connector_stop_kill_reaches_the_connector(monkeypatch):
    seen = []
    monkeypatch.setattr(crypto, "stop_crypto_recorder", lambda paths, kill=False: seen.append(kill))
    assert runner.invoke(cli.app, ["connector", "stop", "crypto", "--kill"]).exit_code == 0
    assert runner.invoke(cli.app, ["connector", "stop", "crypto"]).exit_code == 0
    assert seen == [True, False]


def test_connector_restart_starts_nothing_when_the_stop_leaves_one_running(monkeypatch):
    started = []
    monkeypatch.setattr(crypto, "crypto_recorder_status", lambda paths: {"running": "True"})
    monkeypatch.setattr(crypto, "stop_crypto_recorder", _raises(UqsError("still running")))
    monkeypatch.setattr(crypto, "start_crypto_recorder", lambda *a, **k: started.append(1) or 1)
    assert runner.invoke(cli.app, ["connector", "restart", "crypto"]).exit_code == 1
    assert started == []


def _raises(exc: Exception):
    def fn(*_a, **_k):
        raise exc

    return fn


# -------------------------------------------------------------- receiver stop


@pytest.fixture
def receiver(monkeypatch, tmp_path):
    """A receiver's owner record whose pid is a real child that ignores SIGTERM."""
    child = spawn(IGNORES)
    record = {
        "pid": child.pid,
        "host": socket.gethostname(),
        "worker": "pb_ledger.receiver",
        "attachment": "pb-uat",
    }
    released: list[str] = []
    monkeypatch.setattr(backfill_owner, "active", lambda attachment, root=None: [dict(record)])
    monkeypatch.setattr(
        backfill_owner, "release", lambda attachment, worker, root=None: released.append(worker)
    )
    yield child, released
    reap(child)


def test_a_receiver_that_survives_keeps_its_ownership(receiver):
    """Ownership was released at once, so a detach went ahead beside a live receiver."""
    child, released = receiver
    [run] = backfill_remote.stop_receivers("pb-uat", grace=0.5)
    assert run["outcome"] == "still running" and released == []
    assert child.poll() is None


def test_a_receiver_is_released_once_kill_has_ended_it(receiver):
    child, released = receiver
    [run] = backfill_remote.stop_receivers("pb-uat", grace=0.5, kill=True)
    assert run["outcome"] == "killed" and released == ["pb_ledger.receiver"]


def test_receiver_stop_names_a_survivor_and_exits_one(monkeypatch):
    survivor = {"worker": "pb_ledger.receiver", "pid": 7, "outcome": "still running"}
    seen = []

    def stop(attachment, dry_run=False, kill=False):
        seen.append(kill)
        return [dict(survivor)]

    monkeypatch.setattr(backfill_remote, "stop_receivers", stop)
    words = ["job", "receiver", "stop", "--attachment", "pb-uat", "--kill"]
    result = runner.invoke(cli.app, words)
    assert result.exit_code == 1 and seen == [True]
    assert "still running: pb_ledger.receiver (pid 7)" in result.stdout


# ----------------------------------------------------------- attachment remove


def test_attachment_remove_kill_reaches_the_publishers_stop(monkeypatch):
    seen = []

    def stop(paths, procs, base_port=None, **kw):
        seen.append(kw["kill"])
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(runtime, "stop", stop)
    att = cast(Any, SimpleNamespace(publisher=SimpleNamespace(procname="pb_ledger1")))
    att.stack = SimpleNamespace(id=6001)
    publisher.UqsPublisher(default_paths(), kill=True).stop(att)
    publisher.UqsPublisher(default_paths()).stop(att)
    assert seen == [True, False]
