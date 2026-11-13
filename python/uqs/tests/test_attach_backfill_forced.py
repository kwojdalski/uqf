"""`uqs job backfill --attachment NAME --writer HOST:PORT --override any-writer`: no receiver,
no checks.

What the launcher hands the runner, and what it does NOT refuse. The runner's own forced
manager (scripts/jobs/attached_backfill.q) writes over plain IPC into whatever process listens
at HOST:PORT; that half is exercised against a live q in the remote-writer tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from attach_backfill_support import FROM, TO, WORKER, make_world, save_attachment
from typer.testing import CliRunner

from uqs import cli
from uqs.attach import backfill as attached
from uqs.attach import backfill_remote
from uqs.attach.remote import IpcError
from uqs.paths import UqsError


@pytest.fixture
def world(monkeypatch, tmp_path):
    return make_world(monkeypatch, tmp_path)


class _Popen:
    """Records the runner's command line in place of starting it."""

    argv: list[str] = []

    def __init__(self, argv, **kw) -> None:
        _Popen.argv = [str(a) for a in argv]
        self.pid = 4242


class _Writer:
    """The --writer process answering `writer_state`: filling `partition`, on `eod_date`."""

    def __init__(self, partition: str = "2026-09-14", eod_date: str = "2026-09-14") -> None:
        self.state = {
            "procname": "wdb1", "proctype": "wdb", "partition": partition, "eod_date": eod_date,
            "now": "2026.09.14D10:00:00.000000000", "next_roll": "2026.09.15D00:00:00.000000000",
        }  # fmt: skip
        self.asked: list[tuple[str, int, str]] = []

    def call(self, endpoint, op, request):
        self.asked.append((endpoint.host, endpoint.port, op))
        return self.state


class _Silent:
    def call(self, endpoint, op, request):
        raise IpcError(endpoint, op, "refused")


def _launch(world, monkeypatch, **kw):
    kw.setdefault("ipc", _Writer())
    monkeypatch.setattr(backfill_remote, "q_interpreter", lambda env: Path("/bin/q"))
    monkeypatch.setattr(
        backfill_remote,
        "evaluate",
        lambda *a: {"custom_io": False, "live": True, "source": "s", "dataset": "d", "windows": []},
    )
    monkeypatch.setattr(backfill_remote.subprocess, "Popen", _Popen)
    monkeypatch.setattr(backfill_remote.backfill_owner, "claim", lambda *a: None)
    return backfill_remote.launch_remote(
        world.paths, "pb-uat", WORKER, "v1", FROM, TO,
        writer="uat-box:6055", mode="run", on_conflict="append", **kw,
    )  # fmt: skip


def test_a_forced_destination_needs_no_verification_pinned_hdb_or_published_table(world):
    save_attachment("pb-uat", world.hdb, state="applied", pin=False)
    with pytest.raises(Exception, match="is applied, not verified"):
        attached.resolve(world.paths, "pb-uat", WORKER, on_this_machine=False)
    dest = backfill_remote.forced_destination(world.paths, "pb-uat", WORKER, "uat-box:6055")
    assert dest.hdb_dir == Path(backfill_remote.FORCED_HDB), "the writer's own HDB"
    assert dest.host == "uat-box"


def test_forced_coverage_is_filed_apart_from_the_checked_destinations(world):
    save_attachment("pb-uat", world.hdb)
    checked = attached.resolve(world.paths, "pb-uat", WORKER, on_this_machine=False)
    forced = backfill_remote.forced_destination(world.paths, "pb-uat", WORKER, "testhost:6055")
    assert forced.hdb_dir == checked.hdb_dir, "a pinned HDB is the one written"
    assert forced.state_dir != checked.state_dir, "but a forced run's coverage is its own"


