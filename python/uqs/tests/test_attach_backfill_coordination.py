"""Native write coordination for an attached backfill (#1454, part 2).

There is no lock the native writers honour (see uqs.attach.backfill_coordination), so what is
tested is what replaces it: the admission refusals - each by message, and all reported
together - and, on real q processes, the property the symbol side rests on (kdb+ serialises
sym-file extension between separate processes) and `writer_state`, the read-only question
admission asks each writer.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time

import pytest
from attach_backfill_support import DATASET, WORKER, facts_of, make_world, plan_with
from attach_support import SORT_DEFAULT, probe
from typer.testing import CliRunner

from uqs import cli
from uqs.attach import backfill_coordination as coordination
from uqs.attach import backfill_plan as planning
from uqs.attach.facts import Host
from uqs.attach.remote import Endpoint, KolaIpc, source
from uqs.interpreter import PEACHQ, q_impl, q_interpreter
from uqs.paths import UqsError


@pytest.fixture
def world(monkeypatch, tmp_path):
    return make_world(monkeypatch, tmp_path)


COMMON = [
    "--attach",
    "pb-uat",
    "--from",
    "2026-09-11",
    "--to",
    "2026-09-13",
    "--source-version",
    "v1",
]


def invoke(*argv):
    # Wide, as test_attach_backfill_run.py's: rich truncates a long cell to the
    # terminal, so a longer tmp path (an xdist worker's) cut the plan's lines.
    return CliRunner().invoke(
        cli.app, ["job", "backfill", WORKER, *COMMON, *argv], env={"COLUMNS": "300"}
    )


def refused(world, match, **state):
    for name, over in state.items():
        world.stack.state_over[name] = over
    with pytest.raises(UqsError, match=match) as info:
        plan_with(world, facts_of())
    return str(info.value)


# ------------------------------------------------------------------ the admission gate


def test_a_healthy_stack_reports_how_the_run_is_coordinated(world):
    co = plan_with(world, facts_of())["coordination"]
    assert co["wdb"] == "wdb1"
    assert co["active_date"] == "2026-10-16"
    assert co["next_roll"] == "2026-10-17T00:00:00+00:00"
    assert co["eod_guard_minutes"] == 60 and co["warnings"] == []
    assert co["last_writable_date"] == "2026-10-15"
    assert co["writes_stop_at"] == "2026-10-16T23:00:00+00:00"
    assert "uqf adds no lock the native writers honour" in co["mechanism"]
    assert co["wdb"] == "wdb1" and co["writers"] == ["rdb1", "wdb1"]


@pytest.mark.parametrize("day", ["2026.10.16", "2026.10.17", "2027.01.01"])
def test_the_native_active_write_date_and_later_are_never_backfilled(world, day):
    with pytest.raises(UqsError, match=rf"{day.replace('.', '-')} is not completed history"):
        plan_with(world, facts_of(dates=["2026.09.12", day]))


def test_the_earlier_of_the_wdbs_partition_and_trading_date_is_the_active_date(world):
    # the wdb is still filling the 15th although its trading date has moved on
    world.stack.state_over["wdb1"] = {"partition": "2026.10.15"}
    with pytest.raises(UqsError, match="2026-10-15 is not completed history: wdb1 is writing"):
        plan_with(world, facts_of(dates=["2026.10.15"]))
    assert plan_with(world, facts_of(dates=["2026.10.14"]))["coordination"]["active_date"] == (
        "2026-10-15"
    )


def test_an_unknown_active_date_is_a_refusal_not_a_pass(world):
    refused(world, "wdb1 does not report which date it is writing", wdb1={"partition": ""})


def test_end_of_day_close_by_is_fenced(world):
    refused(
        world,
        "end of day is at 2026-10-17T00:00:00\\+00:00, 30 minutes away",
        wdb1={"now": "2026.10.16D23:30:00.000000000"},
    )


def test_just_after_end_of_day_is_fenced(world):
    refused(
        world,
        "last end of day was 30 minutes ago: its sort, merge and reload may still",
        wdb1={"now": "2026.10.16D00:30:00.000000000"},
    )


def test_a_writer_clock_in_nanoseconds_is_read_to_the_microsecond(world):
    """A Linux writer's `.z.p` has nanoseconds: read, not refused (#1650)."""
    world.stack.state_over["wdb1"] = {"now": "2026.10.16D12:00:00.080122814"}
    assert plan_with(world, facts_of())["coordination"]["next_roll"] == (
        "2026-10-17T00:00:00+00:00"
    )
    refused(
        world,
        "end of day is at 2026-10-17T00:00:00\\+00:00, 30 minutes away",
        wdb1={"now": "2026.10.16D23:29:59.999999999"},
    )


