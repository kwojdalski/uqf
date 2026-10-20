"""A backfill into an attached stack's HDB, end to end, on a DISPOSABLE TorQ stack (#1454, part 4).

Opt-in like the other attach-stack tests (UQS_ATTACH_STACK_TEST=1, `python3 scripts/test.py
attach-stack`): real discovery, tickerplant, rdb, wdb, hdb and sort processes on a port span
nothing else uses, with their own data directory. The attachment is made with the real workflow
(plan, apply, verify); the worker reads a real upstream q process; and the commands are the ones
an operator types, as subprocesses of the CLI.

plan -> dry-run -> run -> a query against the native HDB process -> a retry that rewrites nothing,
with no process of the stack restarted at any point.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from attach_backfill_support import snapshot, start_upstream
from attach_stack_support import CORE, IsolatedStack, requires_stack
from attach_support import make_bundle

from uqs.attach import apply as applying
from uqs.attach import config, plan, service, verify
from uqs.paths import repo_root

pytestmark = requires_stack()
ROOT = repo_root()
WORKER = "upstream_trades_backfill"
TABLE = "imported_trades"
DEFINITION = (
    "imported_trades:([] time:`timestamp$(); sym:`symbol$(); venue:`symbol$(); "
    "price:`float$(); size:`long$(); side:`long$())"
)
RANGE = ["--from", "2015-01-07T09:00", "--to", "2015-01-07T11:00", "--version", "v1"]
MODE = ["--on-conflict", "replace"]


@pytest.fixture(scope="module")
def upstream():
    proc, port = start_upstream()
    yield port
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture
def world(tmp_path, upstream):
    """A running stack with imported_trades attached and verified, pinned to its real storage."""
    stack = IsolatedStack(tmp_path / "stack")
    state = stack.root / "attachments"
    stack.env.update(
        {
            "UQF_STATUS_DIR": str(tmp_path / "status"),
            "UQF_SOURCE_CRED_UPSTREAM_TRADES": f"localhost:{upstream}",
        }
    )
    stack.env.pop("UQF_MODE", None)
    stack.env.pop("UQF_ON_CONFLICT", None)
    try:
        stack.start(*CORE)
        bundle = make_bundle(tmp_path / "bundle", ledger_q=DEFINITION, table=TABLE)
        att = service.create(
            "pb-uat",
            "pb_general_ledger",
            bundle,
            stack.port,
            ROOT,
            host="127.0.0.1",
            discovery=f"127.0.0.1:{stack.port + 1}",
            runtime_only=False,
        )
        config.save(att, state)
        assert applying.apply(att, stack.ipc(), ROOT, state_dir=state).state == "applied"
        planned = plan.build(att, stack.ipc(), ROOT)
        service.save_planned(att, planned, state)  # pins the storage the stack reports
        pinned = config.load("pb-uat", state)
        assert pinned.storage.hdb_dir, "the stack's HDB directory is recorded"
        assert verify.verify(pinned, stack.ipc(), state).ok
        stack.env[config.ATTACH_DIR_ENV] = str(state)
        yield stack
    finally:
        stack.teardown()


def operator(stack, *argv: str, timeout: float = 240):
    """What an operator types: uqf's own runtime - the stack is the attached one, found through
    the saved attachment, never through the runtime the harness started it with."""
    return subprocess.run(
        [sys.executable, "-c", "from uqs.cli import main; main()", *argv],
        cwd=ROOT,
        env={**stack.env, "UQS_RUNTIME": "uqf", "COLUMNS": "300"},
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def backfill(stack, *argv: str, timeout: float = 240):
    return operator(
        stack,
        "job",
        "backfill",
        WORKER,
        "--attach",
        "pb-uat",
        *RANGE,
        *MODE,
        *argv,
        timeout=timeout,
    )


def fenced(result) -> bool:
    return "end of day is at" in (result.stdout + result.stderr) or "last end of day" in (
        result.stdout + result.stderr
    )


def hdb_rows(stack) -> int:
    return int(stack.q("hdb1", "count select from imported_trades where date=2015.01.07"))


def test_plan_dry_run_run_query_and_a_safe_retry_without_restarting_anything(world):
    stack = world
    hdb = stack.hdb_dir
    processes = sorted(stack.pids())

    # ---- plan: read-only, and it names what a run would touch
    planned = backfill(stack, "--mode", "plan", "--json")
    if fenced(planned):
        pytest.skip(
            "the stack's end of day is within the hour: the coordination gate refuses; run later"
        )
    assert planned.returncode == 0, planned.stdout + planned.stderr
    report = json.loads(planned.stdout)
    assert report["mutates"] is False and report["destination"]["hdb_dir"] == str(hdb.resolve())
    assert [t["procname"] for t in report["reload_targets"]] == ["hdb1"]
    assert "wdb1" in report["writers"] and report["coverage"]["fetch"] == 2
    # the stack's HDB already holds this date - with the starter pack's own tables, not ours
    assert report["native_partitions"] == [
        {"date": "2015-01-07", "exists": True, "has_dataset": False}
    ]
    native = sorted(p.name for p in (hdb / "2015.01.07").iterdir() if p.is_dir())
    assert native and TABLE not in native
    native_before = {t: snapshot(hdb / "2015.01.07" / t) for t in native}
    other_dates = sorted(p.name for p in hdb.iterdir() if p.name != "2015.01.07")
    before = snapshot(hdb)

    # ---- dry run: reads the source, writes nothing
    dry = backfill(stack, "--mode", "dry-run", "--wait", "--json")
    assert dry.returncode == 0, dry.stdout + dry.stderr
    assert json.loads(dry.stdout[dry.stdout.index("{") :])["result"]["outcome"] == "dry_run"
    assert snapshot(hdb) == before

    # ---- run: the rows are written and covered, and the stack's own HDB says it cannot serve them
    # yet - a partitioned HDB takes its tables from its LATEST partition, which does not hold the
    # newly attached table until a native end of day writes it. Written is not queryable.
    ran = backfill(stack, "--mode", "run", "--wait", "--json")
    first = json.loads(ran.stdout[ran.stdout.index("{") :])
    assert ran.returncode == 3, ran.stdout + ran.stderr
    assert first["result"]["outcome"] == "written_not_queryable" and first["exit_code"] == 3
    assert "does not define imported_trades yet" in first["result"]["failed_readers"][0]
    assert (hdb / "2015.01.07" / TABLE).is_dir(), "the rows are on disk"
    assert {t: snapshot(hdb / "2015.01.07" / t) for t in native} == native_before, (
        "the date's own tables are byte for byte what they were"
    )
    assert sorted(p.name for p in hdb.iterdir() if p.name != "2015.01.07") == other_dates
    assert stack.rows("rdb1", TABLE) == 0, "today's RDB is not populated by a historical backfill"
    assert int(stack.q("stp1", f".stplg.msgcount`{TABLE}")) == 0, "nothing went through the plant"
    with pytest.raises(Exception, match=TABLE):
        hdb_rows(stack)
    written = snapshot(hdb)

    # ---- the stack's own end of day writes the table into its newest partition ...
    stack.end_of_day()

    # ---- ... and the retry rewrites nothing, asks the reader again, and the rows are queryable
    again = backfill(stack, "--mode", "run", "--wait", "--json")
    assert again.returncode == 0, again.stdout + again.stderr
    second = json.loads(again.stdout[again.stdout.index("{") :])
    assert second["result"]["state"] == "idle" and second["result"]["outcome"] == "queryable"
    rows = hdb_rows(stack)
    assert rows > 0, "the native HDB process serves the backfilled date"
    day = str(hdb / "2015.01.07")
    assert {k: v for k, v in snapshot(hdb / "2015.01.07").items() if k != day} == {
        k: v for k, v in written.items() if k.startswith(day + "/")
    }, "the backfilled date is exactly as the first run left it"

    # ---- the stack was never restarted, and the operator can see what happened
    assert sorted(stack.pids()) == processes
    listing = operator(stack, "list", "backfills")
    assert "pb-uat" in listing.stdout and WORKER in listing.stdout
