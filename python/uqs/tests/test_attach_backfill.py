"""`uqs job backfill --attach NAME --mode plan`: resolving an attached destination and
planning against it, read-only (#1454, part 1).

The stack is scripted (attach_support.FakeStack, plus an HDB reader that answers
`hdb_reader`); the plan itself is evaluated by a real q (scripts/dev/attached_plan.q)
against a state directory this test owns. What is under test is what is refused,
what is reported, and that nothing - on disk or on the stack - changes.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from attach_support import FakeStack
from typer.testing import CliRunner

from uqs import cli
from uqs.attach import backfill as attached
from uqs.attach import backfill_plan as planning
from uqs.attach import config, service
from uqs.attach.config import Attachment, Publisher, Stack, Storage
from uqs.attach.manifest import Manifest
from uqs.attach.remote import Endpoint
from uqs.attach.schema import parse_definition
from uqs.cli import shared
from uqs.interpreter import q_interpreter
from uqs.paths import UqsError, paths_for_root

UQF_ROOT = Path(__file__).resolve().parents[3]
runner = CliRunner()
WORKER = "crypto_market_data_backfill"
DATASET = "crypto_market_data"
FROM = datetime(2026, 9, 11, tzinfo=UTC)
TO = datetime(2026, 9, 13, tzinfo=UTC)


def _tree_table() -> Any:
    for line in (UQF_ROOT / "src/etl/plant_tables.q").read_text().splitlines():
        if line.startswith(f".qetl.plant.{DATASET}:"):
            return parse_definition(line)
    raise AssertionError("plant table missing")


class _ErrorLog:
    """What the CLI's `_die` logged: refusals go to the log, not stdout."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def error(self, message: str, *args) -> None:
        self.messages.append(str(message).format(*args))

    def __getattr__(self, _name: str):
        return lambda *a, **kw: None


class HdbStack(FakeStack):
    """FakeStack whose HDB reader answers `hdb_reader` like a TorQ hdb process."""

    def __init__(self, hdb: Path, other: Path | None = None) -> None:
        super().__init__(6000)
        for name in ("rdb1", "wdb1"):
            self.named(name)["hdbdir"] = f":{hdb}"
        self.readers = {
            "hdb1": {"stackid": "6000", "hdbdir": str(hdb), "partitions": 2},
        }
        if other is not None:
            self.add(
                {**self.named("hdb1"), "procname": "hdb2", "port": self.stack_id + 4},
                listed_only=True,
            )
            self.readers["hdb2"] = {"stackid": "6000", "hdbdir": str(other), "partitions": 1}
        self.refresh_discovery()

    def call(self, endpoint: Endpoint, op: str, request: dict[str, Any]) -> dict[str, Any]:
        if op == "hdb_reader":
            self.calls.append((endpoint.port, op, request))
            if endpoint.port in self.down:
                from uqs.attach.remote import IpcError

                raise IpcError(endpoint, op, "cannot connect: Connection refused")
            return {"procname": endpoint.procname, **self.readers[endpoint.procname]}
        return super().call(endpoint, op, request)


def snapshot(*roots: Path) -> dict[str, tuple[int, int]]:
    """Every path under `roots` (and the roots themselves) with its size and mtime."""
    seen: dict[str, tuple[int, int]] = {}
    for root in roots:
        seen[str(root)] = (-1, -1) if not root.exists() else (0, 0)
        if root.is_dir():
            for p in sorted(root.rglob("*")):
                st = p.stat()
                seen[str(p)] = (st.st_size, st.st_mtime_ns)
    return seen


@pytest.fixture
def world(monkeypatch, tmp_path):
    q = q_interpreter(os.environ)
    if q is None:
        pytest.skip("no q interpreter - set $QCMD, or put q on PATH")
    attach_dir, status = tmp_path / "attachments", tmp_path / "status"
    hdb = tmp_path / "hdb"
    (hdb / "2026.09.11" / DATASET).mkdir(parents=True)
    monkeypatch.setenv(config.ATTACH_DIR_ENV, str(attach_dir))
    monkeypatch.setenv("UQF_STATUS_DIR", str(status))
    # a developer shell may hold the credential: these tests mean the unconfigured source
    monkeypatch.setenv("UQF_SOURCE_CRED_CRYPTO_MARKET_DATA", "")
    errors = _ErrorLog()
    monkeypatch.setattr(shared, "log", errors)
    stack = HdbStack(hdb)
    monkeypatch.setattr(service, "ipc_for", lambda att: stack)
    save_attachment("pb-uat", hdb)
    paths = paths_for_root(UQF_ROOT)
    return type(
        "World",
        (),
        {
            "stack": stack,
            "hdb": hdb,
            "status": status,
            "attach_dir": attach_dir,
            "paths": paths,
            "tmp": tmp_path,
            "errors": errors.messages,
            "monkeypatch": monkeypatch,
        },
    )