def test_an_open_end_of_day_reload_blocks(world):
    refused(world, "end-of-day reload in progress", wdb1={"reloadcomplete": False})


def test_unknown_clock_blocks(world):
    refused(world, "does not report its clock and next roll", wdb1={"next_roll": ""})


def test_the_earliest_active_date_of_all_writers_wins(world):
    """A second wdb on the same directory still filling an earlier date moves the line."""
    world.stack.add(
        probe(
            "wdb2",
            "wdb",
            6007,
            "6000",
            hdbdir=f":{world.hdb}",
            savedir=":/data/w2",
            mode="save",
            writedownmode="default",
            tablelist=[],
            subscribed=[],
            ignorelist=[],
            sortrows=list(SORT_DEFAULT),
            tables=[],
            schemas={},
            rows={},
            tickerplanttypes=[],
            subtabs=[""],
            subsyms=[""],
        )
    )
    world.stack.refresh_discovery()
    world.stack.state_over["wdb2"] = {"partition": "2026.10.14", "proctype": "wdb"}
    with pytest.raises(UqsError, match="2026-10-14 is not completed history: wdb2 is writing"):
        plan_with(world, facts_of(dates=["2026.10.14"]))
    co = plan_with(world, facts_of(dates=["2026.10.13"]))["coordination"]
    assert co["active_date"] == "2026-10-14" and co["writers"] == ["rdb1", "wdb1", "wdb2"]


def test_a_sort_config_this_writer_would_not_reproduce_is_refused(world):
    world.stack.named("wdb1")["sortrows"] = [
        {"tabname": "default", "att": "g", "col": "time", "sort": True},
    ]
    refused(
        world,
        f"wdb1's sort.csv sorts {DATASET} by \\['time'\\] with attributes .*'time': 'g'",
    )


def test_a_table_with_its_own_sort_row_is_judged_by_that_row(world):
    world.stack.named("wdb1")["sortrows"] = [
        {"tabname": "default", "att": "g", "col": "time", "sort": True},
        {"tabname": DATASET, "att": "p", "col": "sym", "sort": True},
        {"tabname": DATASET, "att": "", "col": "time", "sort": True},
    ]
    assert plan_with(world, facts_of())["coordination"]["wdb"] == "wdb1"


def test_an_rdb_that_saves_itself_is_only_held_to_its_trading_date(world):
    world.stack.state_over["rdb1"] = {"savetables": True, "reloadenabled": False}
    assert plan_with(world, facts_of())["coordination"]["active_date"] == "2026-10-16"


def test_a_stack_on_another_host_is_refused(world, monkeypatch):
    monkeypatch.setattr(coordination, "HOST", Host(local_names=("elsewhere",)))
    with pytest.raises(UqsError, match="the stack runs on testhost, not this machine"):
        plan_with(world, facts_of())


def test_a_network_filesystem_is_refused(world, monkeypatch):
    monkeypatch.setattr(coordination, "filesystem_of", lambda path: "nfs4")
    with pytest.raises(UqsError, match="is on a nfs4 filesystem: .* local file locking"):
        plan_with(world, facts_of())


