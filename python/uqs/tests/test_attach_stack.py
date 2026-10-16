"""The attach workflow against a REAL TorQ 5.2.12 stack (#1424).

Opt-in: UQS_ATTACH_STACK_TEST=1 (or `python3 scripts/test.py attach-stack`).
The stack is the starter pack as it ships - the `torq` runtime - on a port span
nothing else uses, with its own data directory and its own copy of the pack.
The first supported case is `piggybank_general_ledger`, from a fixture bundle:
the real bundle is not in this repository.

This file holds the read-only half (plan, validate). Applying and detaching are
in test_attach_apply.py and test_attach_detach.py.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest
from attach_stack_support import CORE, IsolatedStack, requires_stack
from attach_support import LEDGER_COLUMNS, make_bundle

from uqs.attach import plan, preflight, service
from uqs.paths import repo_root

pytestmark = requires_stack()
ROOT = repo_root()


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    s = IsolatedStack(tmp_path_factory.mktemp("attach-stack"))
    try:
        s.start(*CORE)
        yield s
    finally:
        s.teardown()


@pytest.fixture
def bundle(tmp_path):
    return make_bundle(tmp_path / "piggybank")


def attachment(stack, bundle, **kw):
    kw.setdefault("runtime_only", False)
    kw.setdefault("discovery", f"127.0.0.1:{stack.port + 1}")
    return service.create(
        "pb-uat", "pb_general_ledger", bundle, stack.port, ROOT, host="127.0.0.1", **kw
    )


def snapshot(stack) -> dict:
    """The tickerplant's own state, read directly: tables, pub/sub tables,
    open logs, subscriber counts - what a plan must leave exactly as it was."""
    from uqs.attach.remote import source  # noqa: F401 - the loader is exercised below

    probe = stack.ipc().call(stack.endpoint("stp1"), "probe", {"tables": ["trade", "quote"]})
    return {
        k: probe[k]
        for k in ("tables", "published", "subscribers", "logs", "msgcount", "owned", "dldir")
    }


def test_a_plan_identifies_the_existing_stack_and_every_affected_component(stack, bundle):
    before = snapshot(stack)
    p = plan.build(attachment(stack, bundle), stack.ipc(), ROOT)
    assert p.ok, [f for f in p.findings if f.level == "blocker"]
    by_name = {c["procname"]: c for c in p.components()}
    assert by_name["stp1"]["role"] == "tickerplant" and by_name["stp1"]["up"]
    assert by_name["rdb1"]["role"] == "rdb" and by_name["wdb1"]["role"] == "wdb"
    assert by_name["discovery1"]["role"] == "discovery"
    assert by_name["hdb1"]["role"] == "other" and by_name["sort1"]["role"] == "other"
    assert [(c["process"], c["table"]) for c in p.changes() if c["creates"]][:3] == [
        ("stp1", "piggybank_general_ledger"),
        ("rdb1", "piggybank_general_ledger"),
        ("wdb1", "piggybank_general_ledger"),
    ]
    tp = p.observation.tickerplant
    assert tp is not None and tp.probe is not None and tp.probe["torq"] == "5.2.12"
    assert snapshot(stack) == before, "planning changed nothing"
    assert "piggybank_general_ledger" not in before["tables"]


def test_a_plan_pins_the_storage_the_stack_really_reports(stack, bundle, tmp_path):
    att = attachment(stack, bundle)
    p = plan.build(att, stack.ipc(), ROOT)
    pinned = service.observed_storage(att, p.observation)
    assert Path(pinned.tplogdir) == stack.tplogs
    assert Path(pinned.schema_file) == stack.schema_file
    assert Path(pinned.wdb_savedir) == stack.wdb_dir and Path(pinned.hdb_dir) == stack.hdb_dir


def test_discovery_of_another_stack_is_refused_even_though_it_answers(stack, bundle):
    """The incident, for real: the fleet is at stack.port, an attachment that
    believes it is at another base port reaches this discovery and must be told
    it belongs to a different stack - not be accepted because something answered."""
    att = attachment(stack, bundle)
    wrong = service.create(
        "pb-uat", "pb_general_ledger", bundle, stack.port + 50, ROOT, host="127.0.0.1",
        discovery=f"127.0.0.1:{stack.port + 1}", runtime_only=True,
    )  # fmt: skip
    p = plan.build(wrong, stack.ipc(), ROOT)
    assert not p.ok
    mismatches = [f for f in p.findings if f.code == "STACK_MISMATCH"]
    assert mismatches and f"belongs to stack '{stack.port}'" in mismatches[0].message
    assert plan.build(att, stack.ipc(), ROOT).ok


def test_a_discovery_nobody_listens_on_is_a_refusal(stack, bundle):
    att = attachment(stack, bundle, discovery=f"127.0.0.1:{stack.port + 70}")
    p = plan.build(att, stack.ipc(), ROOT)
    assert [f.code for f in p.findings if f.level == "blocker"] == ["DISCOVERY_UNREACHABLE"]
    assert "Connection refused" in p.findings[0].message


def test_a_credential_the_stack_does_not_accept_is_an_unreachable_endpoint(
    stack, bundle, monkeypatch
):
    att = attachment(stack, bundle, user="nobody", password_env="ATTACH_TEST_PW")
    monkeypatch.setenv("ATTACH_TEST_PW", "wrong")
    p = plan.build(att, service.ipc_for(att), ROOT)
    assert not p.ok and "DISCOVERY_UNREACHABLE" in {f.code for f in p.findings}


def test_a_version_the_procedure_was_not_tested_on_is_refused(stack, bundle, monkeypatch):
    monkeypatch.setattr(preflight, "SUPPORTED_TORQ", ("5.2.99",))
    p = plan.build(attachment(stack, bundle), stack.ipc(), ROOT)
    refused = {f.component for f in p.findings if f.code == "TORQ_VERSION"}
    assert {"discovery1", "stp1", "rdb1", "wdb1"} <= refused


def test_a_port_something_else_already_holds_is_a_conflict(stack, bundle):
    att = attachment(stack, bundle)
    port = plan.publisher_port(att, ROOT).port
    assert port is not None
    with socket.socket() as squatter:
        squatter.bind(("127.0.0.1", port))
        squatter.listen()
        p = plan.build(att, stack.ipc(), ROOT)
    assert any(f.code == "PORT_CONFLICT" and str(port) in f.message for f in p.findings)
    assert plan.build(att, stack.ipc(), ROOT).ok, "and clean once it is free"


def test_an_existing_table_with_another_shape_is_refused_everywhere_it_exists(stack, tmp_path):
    clash = make_bundle(
        tmp_path / "clash",
        "trade:([] time:`timestamp$(); sym:`symbol$(); price:`long$())",
        table="trade",
    )
    p = plan.build(attachment(stack, clash), stack.ipc(), ROOT)
    bad = {f.component for f in p.findings if f.code == "SCHEMA_INCOMPATIBLE"}
    assert {"stp1", "rdb1", "wdb1"} <= bad
    assert any("column price is type 'f', expected 'j'" in f.message for f in p.findings)


def test_an_existing_table_with_the_same_shape_is_adopted(stack, tmp_path):
    same = make_bundle(
        tmp_path / "same",
        "trade:([] time:`timestamp$(); sym:`symbol$(); price:`float$(); size:`int$(); "
        "stop:`boolean$(); cond:`char$(); ex:`char$(); side:`symbol$())",
        table="trade",
    )
    p = plan.build(attachment(stack, same), stack.ipc(), ROOT)
    assert p.ok, [f for f in p.findings if f.level == "blocker"]
    assert "WILL_ADOPT" in {f.code for f in p.findings}
    assert not any(c["creates"] for c in p.changes() if c["process"] == "stp1")


def test_a_tickerplant_that_shares_one_log_between_tables_is_refused(tmp_path, bundle):
    s = IsolatedStack(tmp_path / "singular", settings="\\d .stplg\nmultilog:`singular;\n\\d .")
    try:
        s.start("discovery1", "stp1", "rdb1", "wdb1")
        p = plan.build(attachment(s, bundle), s.ipc(), ROOT)
        assert any(f.code == "LOGGING_MODE" and "singular" in f.message for f in p.findings)
        assert not p.ok
    finally:
        s.teardown()


def test_the_probe_reads_the_published_columns_in_order(stack):
    probe = stack.ipc().call(stack.endpoint("stp1"), "probe", {"tables": ["trade"]})
    assert probe["schemas"]["trade"][:3] == [["time", "p"], ["sym", "s"], ["price", "f"]]
    assert [n for n, _ in LEDGER_COLUMNS][0] == "time"
