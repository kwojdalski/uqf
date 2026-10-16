"""An attached table's name must not be a global the stack already defines (#1450).

`stp_register` and the consumers' subscribe do `t set <empty table>`: a table named
`upd`, `loghandles`, `sym`, `date` or `endofday` overwrites that global on the
tickerplant, rdb and wdb at once - and, persisted, again at every restart. These
tests hold the layers that refuse it, against `FakeStack` so they run in CI;
test_attach_name_collision_stack.py puts the same names to a real stack.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from attach_support import FakeStack, make_bundle

from uqs.attach import apply as applying
from uqs.attach import config, facts, overrides, persist, plan, service
from uqs.attach.config import AttachmentError
from uqs.attach.manifest import Manifest
from uqs.attach.overrides import OverrideError
from uqs.attach.schema import SchemaError, TableSpec, parse_definition, reserved_reason
from uqs.paths import repo_root

ROOT = repo_root()
ISSUE_NAMES = ["upd", "loghandles", "sym", "date", "endofday", "endofperiod", "tablelist"]


@pytest.fixture(autouse=True)
def no_real_ports(monkeypatch):
    monkeypatch.setattr(facts, "_tcp_open", lambda host, port: False)


def definition(name: str) -> str:
    return f"{name}:([] time:`timestamp$(); sym:`symbol$(); amount:`float$())"


def bundle_for(tmp_path: Path, name: str) -> Path:
    return make_bundle(tmp_path / "b", definition(name), table=name)


def attachment(bundle: Path) -> config.Attachment:
    return service.create("pb-uat", "pb_general_ledger", bundle, 6000, ROOT, runtime_only=True)


def plan_for(bundle: Path, fake: FakeStack) -> plan.Plan:
    host = facts.Host(lambda h, p: False, ("testhost",))
    return plan.build(attachment(bundle), fake, ROOT, facts.Facts(host=host))


# ------------------------------------------------- the static list (bundle time)


@pytest.mark.parametrize(
    "name", [*ISSUE_NAMES, "month", "year", "int", "count", "select", "reload"]
)
def test_a_table_named_like_a_torq_global_or_partition_or_keyword_is_refused_at_parse(name):
    with pytest.raises(SchemaError, match=f"'{name}' cannot be a table name"):
        parse_definition(definition(name))


@pytest.mark.parametrize(
    ("name", "why"),
    [
        ("upd", "global every TorQ process defines"),
        ("date", "partition name of the hdb"),
        ("count", "q keyword"),
        ("trade", None),
        ("piggybank_general_ledger", None),
    ],
)
def test_the_reason_names_what_kind_of_collision_it_is(name, why):
    got = reserved_reason(name)
    assert (got is None) if why is None else (got is not None and why in got)


# ------------------------------------------------- preflight on the stack's own names


def test_a_name_the_stack_already_binds_to_a_non_table_is_a_blocker_on_each_process(tmp_path):
    """`site_registry` is on no static list: only the probe knows the stack holds
    it. The tickerplant and each consumer say so, separately."""
    fake = FakeStack(6000)
    for name in ("stp1", "rdb1", "wdb1"):
        fake.named(name)["names"].append("site_registry")
    p = plan_for(bundle_for(tmp_path, "site_registry"), fake)
    hit = {f.component for f in p.findings if f.code == "NAME_RESERVED"}
    assert hit == {"stp1", "rdb1", "wdb1"}
    assert not p.ok
    assert all(f.level == "blocker" for f in p.findings if f.code == "NAME_RESERVED")


def test_one_consumer_holding_the_name_is_enough_to_refuse(tmp_path):
    fake = FakeStack(6000)
    fake.named("wdb1")["names"].append("site_registry")
    p = plan_for(bundle_for(tmp_path, "site_registry"), fake)
    assert {f.component for f in p.findings if f.code == "NAME_RESERVED"} == {"wdb1"}
    assert not p.ok


def test_a_name_that_is_a_table_is_judged_by_the_schema_rules_not_as_a_collision(tmp_path):
    """`trade` is in `names` too: it is a root global that happens to be a table."""
    bundle = make_bundle(
        tmp_path / "b", "trade:([] time:`timestamp$(); sym:`symbol$())", table="trade"
    )
    fake = FakeStack(6000)
    fake.named("stp1")["schemas"]["trade"] = [["time", "p"], ["sym", "s"], ["price", "f"]]
    codes = {f.code for f in plan_for(bundle, fake).findings}
    assert "NAME_RESERVED" not in codes
    assert "SCHEMA_INCOMPATIBLE" in codes


def test_the_collision_cannot_be_overridden():
    assert overrides.POLICY["NAME_RESERVED"].overridable is False
    with pytest.raises(OverrideError, match="NAME_RESERVED cannot be overridden"):
        overrides.create(["NAME_RESERVED:stp1"], "i know", [], "alice")


def test_apply_is_refused_before_any_change_and_the_stack_is_untouched(tmp_path):
    fake = FakeStack(6000)
    fake.named("stp1")["names"].append("site_registry")
    att = attachment(bundle_for(tmp_path, "site_registry"))
    state = tmp_path / "attachments"
    config.save(att, state)
    with pytest.raises(applying.ApplyRefused, match="NAME_RESERVED"):
        applying.apply(att, fake, ROOT, state_dir=state)
    assert [op for _, op, _ in fake.calls if op != "probe"] == []
    assert not Manifest.exists("pb-uat", state)


def test_the_attachments_own_table_is_not_a_collision_on_the_next_plan(tmp_path):
    fake = FakeStack(6000)
    bundle = make_bundle(tmp_path / "b")
    att = attachment(bundle)
    state = tmp_path / "attachments"
    config.save(att, state)
    applying.apply(att, fake, ROOT, state_dir=state)
    again = plan_for(bundle, fake)
    assert "NAME_RESERVED" not in {f.code for f in again.findings}


# ------------------------------------------------- the persisted schema block


@pytest.mark.parametrize("name", ["upd", "loghandles", "sym", "date"])
def test_the_schema_block_writer_refuses_such_a_name(tmp_path, name):
    spec = TableSpec(name, (("time", "p"), ("amount", "f")))
    att = attachment(make_bundle(tmp_path / "b"))
    target = tmp_path / "database.q"
    target.write_text("trade:([] time:`timestamp$());\n")
    with pytest.raises(AttachmentError, match=f"refusing to write the schema block.*'{name}'"):
        persist.write(att, "tok", (spec,), str(target))
    assert target.read_text() == "trade:([] time:`timestamp$());\n", "the file was not touched"
    with pytest.raises(AttachmentError, match="refusing to write the schema block"):
        persist.render("pb-uat", "tok", (spec,))