def save_attachment(
    name: str,
    hdb: Path,
    *,
    state: str | None = "verified",
    stack_id: int = 6000,
    pin: bool = True,
    table=None,
) -> Attachment:
    att = Attachment(
        name=name,
        stack=Stack(stack_id, "testhost", "testhost", stack_id + 1, "stp1", ("rdb1", "wdb1")),
        publisher=Publisher("pb_job", "pb_ledger1"),
        tables=(table or _tree_table(),),
        storage=Storage(hdb_dir=str(hdb) if pin else ""),
    )
    config.save(att)
    Manifest.path_for(name).unlink(missing_ok=True)
    if state is not None:
        m = Manifest.new(att, Manifest.path_for(name))
        m.set_state(state, "test")
    return att


def plan(world, *, ipc=None, port=None, fixture=False):
    return planning.plan_attached(
        world.paths,
        "pb-uat",
        WORKER,
        "v1",
        FROM,
        TO,
        ipc=ipc or world.stack,
        port=port,
        fixture=fixture,
        on_conflict="replace",
    )


def facts_of(**over):
    base = {
        "worker": WORKER,
        "dataset": DATASET,
        "source": DATASET,
        "source_version": "v1",
        "live": True,
        "custom_io": False,
        "on_conflict": "upsert",
        "width": "1D",
        "partition": "",
        "time_column": "source_time",
        "planned": 2,
        "cursor": "",
        "windows": [
            {
                "range_from": "2026.09.11D00:00:00.000000000",
                "range_to": "2026.09.12D00:00:00.000000000",
                "decision": "covered",
            },
            {
                "range_from": "2026.09.12D00:00:00.000000000",
                "range_to": "2026.09.13D00:00:00.000000000",
                "decision": "fetch",
            },
        ],
        "dates": ["2026.09.12"],
    }
    return base | over


# ------------------------------------------------------------ destination resolution


def test_the_destination_is_the_attachments_canonical_hdb_never_the_runtimes(world, monkeypatch):
    link = world.tmp / "hdb-link"
    link.symlink_to(world.hdb)
    save_attachment("pb-uat", link)
    dest = attached.resolve(world.paths, "pb-uat", WORKER)
    assert dest.hdb_dir == world.hdb.resolve(), (
        "symlinks are resolved: one destination, one identity"
    )
    assert dest.hdb_dir != world.paths.hdb_dir, "uqf's own KDBHDB is never the destination"
    assert dest.identity == attached.identity_of("testhost", 6000, world.hdb.resolve())
    assert dest.state_dir.parent == world.status / "attached"
    assert dest.state_dir.name == f"pb-uat-{dest.identity}"


def test_two_destinations_never_share_state(world):
    other = world.tmp / "other-hdb"
    other.mkdir()
    save_attachment("pb-prod", other, stack_id=7000)
    a = attached.resolve(world.paths, "pb-uat", WORKER)
    b = attached.resolve(world.paths, "pb-prod", WORKER)
    assert a.identity != b.identity and a.state_dir != b.state_dir
    same_path_other_stack = attached.identity_of("testhost", 7000, world.hdb.resolve())
    assert same_path_other_stack != a.identity, "the stack id is part of the identity"


@pytest.mark.parametrize("state", ["planned", "applied", "detached", "partial", "rolled_back"])
def test_an_attachment_that_is_not_verified_is_refused(world, state):
    save_attachment("pb-uat", world.hdb, state=None if state == "planned" else state)
    with pytest.raises(UqsError, match=f"attachment pb-uat is {state}, not verified"):
        attached.resolve(world.paths, "pb-uat", WORKER)


def test_a_detached_attachment_says_the_stack_is_not_ours(world):
    save_attachment("pb-uat", world.hdb, state="detached")
    with pytest.raises(UqsError, match="detached attachment's stack is not ours to write"):
        attached.resolve(world.paths, "pb-uat", WORKER)