def test_the_filesystem_of_a_real_path_is_found(tmp_path):
    assert coordination.filesystem_of(tmp_path) not in coordination.NETWORK_FILESYSTEMS


def test_the_plan_prints_how_the_run_is_coordinated(world, monkeypatch):
    monkeypatch.setattr(planning, "evaluate", lambda *a, **k: facts_of())
    text = " ".join(invoke("--mode", "plan").output.split())
    assert "coordination active write date 2026-10-16 is never touched" in text
    assert "last writable date 2026-10-15); writes stop at 2026-10-16T23:00:00+00:00" in text
    assert "beside root" in text and ".staging (staging)" in text


def test_a_compression_process_is_refused_and_housekeeping_only_warned(world):
    world.stack.add(probe("compress1", "compression", 6010, "6000"), listed_only=True)
    world.stack.refresh_discovery()
    refused(world, "compress1 \\(compression\\) works on HDB partitions in place")
    world.stack.procs.pop(-6010)
    world.stack.add(probe("hk1", "housekeeping", 6011, "6000"), listed_only=True)
    world.stack.refresh_discovery()
    assert any(
        "hk1 (housekeeping)" in w for w in plan_with(world, facts_of())["coordination"]["warnings"]
    )


def test_a_segmented_hdb_is_refused(world):
    (world.hdb / "par.txt").write_text("/elsewhere\n")
    refused(world, "is segmented \\(par.txt\\)")


def test_a_writer_that_cannot_be_asked_is_a_blocker(world, monkeypatch):
    original = world.stack.call

    def call(endpoint, op, request):
        if op == "writer_state" and endpoint.procname == "wdb1":
            from uqs.attach.remote import IpcError

            raise IpcError(endpoint, op, "cannot connect: Connection refused")
        return original(endpoint, op, request)

    monkeypatch.setattr(world.stack, "call", call)
    refused(world, "wdb1 does not answer writer_state")


def test_every_blocker_is_reported_together(world):
    message = refused(
        world,
        "cannot be coordinated",
        wdb1={
            "partition": "2026.09.01",
            "now": "2026.10.16D23:50:00.000000000",
            "reloadcomplete": False,
        },
        rdb1={"eod_date": ""},
    )
    for fragment in (
        "is not completed history",
        "minutes away",
        "reload in progress",
        "rdb1 does not report which date",
    ):
        assert fragment in message
    assert message.count("\n  - ") >= 4


