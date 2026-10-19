"""Running a backfill into an attached stack's HDB (#1454, part 3), on real q processes.

The stack is scripted (attach_backfill_support.HdbStack) but the rest is real: an upstream q
process the worker reads over IPC, the runner (scripts/dev/attached_backfill.q) as a child
process, and a native-shaped HDB directory it writes. What is under test is where the rows go
and where they do not, what the run leaves behind, and who owns the attachment while it runs.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from attach_backfill_support import UQF_ROOT, HdbStack, save_attachment, snapshot

from uqs.attach import backfill_coordination as coordination
from uqs.attach import backfill_owner, backfill_run, config, detach, service
from uqs.attach.config import AttachmentError
from uqs.attach.facts import Host
from uqs.attach.schema import parse_definition
from uqs.interpreter import PEACHQ, q_impl, q_interpreter
from uqs.paths import UqsError, paths_for_root

pytestmark = pytest.mark.skipif(
    q_impl() == PEACHQ,
    reason="the worker's source is the starter pack's partitioned HDB served by a kdb+ process, "
    "and the destination is a kdb+ HDB: PeachQ loads neither",
)

WORKER = "upstream_trades_backfill"
DATASET = "imported_trades"
FROM = datetime(2015, 1, 7, 9, tzinfo=UTC)
TO = datetime(2015, 1, 7, 11, tzinfo=UTC)
TABLE = (
    "imported_trades:([] time:`timestamp$(); sym:`symbol$(); venue:`symbol$(); "
    "price:`float$(); size:`long$(); side:`long$())"
)
#: a clock far from every real one, so the end-of-day fence is open whenever the test runs
FUTURE = {
    "now": "2090.01.01D12:00:00.000000000",
    "eod_date": "2090.01.01",
    "next_roll": "2090.01.02D00:00:00.000000000",
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def upstream():
    """The q process the worker reads, serving the starter pack's 2015 trades."""
    q = q_interpreter(os.environ)
    if q is None:
        pytest.skip("no q interpreter")
    port = _free_port()
    proc = subprocess.Popen(
        [str(q), "tests/q/upstream_instance.q", "-p", str(port), "-q"],
        cwd=UQF_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            break
        except OSError:
            time.sleep(0.2)
    else:
        proc.terminate()
        pytest.fail("the upstream q process did not come up")
    yield port
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture
def world(monkeypatch, tmp_path, upstream):
    attach_dir, status = tmp_path / "attachments", tmp_path / "status"
    hdb = tmp_path / "native-hdb"
    hdb.mkdir()
    _native_partition(hdb, tmp_path)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "tables.q").write_text(TABLE + "\n")
    monkeypatch.setenv(config.ATTACH_DIR_ENV, str(attach_dir))
    monkeypatch.setenv("UQF_STATUS_DIR", str(status))
    monkeypatch.setenv("UQF_SOURCE_CRED_UPSTREAM_TRADES", f"localhost:{upstream}")
    for var in ("UQF_MODE", "UQF_ON_CONFLICT", "UQF_FIXTURE_WRITES"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(coordination, "HOST", Host(local_names=("testhost",)))
    stack = HdbStack(hdb)
    stack.state_over = {n: dict(FUTURE) | {"partition": "2090.01.01"} for n in ("rdb1", "wdb1")}
    monkeypatch.setattr(service, "ipc_for", lambda att: stack)
    save_attachment("pb-uat", hdb, table=parse_definition(TABLE), origin={"folder": str(bundle)})
    return type(
        "W",
        (),
        {
            "stack": stack,
            "hdb": hdb,
            "status": status,
            "tmp": tmp_path,
            "attach_dir": attach_dir,
            "paths": paths_for_root(UQF_ROOT),
            "monkeypatch": monkeypatch,
            "bundle": bundle,
        },
    )


def _native_partition(hdb: Path, tmp_path: Path) -> None:
    """A date the native stack already holds, with a table the backfill does not own."""
    script = tmp_path / "native.q"
    script.write_text(
        f"other:([] time:enlist 2014.12.31D07:00:00.000000000; sym:enlist `EURUSD; qty:enlist 5);\n"
        f'.Q.dpft[hsym `$"{hdb}";2014.12.31;`sym;`other]; exit 0\n'
    )
    done = subprocess.run(
        [str(q_interpreter(os.environ)), str(script), "-q"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr


def run(world, *, mode="run"):
    return backfill_run.launch(
        world.paths,
        "pb-uat",
        WORKER,
        "v1",
        FROM,
        TO,
        ipc=world.stack,
        mode=mode,
        on_conflict="replace",
    )


def read_rows(hdb: Path, day: str) -> int:
    script = hdb.parent / "count.q"
    script.write_text(f'-1 string count get hsym `$"{hdb}/{day}/{DATASET}/time"; exit 0\n')
    out = subprocess.run(
        [str(q_interpreter(os.environ)), str(script), "-q"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return int(out.stdout.strip().splitlines()[-1])


# --------------------------------------------------------------------------- the run


def test_a_run_writes_the_attached_hdb_and_records_where_it_was_asked_to(world):
    default_hdb = world.paths.hdb_dir
    before_default = snapshot(default_hdb)
    started, plan = run(world)
    code, result = backfill_run.follow(started, timeout=120)
    log = started.log.read_text()
    assert code == 0 and result["state"] == "completed", log
    assert read_rows(world.hdb, "2015.01.07") > 0
    assert sorted(p.name for p in world.hdb.iterdir()) == ["2014.12.31", "2015.01.07", "sym"], (
        "dates and sym only: nothing staged or locked inside the root"
    )
    assert snapshot(default_hdb) == before_default, "uqf's own HDB is not touched"
    assert started.state_dir == world.status / "attached" / f"pb-uat-{started.destination.identity}"
    assert (started.state_dir / "etl_coverage").is_file(), "coverage is filed under the destination"
    assert not (world.status / "etl_coverage").exists(), "and not in the shared status directory"
    assert backfill_owner.active("pb-uat") == [], "completion releases ownership"
    assert plan["coordination"]["active_date"] == "2090-01-01"


def test_the_other_native_date_and_table_are_not_touched(world):
    before = snapshot(world.hdb / "2014.12.31")
    started, _ = run(world)
    code, result = backfill_run.follow(started, timeout=120)
    assert code == 0 and result["state"] == "completed", started.log.read_text()
    assert snapshot(world.hdb / "2014.12.31") == before


def test_a_dry_run_writes_nothing_to_the_destination(world):
    before = snapshot(world.hdb)
    started, _ = run(world, mode="dry-run")
    code, result = backfill_run.follow(started, timeout=120)
    assert code == 0 and result["state"] == "completed", started.log.read_text()
    assert snapshot(world.hdb) == before
    assert not Path(str(world.hdb) + ".staging").exists()


def test_writes_stop_at_the_end_of_day_fence(world):
    """The runner is given the instant the fence begins; past it, no write happens."""
    world.stack.state_over = {
        n: {
            "now": "1999.12.31D12:00:00.000000000",
            "eod_date": "1999.12.31",
            "next_roll": "2000.01.01D00:00:00.000000000",
            "partition": "1999.12.31",
        }
        for n in ("rdb1", "wdb1")
    }
    with pytest.raises(UqsError, match="is not completed history"):
        run(world)  # 2015 is after 1999: admission itself refuses
    world.stack.state_over = {
        n: {
            "now": "2020.01.01D12:00:00.000000000",
            "eod_date": "2020.01.01",
            "next_roll": "2020.01.02D00:00:00.000000000",
            "partition": "2020.01.01",
        }
        for n in ("rdb1", "wdb1")
    }
    before = snapshot(world.hdb)
    started, _ = run(world)  # deadline 2020-01-01T23:00 has passed in real time
    code, result = backfill_run.follow(started, timeout=120)
    log = started.log.read_text()
    assert code != 0 and result["state"] == "partial", log
    assert "windows_completed=0 windows_failed=2 rows_published=0" in log
    assert "the end-of-day fence began at 2020.01.01D23:00:00" in log, "each window says why"
    assert snapshot(world.hdb) == before, "no byte of the destination changed"
    assert backfill_owner.active("pb-uat") == []


def test_a_refused_admission_starts_nothing_and_leaves_nothing(world):
    world.stack.state_over["wdb1"] = dict(FUTURE) | {
        "partition": "2090.01.01",
        "reloadcomplete": False,
    }
    before = snapshot(world.hdb, world.status, world.attach_dir)
    with pytest.raises(UqsError, match="end-of-day reload in progress"):
        run(world)
    assert snapshot(world.hdb, world.status, world.attach_dir) == before


# ------------------------------------------------------------------------ ownership


def test_a_second_run_is_refused_while_the_attachment_owns_one(world):
    backfill_owner.claim("pb-uat", "some_worker", os.getpid(), "abc")
    with pytest.raises(AttachmentError, match="has an attached backfill running: some_worker"):
        run(world)
    assert not (world.status / "attached").exists(), "refused before the state directory exists"


def test_detach_refuses_while_a_backfill_owns_the_attachment(world):
    backfill_owner.claim("pb-uat", WORKER, os.getpid(), "abc")
    att = config.load("pb-uat")
    calls = len(world.stack.calls)
    with pytest.raises(AttachmentError, match="has an attached backfill running"):
        detach.detach(att, world.stack, publisher=None, dry_run=True)  # ty: ignore[invalid-argument-type]
    assert len(world.stack.calls) == calls, "refused before the stack was asked anything"
    backfill_owner.release("pb-uat", WORKER)
    assert backfill_owner.active("pb-uat") == []


def test_a_record_whose_process_is_gone_owns_nothing(world):
    gone = subprocess.Popen(["true"])
    gone.wait()
    backfill_owner.claim("pb-uat", WORKER, gone.pid, "abc")
    assert backfill_owner.active("pb-uat") == []
    backfill_owner.refuse_while_active("pb-uat")  # does not raise


# ------------------------------------------------------------------------------- CLI


def _cli(*argv: str):
    from typer.testing import CliRunner

    from uqs import cli

    return CliRunner().invoke(
        cli.app,
        [
            "job",
            "backfill",
            WORKER,
            "--attach",
            "pb-uat",
            "--from",
            "2015-01-07T09:00",
            "--to",
            "2015-01-07T11:00",
            "--version",
            "v1",
            "--on-conflict",
            "replace",
            *argv,
        ],
        catch_exceptions=False,
    )


def test_cli_run_with_wait_follows_the_run_and_exits_with_its_outcome(world):
    result = _cli("--mode", "run", "--wait", "--json")
    assert result.exit_code == 0, result.output
    import json

    out = json.loads(result.output[result.output.index("{") :])
    assert out["result"]["state"] == "completed" and out["exit_code"] == 0
    assert out["destination"]["hdb_dir"] == str(world.hdb.resolve())
    assert read_rows(world.hdb, "2015.01.07") > 0


def test_cli_run_without_wait_returns_at_once_and_the_run_finishes_on_its_own(world):
    result = _cli("--mode", "dry-run")
    assert result.exit_code == 0, result.output
    assert "started upstream_trades_backfill (pid" in " ".join(result.output.split())
    for _ in range(300):
        if not backfill_owner.active("pb-uat"):
            break
        time.sleep(0.1)
    assert backfill_owner.active("pb-uat") == [], "ownership is released when the run ends"
    assert not (world.hdb / "2015.01.07").exists(), "a dry run wrote nothing"