def test_unpinned_storage_is_refused_not_defaulted(world):
    save_attachment("pb-uat", world.hdb, pin=False)
    with pytest.raises(
        UqsError, match="pins no HDB directory .* never falls back to uqf's own HDB"
    ):
        attached.resolve(world.paths, "pb-uat", WORKER)


def test_a_missing_hdb_directory_is_refused_and_not_created(world):
    gone = world.tmp / "not-there"
    save_attachment("pb-uat", gone)
    with pytest.raises(UqsError, match="is not a directory on this machine"):
        attached.resolve(world.paths, "pb-uat", WORKER)
    assert not gone.exists()


def test_a_dataset_the_attachment_does_not_publish_is_refused(world):
    other = parse_definition("demo_other:([]time:`timestamp$(); sym:`g#`symbol$(); px:`float$())")
    save_attachment("pb-uat", world.hdb, table=other)
    with pytest.raises(
        UqsError, match="writes crypto_market_data, which attachment pb-uat does not publish"
    ):
        attached.resolve(world.paths, "pb-uat", WORKER)


def test_schema_drift_between_attachment_and_worker_is_refused(world):
    table = _tree_table()
    drifted = type(table)(table.name, (*table.columns[:-1], ("extra", "f")))
    save_attachment("pb-uat", world.hdb, table=drifted)
    with pytest.raises(
        UqsError, match="differs between attachment pb-uat and the worker's definition"
    ):
        attached.resolve(world.paths, "pb-uat", WORKER)


def test_a_port_naming_another_stack_is_refused_and_the_stacks_own_is_fine(world):
    with pytest.raises(
        UqsError, match="--port 6051 conflicts with attachment pb-uat, which is stack 6000"
    ):
        attached.resolve(world.paths, "pb-uat", WORKER, port=6051)
    assert attached.resolve(world.paths, "pb-uat", WORKER, port=6000).stack_id == 6000


def test_fixture_is_refused_with_attach(world):
    with pytest.raises(UqsError, match="--fixture is refused with --attach"):
        attached.resolve(world.paths, "pb-uat", WORKER, fixture=True)


def test_an_unknown_attachment_and_an_unknown_worker_are_refused(world):
    with pytest.raises(UqsError, match="no attachment 'nope'"):
        attached.resolve(world.paths, "nope", WORKER)
    with pytest.raises(UqsError, match="no backfill process runs worker 'nope_backfill'"):
        attached.resolve(world.paths, "pb-uat", "nope_backfill")


@pytest.mark.parametrize("mode", [None, "run", "dry-run"])
def test_execution_modes_are_refused_for_now(mode):
    with pytest.raises(UqsError, match="--attach supports --mode validate and plan only"):
        attached.refuse_execution(mode)
    for ok in ("validate", "plan"):
        attached.refuse_execution(ok)


# ------------------------------------------------------------------ admission


def test_the_writers_and_the_readers_that_serve_the_destination_are_found(world):
    plan_ = plan_with(world, facts_of())
    assert plan_["writers"] == ["rdb1", "wdb1"]
    assert plan_["reload_targets"] == [{"procname": "hdb1", "port": 6003}]


def plan_with(world, facts, *, ipc=None):
    world.monkeypatch.setattr(planning, "evaluate", lambda *a, **k: facts)
    return plan(world, ipc=ipc)


def test_a_reader_of_another_directory_is_not_a_reload_target(world, monkeypatch):
    elsewhere = world.tmp / "elsewhere"
    elsewhere.mkdir()
    stack = HdbStack(world.hdb, other=elsewhere)
    monkeypatch.setattr(service, "ipc_for", lambda att: stack)
    plan_ = plan_with(world, facts_of(), ipc=stack)
    assert [t["procname"] for t in plan_["reload_targets"]] == ["hdb1"]
    assert plan_["readers_not_serving"] == [{"procname": "hdb2", "detail": f"serves {elsewhere}"}]


def test_an_hdb_reader_that_cannot_be_asked_blocks_the_plan(world):
    world.stack.down.add(6003)
    with pytest.raises(UqsError, match="HDB reader hdb1 .* does not answer"):
        plan_with(world, facts_of())


def test_wdb_reporting_another_hdb_makes_the_destination_ambiguous(world):
    world.stack.named("wdb1")["hdbdir"] = ":/somewhere/else"
    with pytest.raises(UqsError, match="wdb1 report another HDB directory .* ambiguous"):
        plan_with(world, facts_of())