def test_a_forced_launch_passes_force_and_skips_verification(world, monkeypatch):
    save_attachment("pb-uat", world.hdb, state="applied", pin=False)
    run_, plan = _launch(world, monkeypatch, force=True)
    argv = _Popen.argv
    assert plan["forced"] is True
    assert argv[argv.index("-writer") + 1] == "uat-box:6055"
    assert argv[argv.index("-force_hdb") + 1] == "_own_", "the writer's .wdb.hdbdir"
    assert "-force" in argv


def test_a_forced_launch_names_a_pinned_hdb(world, monkeypatch):
    save_attachment("pb-uat", world.hdb)
    _launch(world, monkeypatch, force=True)
    assert _Popen.argv[_Popen.argv.index("-force_hdb") + 1] == str(world.hdb)


def test_without_force_an_unverified_attachment_is_still_refused(world, monkeypatch):
    save_attachment("pb-uat", world.hdb, state="applied")
    with pytest.raises(Exception, match="is applied, not verified"):
        _launch(world, monkeypatch)


def test_force_without_a_writer_is_refused(world, monkeypatch):
    errors: list[str] = []
    monkeypatch.setattr("uqs.cli.shared.log.error", lambda m, *a: errors.append(str(m).format(*a)))
    result = CliRunner().invoke(
        cli.app,
        ["job", "backfill", WORKER, "--attachment", "pb-uat", "--from", "2015-01-07",
         "--to", "2015-01-08", "--source-version", "v1", "--override", "any-writer",
         "--reason", "a test"],
        catch_exceptions=False,
    )  # fmt: skip
    assert result.exit_code != 0
    assert any("--override any-writer is for --writer" in m for m in errors), result.output


def test_force_refuses_any_strategy_but_append(world, monkeypatch):
    errors: list[str] = []
    monkeypatch.setattr("uqs.cli.shared.log.error", lambda m, *a: errors.append(str(m).format(*a)))
    result = CliRunner().invoke(
        cli.app,
        ["job", "backfill", WORKER, "--attachment", "pb-uat", "--from", "2015-01-07",
         "--to", "2015-01-08", "--source-version", "v1", "--writer", "uat-box:6055",
         "--override", "any-writer", "--reason", "a test", "--on-conflict", "upsert"],
        catch_exceptions=False,
    )  # fmt: skip
    assert result.exit_code != 0
    assert any("--override any-writer only appends" in m for m in errors), result.output


def test_a_forced_launch_always_passes_the_completed_history_bound(world, monkeypatch):
    """#1866: --any-writer skips the receiver's checks, not the date fence. The bound is
    the day before the date the writer reports it is filling."""
    save_attachment("pb-uat", world.hdb, state="applied", pin=False)
    writer = _Writer(partition="2026-09-14")
    _launch(world, monkeypatch, force=True, ipc=writer)
    argv = _Popen.argv
    assert argv[argv.index("-max_date") + 1] == "2026.09.13"
    assert writer.asked == [("uat-box", 6055, "writer_state")], "asked of the --writer process"


def test_a_checked_remote_launch_takes_no_max_date_from_the_launcher(world, monkeypatch):
    """The receiver owns its bound; only the forced runner is told one."""
    save_attachment("pb-uat", world.hdb)
    monkeypatch.setattr(backfill_remote, "require_verified", lambda *a: None)
    _launch(world, monkeypatch)
    assert "-max_date" not in _Popen.argv


def test_a_forced_launch_is_refused_when_the_writer_does_not_say_its_date(world, monkeypatch):
    save_attachment("pb-uat", world.hdb, state="applied", pin=False)
    _Popen.argv = []
    with pytest.raises(UqsError, match="does not report which date it is writing"):
        _launch(world, monkeypatch, force=True, ipc=_Writer(partition="", eod_date=""))
    assert _Popen.argv == [], "nothing started"


def test_a_forced_launch_is_refused_when_the_writer_does_not_answer(world, monkeypatch):
    save_attachment("pb-uat", world.hdb, state="applied", pin=False)
    _Popen.argv = []
    with pytest.raises(UqsError, match="does not answer writer_state"):
        _launch(world, monkeypatch, force=True, ipc=_Silent())
    assert _Popen.argv == []
