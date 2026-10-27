"""`uqs job backfill --attach NAME --writer HOST:PORT --force`: no receiver, nothing checked.

What the launcher hands the runner, and what it does NOT refuse. The runner's own forced
manager (scripts/dev/attached_backfill.q) writes over plain IPC into whatever process listens
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


@pytest.fixture
def world(monkeypatch, tmp_path):
    return make_world(monkeypatch, tmp_path)


class _Popen:
    """Records the runner's command line in place of starting it."""

    argv: list[str] = []

    def __init__(self, argv, **kw) -> None:
        _Popen.argv = [str(a) for a in argv]
        self.pid = 4242


def _launch(world, monkeypatch, **kw):
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
        ["job", "backfill", WORKER, "--attach", "pb-uat", "--from", "2015-01-07",
         "--to", "2015-01-08", "--version", "v1", "--force"],
        catch_exceptions=False,
    )  # fmt: skip
    assert result.exit_code != 0
    assert any("--force is for --writer" in m for m in errors), result.output


def test_force_refuses_any_strategy_but_append(world, monkeypatch):
    errors: list[str] = []
    monkeypatch.setattr("uqs.cli.shared.log.error", lambda m, *a: errors.append(str(m).format(*a)))
    result = CliRunner().invoke(
        cli.app,
        ["job", "backfill", WORKER, "--attach", "pb-uat", "--from", "2015-01-07",
         "--to", "2015-01-08", "--version", "v1", "--writer", "uat-box:6055", "--force",
         "--on-conflict", "upsert"],
        catch_exceptions=False,
    )  # fmt: skip
    assert result.exit_code != 0
    assert any("--force only appends" in m for m in errors), result.output
