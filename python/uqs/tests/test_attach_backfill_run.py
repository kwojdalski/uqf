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
from uqs.attach import backfill_plan as planning
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


class FakeReader:
    """A q process that answers `reload` the way a TorQ HDB does, and says it was asked."""

    def __init__(self, tmp: Path, name: str) -> None:
        self.port = _free_port()
        self.flag = tmp / f"{name}.refuse"
        self.log = tmp / f"{name}.reloads"
        script = tmp / f"{name}.q"
        self.hide = tmp / f"{name}.hide"
        script.write_text(
            f'flag:hsym `$"{self.flag}"; hide:hsym `$"{self.hide}"; trail:hsym `$"{self.log}";\n'
            f"{DATASET}:([] time:`timestamp$());\n"
            'reload:{[x] if[not ()~key flag; \'"injected: this reader refuses to reload"];\n'
            '  h:hopen trail; h "reload\\n"; hclose h;\n'
            f"  $[()~key hide; `{DATASET} set ([] time:`timestamp$());\n"
            f'    value "delete {DATASET} from `."];\n'
            "  1b};\n"
        )
        q = q_interpreter(os.environ)
        self.process = subprocess.Popen(
            [str(q), str(script), "-p", str(self.port), "-q"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", self.port), 0.2).close()
                return
            except OSError:
                time.sleep(0.1)
        self.process.terminate()
        pytest.fail("the fake reader did not come up")

    def refuse(self, on: bool = True) -> None:
        self.flag.touch() if on else self.flag.unlink(missing_ok=True)

    def forget_table(self, on: bool = True) -> None:
        """After its next reload this reader's database no longer defines the dataset."""
        self.hide.touch() if on else self.hide.unlink(missing_ok=True)

    @property
    def reloads(self) -> int:
        return len(self.log.read_text().splitlines()) if self.log.exists() else 0

    def stop(self) -> None:
        self.process.terminate()
        self.process.wait(timeout=10)


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
    reader = FakeReader(tmp_path, "hdb1")
    stack = HdbStack(hdb, reader_ports={"hdb1": reader.port})
    stack.state_over = {n: dict(FUTURE) | {"partition": "2090.01.01"} for n in ("rdb1", "wdb1")}
    monkeypatch.setattr(service, "ipc_for", lambda att: stack)
    save_attachment(
        "pb-uat",
        hdb,
        host="localhost",
        table=parse_definition(TABLE),
        origin={"folder": str(bundle)},
    )
    world = type(
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
            "reader": reader,
        },
    )
    yield world
    reader.stop()


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


# ----------------------------------------------------------- the runner has no default


def _runner(world, *flags: str, status: Path | None = None, decoy: Path | None = None):
    env = {**os.environ, "UQF_STATUS_DIR": str(status or world.tmp / "st-abc123")}
    if decoy is not None:
        env["KDBHDB"] = str(decoy)
    return subprocess.run(
        [str(q_interpreter(os.environ)), str(UQF_ROOT / backfill_run.RUNNER), *flags],
        cwd=UQF_ROOT, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=60, check=False,
    )  # fmt: skip


RANGE_FLAGS = [
    "-worker",
    WORKER,
    "-version",
    "v1",
    "-from",
    "2015.01.07D09:00",
    "-to",
    "2015.01.07D11:00",
]


def test_the_runner_given_no_destination_refuses_and_never_falls_back_to_kdbhdb(world):
    decoy = world.tmp / "uqf-default-hdb"
    decoy.mkdir()
    result = _runner(world, *RANGE_FLAGS, decoy=decoy)
    assert result.returncode == 2
    assert "missing -hdb, -destid, -attachment, -max_date, -deadline, -owner, -readers, -user" in (
        result.stdout + result.stderr
    )
    assert list(decoy.iterdir()) == [], "KDBHDB is not consulted, let alone written"


def test_the_runner_refuses_a_state_directory_that_is_not_the_destinations(world):
    flags = [
        *RANGE_FLAGS, "-hdb", str(world.hdb), "-destid", "abc123", "-attachment", "pb-uat",
        "-max_date", "2026.01.01", "-deadline", "2099.01.01D00:00", "-owner",
        str(world.tmp / "o.json"), "-readers", "_none_", "-user", "admin",
    ]  # fmt: skip
    wrong = _runner(world, *flags, status=world.tmp / "st-someone-else")
    assert wrong.returncode != 0
    assert "is not the directory for destination abc123" in wrong.stdout + wrong.stderr
    assert not (world.hdb / "2015.01.07").exists()


def test_the_runner_refuses_a_fixture_and_a_plan_mode(world):
    base = [
        "-hdb", str(world.hdb), "-destid", "abc123", "-attachment", "pb-uat", "-max_date",
        "2026.01.01", "-deadline", "2099.01.01D00:00", "-owner", str(world.tmp / "o.json"),
        "-readers", "_none_", "-user", "admin",
    ]  # fmt: skip
    fixture = _runner(world, *RANGE_FLAGS, *base, "-fixture")
    assert "-fixture is refused" in fixture.stdout + fixture.stderr
    plan_ = _runner(world, *RANGE_FLAGS, *base, "-mode", "plan")
    assert "-mode plan is the launcher's" in plan_.stdout + plan_.stderr


# --------------------------------------------------------------- written is not queryable


def test_a_run_whose_readers_reloaded_is_queryable_and_releases_coverage(world):
    started, _ = run(world)
    code, result = backfill_run.follow(started, timeout=120)
    assert code == 0 and result["outcome"] == "queryable" and result["queryable"] is True
    assert result["readers"] == ["hdb1"] and result["failed_readers"] == []
    assert world.reader.reloads == 1, "the verified reader was asked once"
    assert (started.state_dir / "etl_ready").is_file(), "the readiness watermark advanced"


def test_only_the_readers_that_serve_the_destination_are_asked_to_reload(world, tmp_path):
    other = FakeReader(tmp_path, "hdb2")
    elsewhere = tmp_path / "elsewhere-hdb"
    elsewhere.mkdir()
    world.stack = HdbStack(
        world.hdb, other=elsewhere, reader_ports={"hdb1": world.reader.port, "hdb2": other.port}
    )
    world.monkeypatch.setattr(service, "ipc_for", lambda att: world.stack)
    world.stack.state_over = {
        n: dict(FUTURE) | {"partition": "2090.01.01"} for n in ("rdb1", "wdb1")
    }
    try:
        started, plan = run(world)
        code, result = backfill_run.follow(started, timeout=120)
    finally:
        other.stop()
    assert code == 0 and result["readers"] == ["hdb1"], "hdb2 serves another directory"
    assert [t["procname"] for t in plan["reload_targets"]] == ["hdb1"]
    assert (world.reader.reloads, other.reloads) == (1, 0)


def test_a_reader_that_will_not_reload_leaves_rows_written_and_covered_but_not_queryable(world):
    world.reader.refuse()
    started, _ = run(world)
    code, result = backfill_run.follow(started, timeout=120)
    log = started.log.read_text()
    assert code == backfill_run.EXIT_NOT_QUERYABLE == 3, log
    assert result["state"] == "completed", "the write itself succeeded"
    assert result["outcome"] == "written_not_queryable" and result["queryable"] is False
    assert result["failed_readers"] == [
        "hdb1: reload failed: injected: this reader refuses to reload"
    ]
    assert read_rows(world.hdb, "2015.01.07") > 0, "the rows are on disk"
    assert (started.state_dir / "etl_coverage").is_file(), "and covered"
    assert not (started.state_dir / "etl_ready").exists(), "but not released to the readers"
    assert "NOT queryable" in log and "nothing will be rewritten" in log
    assert backfill_owner.active("pb-uat") == [], "the run still releases the attachment"


def test_a_reader_that_reloads_but_does_not_serve_the_table_is_not_queryable(world):
    """The real-stack case: an HDB takes its tables from its latest partition, so a table written
    only into an older date is invisible after a successful reload."""
    world.reader.forget_table()
    started, _ = run(world)
    code, result = backfill_run.follow(started, timeout=120)
    assert code == 3 and result["outcome"] == "written_not_queryable", started.log.read_text()
    (why,) = result["failed_readers"]
    assert why.startswith("hdb1: reloaded, but its HDB does not define imported_trades yet")
    assert "next end of day" in why and "hdb-check --attach NAME --apply" in why, (
        "it says how to fix it"
    )
    assert not (started.state_dir / "etl_ready").exists()
    world.reader.forget_table(False)
    second, _ = run(world)
    code, result = backfill_run.follow(second, timeout=120)
    assert code == 0 and result["outcome"] == "queryable" and result["state"] == "idle"


def test_retrying_after_a_failed_reload_rewrites_nothing_and_makes_the_rows_queryable(world):
    world.reader.refuse()
    first, _ = run(world)
    assert backfill_run.follow(first, timeout=120)[0] == 3
    written = snapshot(world.hdb)
    plan_ = planning.plan_attached(
        world.paths, "pb-uat", WORKER, "v1", FROM, TO, ipc=world.stack, on_conflict="replace"
    )
    assert plan_["reload_owed"] is True
    assert plan_["coverage"] == {"fetch": 0, "covered": 2, "deferred": 0}, "every window is covered"
    world.reader.refuse(False)
    second, _ = run(world)
    code, result = backfill_run.follow(second, timeout=120)
    assert code == 0 and result["outcome"] == "queryable", second.log.read_text()
    assert result["state"] == "idle", "nothing was fetched or written"
    assert snapshot(world.hdb) == written, "not one file of the destination changed"
    assert world.reader.reloads == 1 and (second.state_dir / "etl_ready").is_file()
    again = planning.plan_attached(
        world.paths, "pb-uat", WORKER, "v1", FROM, TO, ipc=world.stack, on_conflict="replace"
    )
    assert again["reload_owed"] is False


def test_a_dry_run_asks_no_reader_to_reload(world):
    started, _ = run(world, mode="dry-run")
    code, result = backfill_run.follow(started, timeout=120)
    assert code == 0 and result["outcome"] == "dry_run" and world.reader.reloads == 0


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


# ------------------------------------------------------------- status, runs and logs


def _cli_any(*argv: str):
    from typer.testing import CliRunner

    from uqs import cli

    return CliRunner().invoke(cli.app, list(argv), catch_exceptions=False, env={"COLUMNS": "300"})


def test_the_listing_and_the_run_ledger_show_the_attached_run(world):
    from uqs.stack import runs as stack_runs

    world.reader.refuse()
    started, _ = run(world)
    assert backfill_run.follow(started, timeout=120)[0] == 3
    listing_ = _cli_any("list", "backfills")
    flat = " ".join(listing_.output.split())
    assert "pb-uat" in flat and WORKER in flat and "written_not_queryable" in flat
    ledger = _cli_any("job", "runs", "list", "--attach", "pb-uat")
    assert WORKER in ledger.output, "the destination's own run ledger"
    run_id = stack_runs.history(world.paths, directory=started.state_dir)[0]["run_id"]
    shown = " ".join(_cli_any("job", "runs", "show", run_id, "--attach", "pb-uat").output.split())
    assert "--attach pb-uat" in shown, (
        "the re-run command names the destination, or it would write uqf's HDB"
    )
    assert str(started.state_dir / "logs") in shown.replace("\n", "")