def test_no_process_reporting_the_directory_is_a_blocker(world):
    for name in ("rdb1", "wdb1"):
        world.stack.named(name)["hdbdir"] = ""
    with pytest.raises(UqsError, match="no wdb on stack 6000 reports"):
        plan_with(world, facts_of())


def test_an_unreachable_discovery_is_a_blocker(world):
    world.stack.down.add(6001)
    with pytest.raises(UqsError, match="discovery"):
        plan_with(world, facts_of())


# ----------------------------------------------------------------------- plan


def test_the_plan_reports_everything_the_issue_asks_for(world):
    p = plan_with(world, facts_of())
    assert p["worker"] == WORKER and p["attachment"] == "pb-uat" and p["mutates"] is False
    assert p["source"] == {"name": DATASET, "live": True}
    assert p["range"]["from"] == "2026-09-11T00:00:00+00:00"
    assert p["range"]["to"] == "2026-09-13T00:00:00+00:00"
    assert p["range"]["half_open"] == "[from, to)"
    assert p["conflict_policy"] == "upsert"
    assert [w["decision"] for w in p["windows"]] == ["covered", "fetch"]
    assert p["coverage"] == {"fetch": 1, "covered": 1, "deferred": 0}
    assert p["native_partitions"] == [{"date": "2026-09-12", "exists": False, "has_dataset": False}]
    assert p["destination"]["hdb_dir"] == str(world.hdb.resolve())
    assert p["reload_targets"] == [{"procname": "hdb1", "port": 6003}]


def test_native_partitions_say_what_already_exists(world):
    p = plan_with(world, facts_of(dates=["2026.09.11", "2026.09.12"]))
    assert p["native_partitions"] == [
        {"date": "2026-09-11", "exists": True, "has_dataset": True},
        {"date": "2026-09-12", "exists": False, "has_dataset": False},
    ]


def test_a_custom_io_manager_is_refused(world):
    with pytest.raises(UqsError, match="declares its own IO manager"):
        plan_with(world, facts_of(custom_io=True))


def test_a_source_without_a_credential_is_refused_not_run_on_its_fixture(world):
    with pytest.raises(
        UqsError, match="has no credential configured: .* FIXTURE into a native HDB"
    ):
        plan_with(world, facts_of(live=False))


def test_a_plan_changes_nothing_on_disk_or_on_the_stack(world):
    before = snapshot(world.hdb, world.status, world.attach_dir, world.status / "attached")
    calls = len(world.stack.calls)
    plan_with(world, facts_of())
    assert snapshot(world.hdb, world.status, world.attach_dir, world.status / "attached") == before
    assert {op for _, op, _ in world.stack.calls[calls:]} <= {"probe", "hdb_reader"}
    assert not world.status.exists(), "not even the state directory is created by a plan"


# ------------------------------------------- the real q: coverage and the fixture refusal


def _q_plan(world, **kw):
    return planning.evaluate(
        world.paths,
        attached.resolve(world.paths, "pb-uat", WORKER),
        WORKER,
        "v1",
        FROM,
        TO,
        kw.get("on_conflict"),
    )


def test_q_evaluates_windows_and_an_unconfigured_source_is_seen_as_not_live(world):
    facts = _q_plan(world, on_conflict="replace")
    assert facts["live"] is False and facts["custom_io"] is False
    assert facts["on_conflict"] == "replace"
    assert [w["decision"] for w in facts["windows"]] == ["fetch"] * len(facts["windows"])
    assert facts["dates"][0] == "2026.09.11"
    assert not world.status.exists(), "q created no state directory either"
    with pytest.raises(UqsError, match="has no credential configured"):
        plan(world)


def test_coverage_recorded_for_this_destination_decides_its_windows_and_no_other(world):
    dest = attached.resolve(world.paths, "pb-uat", WORKER)
    other = world.tmp / "other-hdb"
    other.mkdir()
    save_attachment("pb-prod", other, stack_id=7000)
    other_dest = attached.resolve(world.paths, "pb-prod", WORKER)
    assert dest.state_dir != other_dest.state_dir
    _record_coverage(world, dest, "v1~fixture")
    here = planning.evaluate(world.paths, dest, WORKER, "v1", FROM, TO, None)
    there = planning.evaluate(world.paths, other_dest, WORKER, "v1", FROM, TO, None)
    assert [w["decision"] for w in here["windows"]][0] == "covered"
    assert {w["decision"] for w in there["windows"]} == {"fetch"}, (
        "another HDB's coverage is invisible"
    )