# ------------------------------------------------------ writer_state, on a real q process


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def fake_wdb(tmp_path):
    """A real q process that is a `wdb` as far as `writer_state` can tell."""
    if q_interpreter(os.environ) is None:
        pytest.skip("no q interpreter")
    hdb = tmp_path / "wdb-hdb"
    hdb.mkdir()
    port = _free_port()
    script = tmp_path / "wdb.q"
    script.write_text(
        f".proc.procname:`wdb1; .proc.proctype:`wdb;\n"
        f'.wdb.hdbdir:hsym `$"{hdb}"; .wdb.savedir:hsym `$"{tmp_path}/save";\n'
        ".wdb.mode:`saveandsort;\n"
        ".wdb.writedownmode:`default; .wdb.getpartition:{2026.10.16}; .wdb.permitreload:1b;\n"
        ".wdb.reloadcomplete:1b; .eodtime.d:2026.10.16;\n"
        ".eodtime.nextroll:2026.10.17D00:00:00.000000000;\n"
    )
    proc = subprocess.Popen(
        [str(q_interpreter(os.environ)), str(script), "-p", str(port), "-q"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    ipc = KolaIpc("u", "p", timeout_ms=5000)
    endpoint = Endpoint("wdb1", "127.0.0.1", port)
    for _ in range(100):
        try:
            ipc.call(endpoint, "writer_state", {})
            break
        except Exception:  # noqa: BLE001 - still starting
            time.sleep(0.1)
    else:
        proc.terminate()
        raise AssertionError(
            "the fake wdb did not come up: " + (proc.stdout.read() if proc.stdout else "")
        )
    yield type("W", (), {"hdb": hdb, "ipc": ipc, "endpoint": endpoint})
    proc.terminate()
    proc.wait(timeout=10)


def test_writer_state_reports_what_the_wdb_will_write_and_when(fake_wdb):
    state = fake_wdb.ipc.call(fake_wdb.endpoint, "writer_state", {})
    assert state["proctype"] == "wdb" and state["procname"] == "wdb1"
    assert state["partition"] == "2026.10.16" and state["eod_date"] == "2026.10.16"
    assert state["next_roll"].startswith("2026.10.17D00:00:00")
    assert state["hdbdir"] == f":{fake_wdb.hdb}" and state["reloadcomplete"] is True
    assert state["now"], "the wdb's own clock is part of the answer"


def test_writer_state_is_one_lambda_that_changes_nothing():
    text = source("writer_state")
    assert text.startswith("{[req]")
    assert " set " not in text and "insert" not in text and "upsert" not in text


# ---------------------------------------------- the property the symbol side rests on

_WRITER = """
id:first "J"$.z.x 0; n:"J"$.z.x 1;
i:0; acc:([] s:`symbol$(); ix:`long$());
/ the same names in every process: they race to be the first to add each
do[n; i+:1; t:([] s:`$"p",/:(string i),/:"_",/:string til 15);
  e:.Q.en[`:HDB;t]; acc,:([] s:t`s; ix:"j"$e`s)];
(hsym `$"acc",string id) set acc; exit 0
"""

_CHECK = """
final:get `:HDB/sym;
bad:{[i] a:get hsym `$"acc",string i; exec sum not final[ix]=s from a} each 1+til PROCS;
-1 .j.j `size`distinct`bad!(count final;count distinct final;bad); exit 0
"""


def test_separate_processes_enumerating_one_sym_file_never_duplicate_or_misindex(tmp_path):
    """The mechanism for symbols is not ours: every native writer and uqf's own HDB writer
    call .Q.en, and kdb+ extends the sym file atomically between processes. This is the test
    that fails - on any platform CI runs - if a build stops doing that, in which case an
    attached backfill is unsafe beside the native writers and part 2's premise is gone."""
    q = q_interpreter(os.environ)
    if q is None:
        pytest.skip("no q interpreter")
    if q_impl() == PEACHQ:
        pytest.skip(
            "the writers of a native stack are kdb+ processes; PeachQ's enumeration is its own"
        )
    procs, iterations = 6, 200
    hdb = tmp_path / "hdb"
    hdb.mkdir()
    (tmp_path / "seed.q").write_text(f".Q.en[`:{hdb.name};([] s:`seed`seed2)]; exit 0\n")
    (tmp_path / "writer.q").write_text(_WRITER.replace("HDB", hdb.name))
    (tmp_path / "check.q").write_text(_CHECK.replace("HDB", hdb.name).replace("PROCS", str(procs)))
    run = [str(q)]
    seed = subprocess.run(
        [*run, "seed.q", "-q"],
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert seed.returncode == 0, seed.stdout + seed.stderr
    writers = [
        subprocess.Popen(
            [*run, "writer.q", str(i), str(iterations), "-q"],
            cwd=tmp_path,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        for i in range(1, procs + 1)
    ]
    outputs = [w.communicate(timeout=120)[0] for w in writers]
    assert [w.returncode for w in writers] == [0] * procs, outputs
    check = subprocess.run(
        [*run, "check.q", "-q"],
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    result = json.loads(check.stdout.strip().splitlines()[-1])
    assert result["size"] == result["distinct"] == 2 + iterations * 15, result
    assert result["bad"] == [0] * procs, "every index a writer was given names its symbol"
