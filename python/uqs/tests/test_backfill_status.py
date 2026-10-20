"""`uqs list backfills`: what an attached run left behind, read back (#1454).

No process is started: the files a run leaves - its log's closing RESULT line and the owner
record in the attachment's folder - are written by hand, and the listing must tell written
from queryable, running from dead, and say what to do next.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest
from attach_backfill_support import UQF_ROOT
from typer.testing import CliRunner

from uqs import cli
from uqs.attach import backfill_owner, config
from uqs.paths import paths_for_root
from uqs.stack import backfill_status, listing


@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.setenv(config.ATTACH_DIR_ENV, str(tmp_path / "attachments"))
    monkeypatch.setenv("UQF_STATUS_DIR", str(tmp_path / "status"))
    return type("W", (), {"status": tmp_path / "status", "paths": paths_for_root(UQF_ROOT)})


def leave(world, attachment: str, identity: str, result: dict | None, *, stamp: str = "a"):
    state = world.status / "attached" / f"{attachment}-{identity}"
    (state / "logs").mkdir(parents=True, exist_ok=True)
    body = "2026.10.16D12:00:00|INFO|w|something\n"
    if result is not None:
        body += "RESULT " + json.dumps(result) + "\n"
    (state / "logs" / f"w-{stamp}.log").write_text(body)
    return state


def test_nothing_run_lists_nothing(world):
    assert backfill_status.rows(world.paths) == []


def test_written_and_queryable_are_told_apart(world):
    leave(
        world,
        "pb-uat",
        "abc123",
        {
            "worker": "w",
            "state": "completed",
            "outcome": "queryable",
            "queryable": True,
            "failed_readers": [],
        },
    )
    leave(
        world,
        "pb-prod",
        "def456",
        {
            "worker": "w",
            "state": "completed",
            "outcome": "written_not_queryable",
            "queryable": False,
            "failed_readers": ["hdb1: cannot connect"],
        },
    )
    rows = {r["attachment"]: r for r in backfill_status.rows(world.paths)}
    assert (rows["pb-uat"]["outcome"], rows["pb-uat"]["queryable"]) == ("queryable", "yes")
    bad = rows["pb-prod"]
    assert (bad["outcome"], bad["queryable"]) == ("written_not_queryable", "no")
    assert "NOT queryable" in bad["detail"] and "re-run the same command" in bad["detail"]
    assert "hdb1: cannot connect" in bad["detail"], "the failure is named, not just counted"
    assert bad["destination"] == "def456" and bad["worker"] == "w"


def test_a_run_that_left_no_outcome_says_it_died(world):
    leave(world, "pb-uat", "abc123", None)
    (row,) = backfill_status.rows(world.paths)
    assert row["state"] == "no outcome" and "died or was killed" in row["detail"]


def test_a_failed_run_carries_its_error(world):
    leave(
        world,
        "pb-uat",
        "abc123",
        {
            "worker": "w",
            "state": "failed",
            "outcome": "failed",
            "error": "hdb_scoped: the fence began",
            "queryable": False,
        },
    )
    (row,) = backfill_status.rows(world.paths)
    assert row["state"] == "failed" and row["detail"] == "hdb_scoped: the fence began"


def test_a_live_owner_record_makes_the_run_running(world):
    leave(world, "pb-uat", "abc123", {"worker": "w", "state": "completed", "outcome": "queryable"})
    backfill_owner.claim("pb-uat", "w", os.getpid(), "abc123")
    (row,) = backfill_status.rows(world.paths)
    assert (
        row["state"] == "running" and row["outcome"] == "" and f"pid {os.getpid()}" in row["detail"]
    )
    gone = subprocess.Popen(["true"])
    gone.wait()
    backfill_owner.claim("pb-uat", "w", gone.pid, "abc123")
    (row,) = backfill_status.rows(world.paths)
    assert row["state"] == "completed", "a dead owner is not running"


def test_the_latest_log_of_a_destination_is_the_one_shown(world):
    state = leave(
        world, "pb-uat", "abc123", {"worker": "w", "state": "failed", "outcome": "failed"}
    )
    old = next((state / "logs").glob("*.log"))
    os.utime(old, (1, 1))
    leave(
        world,
        "pb-uat",
        "abc123",
        {"worker": "w", "state": "completed", "outcome": "queryable", "queryable": True},
        stamp="b",
    )
    (row,) = backfill_status.rows(world.paths)
    assert row["outcome"] == "queryable"


def test_list_backfills_is_a_listable_kind_and_prints(world):
    assert "backfills" in listing.LISTABLE_KINDS
    leave(
        world,
        "pb-uat",
        "abc123",
        {"worker": "w", "state": "completed", "outcome": "queryable", "queryable": True},
    )
    result = CliRunner().invoke(
        cli.app, ["list", "backfills"], catch_exceptions=False, env={"COLUMNS": "260"}
    )
    assert result.exit_code == 0
    assert "pb-uat" in result.output and "queryable" in result.output