def _record_coverage(world, dest, version: str) -> None:
    script = f"""
\\l src/init.q
\\l src/etl/init.q
.qetl.coverage.attach[];
.qetl.coverage.stage_completion[`{DATASET};`;`$"{version}";2026.09.11D00:00;2026.09.12D00:00;5];
exit 0
"""
    path = world.tmp / "cov.q"
    path.write_text(script)
    dest.state_dir.mkdir(parents=True)
    result = subprocess.run(
        [str(q_interpreter(os.environ)), str(path), "-q"],
        cwd=UQF_ROOT,
        stdin=subprocess.DEVNULL,
        env={**os.environ, "UQF_STATUS_DIR": str(dest.state_dir), "QCMD": "q"},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# ------------------------------------------------------------------------ CLI


def invoke(*argv: str):
    return runner.invoke(cli.app, ["job", "backfill", WORKER, *argv], catch_exceptions=False)


COMMON = ["--attach", "pb-uat", "--from", "2026-09-11", "--to", "2026-09-13", "--version", "v1"]


def test_cli_refuses_execution_before_touching_anything(world):
    before = snapshot(world.hdb, world.status, world.attach_dir)
    for extra in ([], ["--mode", "run"], ["--mode", "dry-run"]):
        result = invoke(*COMMON, *extra)
        assert result.exit_code != 0
        assert "--attach supports --mode validate and plan only" in world.errors[-1]
    assert world.stack.calls == [] and snapshot(world.hdb, world.status, world.attach_dir) == before


def test_cli_refuses_a_conflicting_port(world):
    result = invoke(*COMMON, "--mode", "plan", "--port", "6051")
    assert result.exit_code != 0
    assert "conflicts with attachment pb-uat" in world.errors[-1]


def test_cli_validate_resolves_the_destination_and_opens_nothing(world):
    result = invoke(*COMMON, "--mode", "validate", "--json")
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert out["destination"]["hdb_dir"] == str(world.hdb.resolve()) and out["mutates"] is False
    assert world.stack.calls == [], "validate asks the stack nothing"


def test_cli_plan_prints_the_report_and_json_matches(world, monkeypatch):
    monkeypatch.setattr(planning, "evaluate", lambda *a, **k: facts_of())
    text = invoke(*COMMON, "--mode", "plan")
    assert text.exit_code == 0, text.output
    out = " ".join(text.output.split())
    assert "attachment pb-uat (nothing was written)" in out and "reload hdb1:6003" in out
    as_json = invoke(*COMMON, "--mode", "plan", "--json")
    assert json.loads(as_json.output)["reload_targets"] == [{"procname": "hdb1", "port": 6003}]


def test_json_without_attach_is_refused(world):
    result = invoke("--from", "2026-09-11", "--to", "2026-09-13", "--version", "v1", "--json")
    assert result.exit_code != 0 and "--json is for --attach" in world.errors[-1]


# ----------------------------------------------------- the hdb_reader operation, on a real q


def test_hdb_reader_reports_the_directory_a_real_q_loaded(world):
    """The operation a stack's HDB reader answers: the loaded directory and what it maps."""
    from uqs.attach.remote import source

    hdb = world.tmp / "real-hdb"
    hdb.mkdir()
    script = world.tmp / "reader.q"
    script.write_text(
        f'system"cd {hdb}";\n'
        "t:([] sym:`a`b; px:1 2f);\n"
        ".Q.dpft[`:.;2026.09.11;`sym;`t];\n"
        ".Q.dpft[`:.;2026.09.12;`sym;`t];\n"
        f'system"cd {world.tmp}";\n'
        f"\\l {hdb}\n"
        f"f:{source('hdb_reader')};\n"
        '-1 f "{}";\n'
        "exit 0\n"
    )
    result = subprocess.run(
        [str(q_interpreter(os.environ)), str(script), "-q"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    answer = json.loads(result.stdout.strip().splitlines()[-1])
    assert Path(answer["hdbdir"]).resolve() == hdb.resolve()
    assert answer["partitions"] == 2 and answer["lastpartition"] == "2026.09.12"
    assert answer["stackid"] == "" and answer["procname"] == ""
