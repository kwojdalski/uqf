"""The #1450 blast radius on a REAL TorQ 5.2.12 stack: a table named like a global.

Before the fix an attached table named `upd`, `loghandles`, `sym` or `endofday`
passed plan, validate and apply, and `t set <empty table>` replaced the global on
the tickerplant and the consumers (and, persisted, after every restart). Here the
same names meet the isolated starter-pack stack and are refused - by preflight
before any change, and by the q operations themselves when something reaches them
anyway.

Opt-in like the other stack tests (UQS_ATTACH_STACK_TEST=1).
"""

from __future__ import annotations

import pytest
from attach_stack_support import CORE, IsolatedStack, requires_stack
from attach_support import make_bundle

from uqs.attach import apply as applying
from uqs.attach import config, plan, schema, service
from uqs.paths import repo_root

pytestmark = requires_stack()
ROOT = repo_root()
COLUMNS = [["time", "p"], ["sym", "s"], ["amount", "f"]]
#: where each global really lives on the starter pack (system"f" / system"v"), per the issue
HOLDERS = {
    "upd": {"stp1", "rdb1", "wdb1"},
    "loghandles": {"stp1"},
    "tablelist": {"stp1"},
    "endofday": {"rdb1", "wdb1"},
    "sym": {"wdb1"},
}


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    s = IsolatedStack(tmp_path_factory.mktemp("attach-names"))
    try:
        s.start(*CORE)
        yield s
    finally:
        s.teardown()


def definition(name: str) -> str:
    return f"{name}:([] time:`timestamp$(); sym:`symbol$(); amount:`float$())"


def attachment(stack, bundle):
    return service.create(
        "pb-uat",
        "pb_general_ledger",
        bundle,
        stack.port,
        ROOT,
        host="127.0.0.1",
        runtime_only=False,
        discovery=f"127.0.0.1:{stack.port + 1}",
    )


def kinds(stack, name: str) -> dict[str, int]:
    """`type` of the global on each process that was probed to hold it."""
    return {p: int(stack.q(p, f"type @[value;`{name};0]")) for p in ("stp1", "rdb1", "wdb1")}


def request(name: str) -> dict:
    return {"table": name, "columns": COLUMNS, "token": "t1", "attachment": "pb-uat"}


@pytest.mark.parametrize("name", sorted(HOLDERS))
def test_preflight_refuses_a_table_named_like_a_global_and_apply_changes_nothing(
    stack, tmp_path, monkeypatch, name
):
    """The static list would stop these at parse; it is switched off here so that
    the probe - what the RUNNING processes hold - is what is shown to refuse."""
    monkeypatch.setattr(schema, "reserved_reason", lambda n: None, raising=False)
    bundle = make_bundle(tmp_path / "b", definition(name), table=name)
    att = attachment(stack, bundle)
    before = kinds(stack, name)
    schema_before = stack.schema_file.read_text()

    p = plan.build(att, stack.ipc(), ROOT)
    hit = {f.component for f in p.findings if f.code == "NAME_RESERVED"}
    assert hit == HOLDERS[name]
    assert not p.ok

    config.save(att, stack.root / "attachments")
    with pytest.raises(applying.ApplyRefused, match="NAME_RESERVED"):
        applying.apply(att, stack.ipc(), ROOT, state_dir=stack.root / "attachments")
    assert kinds(stack, name) == before, "the global is still what it was on every process"
    assert stack.schema_file.read_text() == schema_before


@pytest.mark.parametrize(("name", "process"), [("loghandles", "stp1"), ("upd", "stp1")])
def test_the_tickerplant_operation_refuses_to_overwrite_a_global(stack, name, process):
    before = kinds(stack, name)
    answer = stack.ipc().call(stack.endpoint(process), "stp_register", request(name))
    assert answer["status"] == "refused"
    assert "already defined" in answer["detail"]
    assert kinds(stack, name) == before
    assert name not in stack.q("stp1", "string each tables[]")


@pytest.mark.parametrize(
    ("name", "process"),
    [("upd", "rdb1"), ("upd", "wdb1"), ("endofday", "rdb1"), ("sym", "wdb1")],
)
def test_the_consumer_operation_refuses_to_overwrite_a_global(stack, name, process):
    before = kinds(stack, name)
    answer = stack.ipc().call(stack.endpoint(process), "consumer_attach", request(name))
    assert answer["status"] == "refused"
    assert "already defined" in answer["detail"]
    assert kinds(stack, name) == before
    assert name not in stack.q(process, "string each tables[]")


def test_a_q_keyword_is_not_taken_as_a_table_name_either(stack):
    answer = stack.ipc().call(stack.endpoint("stp1"), "stp_register", request("count"))
    assert answer["status"] == "refused"
    assert int(stack.q("stp1", "count 1 2 3")) == 3
