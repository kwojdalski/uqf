"""Plan and preflight for attaching a job to an existing stack (#1424).

Everything here runs against `FakeStack` - no q, no ports - so it runs in CI.
The same questions are put to a real TorQ 5.2.12 stack in test_attach_stack.py.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest
from attach_support import LEDGER_COLUMNS, LEDGER_Q, SORT_DEFAULT, FakeStack, make_bundle
from typer.testing import CliRunner

from uqs import cli
from uqs.attach import config, facts, jobs, plan, preflight, service, topology
from uqs.attach.findings import blockers
from uqs.attach.schema import SchemaError, TableSpec, differences, parse_definition
from uqs.cli import shared
from uqs.paths import repo_root

ROOT = repo_root()
LEDGER = TableSpec("piggybank_general_ledger", tuple((n, c) for n, c in LEDGER_COLUMNS))


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    return make_bundle(tmp_path / "piggybank")


def attachment(bundle: Path, stack_id: int = 6000, **kw) -> config.Attachment:
    """Runtime-only unless a test is about persisting: the fake stack has no
    schema file, which would otherwise be a blocker of its own."""
    kw.setdefault("runtime_only", True)
    return service.create("pb-uat", "pb_general_ledger", bundle, stack_id, ROOT, **kw)


def codes(findings, level: str | None = None) -> set[str]:
    return {f.code for f in findings if level is None or f.level == level}


def host(open_ports: tuple[int, ...] = ()) -> preflight.Host:
    """A host where only `open_ports` answer and every name is local."""
    return preflight.Host(lambda h, port: port in open_ports, ("localhost", "testhost"))


def run(att: config.Attachment, fake: FakeStack, open_ports: tuple[int, ...] = ()) -> plan.Plan:
    facts = preflight.Facts(host=host(open_ports))
    return plan.build(att, fake, ROOT, facts)


# ------------------------------------------------------------------ schema


def test_a_table_definition_reads_into_ordered_typed_columns():
    spec = parse_definition(
        "t_x:([] time:`timestamp$(); sym:`g#`symbol$(); px:`float$(); n:`long$(); extra:`char$())"
    )
    assert spec.columns == (
        ("time", "p"),
        ("sym", "s"),
        ("px", "f"),
        ("n", "j"),
        ("extra", "c"),
    )


def test_a_definition_round_trips_through_its_own_q_text():
    assert parse_definition(LEDGER.definition()) == LEDGER


@pytest.mark.parametrize("ending", ["", ";", "; ", ";\t", " ;"])
def test_a_statement_terminator_after_the_definition_is_accepted(ending):
    """`t:([] ...);` is a q statement the same as `t:([] ...)` (#1430)."""
    assert parse_definition(LEDGER.definition() + ending) == LEDGER


@pytest.mark.parametrize(
    "suffix",
    [";;", "; delete from `t", ";exit 0", " 1", "; t:([] time:`timestamp$())"],
)
def test_anything_after_the_terminator_is_still_refused(suffix):
    """Refused whichever check catches it: the head when the line no longer ends
    in `)` or `);`, the column reader when a second definition follows."""
    with pytest.raises(SchemaError):
        parse_definition(LEDGER.definition() + suffix)


@pytest.mark.parametrize(
    ("line", "why"),
    [
        ("t:([k:`symbol$()] time:`timestamp$())", "unkeyed"),
        ("t:([] time:`timestamp$(); px:enlist 1f)", "cannot read column"),
        ("t:([] sym:`symbol$(); time:`timestamp$())", "first column must be `time`"),
        ("t:([] time:`timestamp$(); a:`float$(); a:`long$())", "defined twice"),
        ("t:([] time:`timestamp$(); a:`bogus$())", "unknown q type"),
        ("t:([] time:`date$())", "first column must be `time`"),
    ],
)
def test_a_definition_the_plant_cannot_take_is_refused_naming_why(line, why):
    with pytest.raises(SchemaError, match=why):
        parse_definition(line)


def test_every_table_this_trees_plant_defines_that_an_attachment_could_take_parses():
    """The parser is meant for the shape plant tables really have; if the tree
    grows a form it cannot read, this says which table."""
    from uqs.paths import TABLES_FILE

    text = (ROOT / TABLES_FILE).read_text()
    lines = [ln for ln in text.splitlines() if jobs._TABLE_LINE.match(ln)]
    assert lines, "plant_tables.q defines tables"
    nested = jobs._nested(text, "plant_tables.q")
    refused = {}
    for line in lines:
        matched = jobs._TABLE_LINE.match(line)
        assert matched is not None
        name = matched.group(1)
        try:
            parse_definition(line, nested.get(name, {}))
        except SchemaError as exc:
            refused[name] = str(exc)
            assert (
                "cannot read column" in str(exc)
                or "first column" in str(exc)
                or ("holding any value" in str(exc))
            ), exc
    # config_change's old/new hold any value on purpose; the wdb cannot persist that safely
    assert [n for n, why in refused.items() if "holding any value" in why] == ["config_change"]


def test_differences_name_missing_extra_retyped_and_reordered_columns():
    assert differences(LEDGER, LEDGER_COLUMNS) == []
    assert differences(LEDGER, [*LEDGER_COLUMNS[:-1]]) == ["missing column note"]
    assert differences(LEDGER, [*LEDGER_COLUMNS, ["z", "j"]]) == ["unexpected column z"]
    retyped = [*LEDGER_COLUMNS[:3], ["amount", "j"], LEDGER_COLUMNS[4]]
    assert differences(LEDGER, retyped) == ["column amount is type 'j', expected 'f'"]
    swapped = [LEDGER_COLUMNS[0], LEDGER_COLUMNS[2], LEDGER_COLUMNS[1], *LEDGER_COLUMNS[3:]]
    assert "different order" in differences(LEDGER, swapped)[0]


# -------------------------------------------------------------------- jobs


def test_the_job_and_its_table_are_read_from_the_bundle_folder(bundle):
    spec = jobs.from_bundle("pb_general_ledger", bundle)
    assert (spec.procname, spec.external) == ("pb_ledger1", ())
    assert spec.tables == (LEDGER,)


def test_a_bundle_whose_tables_q_ends_its_definition_with_a_semicolon_resolves(tmp_path):
    """The bundle that failed in #1430: resolved from text alone, no stack."""
    folder = make_bundle(tmp_path / "piggybank", ledger_q=LEDGER_Q + ";")
    spec = jobs.from_bundle("pb_general_ledger", folder)
    assert spec.tables == (LEDGER,)
    assert spec.origin["bundle"] == "piggybank"


def test_a_job_the_bundle_does_not_declare_is_refused_naming_what_it_has(bundle):
    with pytest.raises(config.AttachmentError, match=r"no streaming job 'nope'.*pb_general_ledger"):
        jobs.from_bundle("nope", bundle)


def test_a_published_table_the_bundle_does_not_define_is_external(tmp_path):
    folder = make_bundle(tmp_path / "pb2")
    (folder / "pb_general_ledger.q").write_text(
        ".qetl.job.stream.define[`pb_general_ledger;`procname`subscribe_to`publishes!"
        "(`pb_ledger1;`symbol$();`piggybank_general_ledger`quote)];\n"
    )
    spec = jobs.from_bundle("pb_general_ledger", folder)
    assert [t.name for t in spec.tables] == ["piggybank_general_ledger"]
    assert spec.external == ("quote",)


# ------------------------------------------------------------------ config


def test_the_stack_id_has_no_default_and_discovery_is_stored_once(bundle):
    att = attachment(bundle, 6000)
    assert (att.stack.id, att.stack.discovery_port) == (6000, 6001)
    other = attachment(bundle, 6000, host="otherhost", discovery="otherhost:7001")
    assert (other.stack.discovery_host, other.stack.discovery_port) == ("otherhost", 7001)
    with pytest.raises(config.AttachmentError, match="not a base port"):
        attachment(bundle, 80)
    with pytest.raises(config.AttachmentError, match="HOST:PORT"):
        attachment(bundle, 6000, discovery="6001")


class _Recording(FakeStack):
    """The fake stack, also noting the host every call was addressed to."""

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.hosts: list[tuple[str, str]] = []

    def call(self, endpoint, op, request):
        self.hosts.append((endpoint.procname, endpoint.host))
        return super().call(endpoint, op, request)


def test_a_discovery_host_other_than_the_stack_host_is_refused_not_ignored(bundle):
    """#1440: `--discovery uat-box:6001` without `--host uat-box` was stored, shown,
    and then ignored - discovery and every process were asked on localhost."""
    with pytest.raises(
        config.AttachmentError,
        match=r"--discovery uat-box\.example:6001 is not on "
        r"the --host machine \(localhost\).*Give --host uat-box\.example",
    ):
        attachment(bundle, 6000, discovery="uat-box.example:6001")


def test_discovery_is_asked_at_the_address_the_attachment_records(bundle):
    fake = _Recording(6000)
    att = attachment(bundle, 6000, host="UAT-box.example", discovery="uat-box.example:6001")
    obs, found = topology.observe(att, fake)
    assert not found and obs.tickerplant is not None
    assert fake.hosts[0] == ("discovery", "uat-box.example"), "the host --discovery gave"
    # #1651: every process at the host discovery lists for it, not --host
    assert {h for _, h in fake.hosts[1:]} == {"testhost"}, "processes: as discovery lists them"


def test_a_process_listed_on_loopback_is_asked_at_the_stacks_host(bundle):
    """The stack's own loopback is the stack's machine, not this one (#1651)."""
    stack = attachment(bundle, 6000, host="uat-box.example").stack
    assert topology.host_of(":localhost:6002", stack) == "uat-box.example"
    assert topology.host_of(":127.0.0.1:6002", stack) == "uat-box.example"
    assert topology.host_of(":uat-tp.internal:6002", stack) == "uat-tp.internal"
    assert topology.host_of(":uat-tp.internal:6002:admin:pw", stack) == "uat-tp.internal"


def test_two_names_for_this_machine_are_one_host(bundle):
    fake = _Recording(6000)
    att = attachment(bundle, 6000, host="localhost", discovery="127.0.0.1:6001")
    topology.observe(att, fake)
    assert fake.hosts[0] == ("discovery", "127.0.0.1")


def test_the_config_round_trips_and_a_foreign_schema_is_refused(bundle, tmp_path):
    att = attachment(bundle, consumers=("rdb1",), password_env="STACK_PW")
    config.save(att, tmp_path)
    assert config.load("pb-uat", tmp_path) == att
    path = tmp_path / "pb-uat" / config.CONFIG_FILE
    data = json.loads(path.read_text())
    assert "STACK_PW" in path.read_text() and "hunter2" not in path.read_text()
    path.write_text(json.dumps({**data, "schema": 99}))
    with pytest.raises(config.AttachmentError, match="schema 99"):
        config.load("pb-uat", tmp_path)


def test_a_missing_password_variable_is_refused_not_defaulted(bundle, monkeypatch):
    monkeypatch.delenv("STACK_PW", raising=False)
    att = attachment(bundle, password_env="STACK_PW")
    with pytest.raises(config.AttachmentError, match="STACK_PW is not set"):
        att.stack.password()
    monkeypatch.setenv("STACK_PW", "s3cret")
    assert att.stack.password() == "s3cret"


def test_attachment_names_are_checked_and_listed(tmp_path, bundle):
    with pytest.raises(config.AttachmentError, match="not an attachment name"):
        config.directory("../escape", tmp_path)
    config.save(attachment(bundle), tmp_path)
    assert config.names(tmp_path) == ["pb-uat"]


def test_two_operations_on_one_attachment_cannot_overlap(tmp_path):
    with config.locked("pb-uat", tmp_path):
        with pytest.raises(config.AttachmentError, match="another uqs operation"):
            with config.locked("pb-uat", tmp_path):
                pass
    with config.locked("pb-uat", tmp_path):
        pass


# -------------------------------------------------------------------- plan


def test_a_plan_on_a_healthy_stack_names_every_component_and_change_and_has_no_blocker(bundle):
    p = run(attachment(bundle), FakeStack(6000))
    assert p.ok, p.findings
    roles = {c["procname"]: c["role"] for c in p.components()}
    assert roles["discovery1"] == "discovery" and roles["stp1"] == "tickerplant"
    assert roles["rdb1"] == "rdb" and roles["wdb1"] == "wdb"
    assert roles["hdb1"] == "other"
    steps = [(s["process"], s["action"]) for s in p.changes()]
    assert steps[:3] == [
        ("stp1", "register table"),
        ("rdb1", "subscribe and create table"),
        ("wdb1", "subscribe and create table"),
    ]
    assert all(not s["needs_restart"] for s in p.changes())
    port = p.publisher_port.port
    assert port is not None and port > 6000 and p.publisher_port.provisional


def test_planning_asks_only_the_read_only_probe(bundle):
    fake = FakeStack(6000)
    run(attachment(bundle), fake)
    assert fake.ops() == {"probe"}
    assert all(req["tables"] == ["piggybank_general_ledger"] for _, _, req in fake.calls)


def test_a_table_already_registered_with_the_same_shape_is_adopted_not_created(bundle):
    fake = FakeStack(6000)
    fake.named("stp1")["schemas"] = {"piggybank_general_ledger": LEDGER_COLUMNS}
    fake.named("stp1")["published"] = ["quote", "trade", "piggybank_general_ledger"]
    p = run(attachment(bundle), fake)
    assert p.ok and "WILL_ADOPT" in codes(p.findings, "info")
    assert p.changes()[0]["action"] == "adopt existing table" and not p.changes()[0]["creates"]


# ----------------------------------------------------- the 6051 vs 6001 incident


def test_discovery_at_the_wrong_stack_is_refused_as_that_stacks_not_ours(bundle):
    """The incident: a discovery answered, on the runtime's default 6051, for a
    different stack than the fleet at 6000. Every process the saved discovery
    leads to must say it belongs to the stack the attachment names."""
    wrong = FakeStack(6050)
    att = attachment(bundle, 6000, host="testhost", discovery="testhost:6051")
    p = run(att, wrong)
    assert not p.ok
    assert "STACK_MISMATCH" in codes(p.findings, "blocker")
    assert any("belongs to stack '6050'" in f.message for f in p.findings)


def test_no_discovery_where_the_attachment_says_is_a_refusal_not_a_fallback(bundle):
    fake = FakeStack(6000)
    fake.down.add(6001)
    p = run(attachment(bundle), fake)
    assert codes(p.findings, "blocker") == {"DISCOVERY_UNREACHABLE"}
    assert fake.ops() == {"probe"} and {port for port, _, _ in fake.calls} == {6001}, (
        "nothing else is tried when discovery is unreachable"
    )


# ---------------------------------------------------------------- preflight


def test_an_unavailable_consumer_is_refused(bundle):
    fake = FakeStack(6000)
    fake.down.add(6005)
    p = run(attachment(bundle), fake)
    assert any(f.code == "UNREACHABLE" and f.component == "wdb1" for f in blockers(p.findings))


def test_a_consumer_left_out_by_name_is_a_warning_when_up_and_no_blocker_when_down(bundle):
    fake = FakeStack(6000)
    fake.down.add(6005)
    p = run(attachment(bundle, consumers=("rdb1",)), fake)
    assert p.ok, p.findings
    fake = FakeStack(6000)
    p = run(attachment(bundle, consumers=("rdb1",)), fake)
    assert p.ok and "UNMANAGED_CONSUMER" in codes(p.findings, "warning")


def test_an_unknown_consumer_name_is_refused(bundle):
    p = run(attachment(bundle, consumers=("rdb9",)), FakeStack(6000))
    assert "UNKNOWN_CONSUMER" in codes(p.findings, "blocker")


@pytest.mark.parametrize("version", ["5.2.11", "5.3.0", "4.0.1"])
def test_an_unsupported_torq_version_is_refused(bundle, version):
    p = run(attachment(bundle), FakeStack(6000, torq=version))
    assert "TORQ_VERSION" in codes(p.findings, "blocker")
    assert any(version in f.message and "5.2.12" in f.message for f in p.findings)


def test_the_tested_version_is_accepted(bundle):
    assert "TORQ_VERSION" not in codes(run(attachment(bundle), FakeStack(6000)).findings)


def test_a_port_another_process_holds_in_the_stack_is_a_conflict(bundle):
    att = attachment(bundle)
    port = plan.publisher_port(att, ROOT).port
    assert port is not None
    fake = FakeStack(6000)
    squatter = {"procname": "squatter1", "proctype": "feed", "hpup": f":testhost:{port}"}
    fake.named("discovery1")["services"].append(squatter)
    p = run(att, fake)
    assert any(f.code == "PORT_CONFLICT" and "squatter1" in f.message for f in p.findings)


def _squat(bundle, hpup_host: str, facts_host: preflight.Host | None = None) -> set[str]:
    """Blocker codes when `squatter1` holds the publisher's port at `hpup_host`."""
    att = attachment(bundle)
    port = plan.publisher_port(att, ROOT).port
    assert port is not None
    fake = FakeStack(6000)
    fake.named("discovery1")["services"].append(
        {"procname": "squatter1", "proctype": "feed", "hpup": f":{hpup_host}:{port}"}
    )
    p = plan.build(att, fake, ROOT, preflight.Facts(host=facts_host or host()))
    return codes(p.findings, "blocker")


def test_the_publishers_port_held_on_another_host_is_no_conflict(bundle):
    """The same port on another machine never collides (#1686). 192.0.2.0/24 is
    TEST-NET-1: an address, so it resolves, that no machine here owns."""
    assert "PORT_CONFLICT" not in _squat(bundle, "192.0.2.77")


@pytest.mark.parametrize("name", ["TESTHOST", "TestHost.example.com", "localhost", "127.0.0.1", ""])
def test_the_publishers_port_held_on_its_own_host_is_a_conflict_however_named(bundle, name):
    """By name in any case (q's .z.h is lower-case, gethostname() on macOS is not,
    #726), by loopback, or with no host at all - all this machine (#1686)."""
    assert "PORT_CONFLICT" in _squat(bundle, name)


def test_the_machines_real_hostname_in_upper_case_is_a_conflict(bundle):
    real = preflight.Host(lambda h, port: False)
    assert "PORT_CONFLICT" in _squat(bundle, socket.gethostname().upper(), real)


def test_a_host_that_does_not_resolve_errs_to_a_conflict(bundle):
    """An unknown host might be this machine, so the port check refuses (#1686)."""
    assert "PORT_CONFLICT" in _squat(bundle, "no-such-host.invalid")


def test_a_port_in_use_by_something_unknown_is_a_conflict_and_the_publisher_itself_is_running(
    bundle,
):
    att = attachment(bundle)
    port = plan.publisher_port(att, ROOT).port
    assert port is not None
    p = run(att, FakeStack(6000), open_ports=(port,))
    assert "PORT_CONFLICT" in codes(p.findings, "blocker")
    fake = FakeStack(6000)
    fake.named("discovery1")["services"].append(
        {"procname": "pb_ledger1", "proctype": "feed", "hpup": f":testhost:{port}"}
    )
    p = run(att, fake, open_ports=(port,))
    assert "PUBLISHER_RUNNING" in codes(p.findings, "blocker")


def test_a_shared_log_logging_mode_is_refused_with_why(bundle):
    for mode in ("singular", "periodic", "custom"):
        fake = FakeStack(6000)
        fake.named("stp1")["multilog"] = mode
        p = run(attachment(bundle), fake)
        assert any(f.code == "LOGGING_MODE" and mode in f.message for f in p.findings)
    fake = FakeStack(6000)
    fake.named("stp1")["multilog"] = "tabular"
    assert "LOGGING_MODE" not in codes(run(attachment(bundle), fake).findings)


def test_a_tickerplant_that_writes_no_logs_or_is_chained_is_refused(bundle):
    fake = FakeStack(6000)
    fake.named("stp1")["createlogs"] = False
    fake.named("stp1")["chained"] = True
    assert {"LOGS_DISABLED", "CHAINED_TP"} <= codes(run(attachment(bundle), fake).findings)


def test_an_incompatible_existing_table_is_refused_on_the_tickerplant_and_on_a_consumer(bundle):
    fake = FakeStack(6000)
    wrong = [*LEDGER_COLUMNS[:3], ["amount", "j"], LEDGER_COLUMNS[4]]
    fake.named("stp1")["schemas"] = {"piggybank_general_ledger": wrong}
    fake.named("stp1")["published"] = ["piggybank_general_ledger"]
    fake.named("rdb1")["schemas"] = {"piggybank_general_ledger": LEDGER_COLUMNS[:2]}
    p = run(attachment(bundle), fake)
    bad = [f for f in blockers(p.findings) if f.code == "SCHEMA_INCOMPATIBLE"]
    assert {f.component for f in bad} == {"stp1", "rdb1"}
    assert "column amount is type 'j'" in bad[0].message or "column amount" in bad[1].message


def test_a_table_the_tickerplant_defines_but_does_not_publish_is_refused(bundle):
    fake = FakeStack(6000)
    fake.named("stp1")["schemas"] = {"piggybank_general_ledger": LEDGER_COLUMNS}
    assert "NOT_PUBLISHED" in codes(run(attachment(bundle), fake).findings, "blocker")


def test_a_consumer_that_filters_or_ignores_the_table_is_refused(bundle):
    fake = FakeStack(6000)
    fake.named("rdb1")["subscribeto"] = ["trade", "quote"]
    fake.named("wdb1")["ignorelist"] = ["heartbeat", "piggybank_general_ledger"]
    got = codes(run(attachment(bundle), fake).findings, "blocker")
    assert {"CONSUMER_FILTERED", "CONSUMER_IGNORES"} <= got
    fake = FakeStack(6000)
    fake.named("rdb1")["subfiltered"] = True
    assert "CONSUMER_FILTERED" in codes(run(attachment(bundle), fake).findings, "blocker")


def test_a_wdb_that_cannot_sort_the_table_is_refused_before_end_of_day(bundle, tmp_path):
    fake = FakeStack(6000)
    fake.named("wdb1")["sortrows"] = [
        {"tabname": "default", "att": "p", "col": "sector", "sort": True}
    ]
    p = run(attachment(bundle), fake)
    assert any(f.code == "SORT_CONFIG" and "sector" in f.message for f in p.findings)
    fake.named("wdb1")["sortrows"] = []
    assert "SORT_CONFIG" in codes(run(attachment(bundle), fake).findings, "blocker")
    fake.named("wdb1")["sortrows"] = [
        {"tabname": "piggybank_general_ledger", "att": "p", "col": "account", "sort": True},
        *SORT_DEFAULT,
    ]
    assert "SORT_CONFIG" not in codes(run(attachment(bundle), fake).findings)


def test_pinned_storage_that_the_stack_does_not_report_is_refused(bundle):
    att = attachment(bundle, storage=config.Storage(tplogdir="/elsewhere/tplogs"))
    p = run(att, FakeStack(6000))
    assert any(
        f.code == "STORAGE_MISMATCH" and "/elsewhere/tplogs" in f.message for f in p.findings
    )
    same = attachment(bundle, storage=config.Storage(tplogdir="/data/tplogs"))
    assert "STORAGE_MISMATCH" not in codes(run(same, FakeStack(6000)).findings)


def test_a_plan_pins_what_it_observed_and_a_later_move_is_refused(bundle, tmp_path):
    att = attachment(bundle)
    fake = FakeStack(6000)
    planned = run(att, fake)
    saved = service.save_planned(att, planned, tmp_path)
    kept = config.load("pb-uat", tmp_path)
    assert kept.storage.tplogdir == "/data/tplogs"
    assert kept.storage.wdb_savedir == "/data/wdbhdb" and kept.storage.hdb_dir == "/data/hdb"
    assert saved.is_file()
    fake.named("stp1")["tplogdir"] = "/uqf-default/tplogs"
    again = run(kept, fake)
    assert "STORAGE_MISMATCH" in codes(again.findings, "blocker")


# ------------------------------------------------------------ persistence


def test_persisting_needs_a_writable_local_non_generated_schema_file(bundle, tmp_path):
    schema = tmp_path / "database.q"
    schema.write_text("trade:([] time:`timestamp$())\n")
    fake = FakeStack(6000, schema_file=str(schema))
    p = run(attachment(bundle, runtime_only=False), fake)
    assert p.ok and "WILL_PERSIST" in codes(p.findings, "info")
    schema.write_text("/ GENERATED by uqs - do not edit\ntrade:([] time:`timestamp$())\n")
    p = run(attachment(bundle, runtime_only=False), fake)
    assert any(f.code == "PERSIST_UNAVAILABLE" and "generated" in f.message for f in p.findings)
    schema.chmod(0o444)
    p = run(attachment(bundle, runtime_only=False), FakeStack(6000, schema_file=str(schema)))
    assert "PERSIST_UNAVAILABLE" in codes(p.findings, "blocker")


def test_no_schema_file_or_a_remote_one_cannot_be_persisted_to(bundle, tmp_path):
    p = run(attachment(bundle, runtime_only=False), FakeStack(6000))
    assert any("-schemafile" in f.message for f in blockers(p.findings))
    schema = tmp_path / "database.q"
    schema.write_text("x\n")
    fake = FakeStack(6000, schema_file=str(schema))
    facts = preflight.Facts(host=preflight.Host(lambda h, p: False, ("localhost",)))
    remote = plan.build(attachment(bundle, host="otherbox", runtime_only=False), fake, ROOT, facts)
    # the path exists HERE, and is still not read: it names a file on otherbox (#1651)
    (refused,) = [f for f in remote.findings if f.code == "PERSIST_UNAVAILABLE"]
    assert "the schema file is on otherbox; this machine cannot edit it" in refused.message
    for option in ("--remote-schema-file", "--runtime-only", "run uqs on the stack's machine"):
        assert option in refused.action


def test_runtime_only_is_a_warning_that_says_a_restart_drops_the_table(bundle):
    p = run(attachment(bundle), FakeStack(6000))
    assert p.ok and "RUNTIME_ONLY" in codes(p.findings, "warning")
    assert any("DROPS the registration" in f.message for f in p.findings)


def test_a_chained_tickerplant_and_the_hdb_history_are_named_not_hidden(bundle):
    fake = FakeStack(6000)
    fake.add(
        {
            **fake.named("rdb1"),
            "procname": "sctp1",
            "proctype": "segmentedchainedtickerplant",
            "port": 6015,
        }
    )
    fake.refresh_discovery()
    p = run(attachment(bundle), fake)
    assert {"CHAINED_UNMANAGED", "HDB_HISTORY"} <= codes(p.findings, "warning")
    assert any(
        c["procname"] == "sctp1" and c["effect"].startswith("unchanged") for c in p.components()
    )


# --------------------------------------------------------------------- CLI


class _ErrorLog:
    """What `_die` was handed: it logs through loguru, so CliRunner's output is
    empty for a refusal and asserting on it would pass vacuously."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def error(self, message: str, *args) -> None:
        self.messages.append(str(message).format(*args))

    def __getattr__(self, _name: str):
        return lambda *a, **kw: None


@pytest.fixture
def cli_stack(monkeypatch, tmp_path):
    fake = FakeStack(6000)
    monkeypatch.setenv(config.ATTACH_DIR_ENV, str(tmp_path / "attachments"))
    monkeypatch.setattr(service, "ipc_for", lambda att: fake)
    monkeypatch.setattr(facts, "_tcp_open", lambda host, port: False)
    errors = _ErrorLog()
    monkeypatch.setattr(shared, "log", errors)
    fake.errors = errors.messages
    return fake


def invoke(*argv: str):
    return CliRunner().invoke(cli.app, ["job", "attachment", "add", *argv], catch_exceptions=False)


def test_a_new_attachment_needs_a_job_and_a_stack_id(cli_stack):
    result = invoke("pb-uat", "--dry-run")
    assert result.exit_code != 0
    assert any("needs --job and --stack-id" in m for m in cli_stack.errors)


def test_a_dry_run_prints_the_plan_as_json_and_saves_nothing(cli_stack, bundle, tmp_path):
    result = invoke(
        "pb-uat", "--job", "pb_general_ledger", "--bundle", str(bundle),
        "--stack-id", "6000", "--runtime-only", "--json", "--dry-run",
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)["plan"]
    assert data["stack"]["id"] == 6000 and data["stack"]["discovery"] == "localhost:6001"
    assert data["ok"] is True and data["changes"][0]["action"] == "register table"
    assert not (tmp_path / "attachments").exists()


def test_executing_saves_the_config_with_the_observed_storage(cli_stack, bundle, tmp_path):
    args = ["--job", "pb_general_ledger", "--bundle", str(bundle), "--stack-id", "6000"]
    assert invoke("pb-uat", *args, "--runtime-only", "--yes", "--no-start").exit_code == 0
    saved = json.loads((tmp_path / "attachments" / "pb-uat" / config.CONFIG_FILE).read_text())
    assert saved["stack"]["discovery_port"] == 6001
    assert saved["storage"]["tplogdir"] == "/data/tplogs"
    assert invoke("pb-uat", "--dry-run", "--json").exit_code == 0, "reuses the saved identity"
    assert invoke("pb-uat", *args, "--runtime-only", "--dry-run").exit_code == 0, "the same"


def test_a_dry_run_exits_nonzero_on_a_blocker_and_zero_when_clean(cli_stack, bundle):
    args = ["--job", "pb_general_ledger", "--bundle", str(bundle), "--stack-id", "6000"]
    assert invoke("pb-uat", *args, "--runtime-only", "--dry-run").exit_code == 0
    cli_stack.named("stp1")["multilog"] = "singular"
    result = invoke("pb-uat", *args, "--runtime-only", "--dry-run", "--json")
    assert result.exit_code == 1
    assert "LOGGING_MODE" in {f["code"] for f in json.loads(result.output)["plan"]["findings"]}


def test_a_detach_of_an_unknown_attachment_says_so(cli_stack):
    result = CliRunner().invoke(
        cli.app, ["job", "attachment", "remove", "nope"], catch_exceptions=False
    )
    assert result.exit_code != 0 and any("no attachment 'nope'" in m for m in cli_stack.errors)


def test_the_cli_refuses_a_discovery_on_another_machine_before_asking_anything(cli_stack, bundle):
    result = invoke(
        "pb-uat", "--job", "pb_general_ledger", "--bundle", str(bundle),
        "--stack-id", "6000", "--discovery", "uat-box.example:6001", "--dry-run",
    )  # fmt: skip
    assert result.exit_code != 0
    assert any("is not on the --host machine (localhost)" in m for m in cli_stack.errors)
    assert cli_stack.calls == []
