"""An attached table must declare every column's type (#1451).

A mixed column (`col:()`) is reset to `()` by the tickerplant after every publish
and kept general by the wdb, so each batch decides its stored type afresh. Once
the wdb has written one shape, a later batch of another shape makes the append
fail with `'type`, TorQ's writedown re-raises it, and both its callers iterate
the tables with a plain `each`: the tables after it are skipped, and end of day
aborts before sort, move and reload - for EVERY table. These tests hold the
layers that refuse such a column, against `FakeStack` (runs in CI); the stack file
shows the damage and the refusal on a real wdb.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from attach_support import FakeStack, make_bundle

from uqs.attach import apply as applying
from uqs.attach import config, facts, overrides, plan, service
from uqs.attach.config import AttachmentError
from uqs.attach.manifest import Manifest
from uqs.attach.overrides import OverrideError
from uqs.attach.schema import (
    SchemaError,
    TableSpec,
    check_publishable,
    differences,
    parse_definition,
    parse_nested,
)
from uqs.paths import repo_root

ROOT = repo_root()
MIXED_Q = "t_x:([] time:`timestamp$(); sym:`symbol$(); note:())"


@pytest.fixture(autouse=True)
def no_real_ports(monkeypatch):
    monkeypatch.setattr(facts, "_tcp_open", lambda host, port: False)


def test_an_untyped_column_is_refused_at_parse_naming_the_column_and_the_consequence():
    with pytest.raises(SchemaError, match=r"t_x\.note is an untyped `\(\)` column.*EVERY table"):
        parse_definition(MIXED_Q)


def test_a_declared_nested_form_is_not_a_way_round():
    """The reader takes `col:`type$()` only: a list-of-lists declaration is refused
    as unreadable, never accepted as 'nested, therefore fine'."""
    with pytest.raises(SchemaError, match="cannot read column"):
        parse_definition("t_x:([] time:`timestamp$(); note:enlist `char$())")


def test_a_spec_built_without_the_parser_is_refused_too():
    with pytest.raises(SchemaError, match=r"t_x\.note is an untyped"):
        check_publishable(TableSpec("t_x", (("time", "p"), ("note", " "))))


def test_creating_an_attachment_for_a_bundle_with_an_untyped_column_is_refused(tmp_path):
    bundle = make_bundle(tmp_path / "b", MIXED_Q.replace("t_x", "pb_ledger"), table="pb_ledger")
    with pytest.raises(AttachmentError, match="pb_ledger.note is an untyped"):
        service.create("pb-uat", "pb_general_ledger", bundle, 6000, ROOT, runtime_only=True)


def test_the_fixture_ledger_declares_a_concrete_type_for_every_column(tmp_path):
    att = service.create(
        "pb-uat", "pb_general_ledger", make_bundle(tmp_path / "b"), 6000, ROOT, runtime_only=True
    )
    assert all(char != " " for _, char in att.tables[0].columns)


def legacy(tmp_path: Path) -> config.Attachment:
    """An attachment saved before the refusal: its config holds a mixed column."""
    att = service.create(
        "pb-uat", "pb_general_ledger", make_bundle(tmp_path / "b"), 6000, ROOT, runtime_only=True
    )
    t = att.tables[0]
    old = TableSpec(t.name, (*t.columns[:-1], ("note", " ")))
    return dataclasses.replace(att, tables=(old,))


def test_a_saved_attachment_with_an_untyped_column_is_blocked_by_preflight(tmp_path):
    fake = FakeStack(6000)
    p = plan.build(
        legacy(tmp_path),
        fake,
        ROOT,
        facts.Facts(host=facts.Host(lambda h, p: False, ("testhost",))),
    )
    hit = [f for f in p.findings if f.code == "UNTYPED_COLUMN"]
    assert [f.facts for f in hit] == [{"table": "piggybank_general_ledger", "column": "note"}]
    assert hit[0].level == "blocker" and not p.ok


def test_an_untyped_column_cannot_be_overridden():
    assert overrides.POLICY["UNTYPED_COLUMN"].overridable is False
    with pytest.raises(OverrideError, match="UNTYPED_COLUMN cannot be overridden"):
        overrides.create(["UNTYPED_COLUMN:schema"], "i know", [], "alice")


def test_apply_of_such_an_attachment_is_refused_before_any_change(tmp_path):
    fake, state = FakeStack(6000), tmp_path / "attachments"
    att = legacy(tmp_path)
    config.save(att, state)
    with pytest.raises(applying.ApplyRefused, match="UNTYPED_COLUMN"):
        applying.apply(att, fake, ROOT, state_dir=state)
    assert [op for _, op, _ in fake.calls if op != "probe"] == []
    assert not Manifest.exists("pb-uat", state)


# ------------------------------------------------------ a list per row, with its element type

BOOK_Q = "book:([] time:`timestamp$(); sym:`symbol$(); bid_prices:(); bid_sizes:())"
BOOK_NESTED = 'nested[`book;`bid_prices`bid_sizes!"FJ"]'


def test_a_nested_column_with_a_declared_element_type_reads_as_its_upper_case_meta_char():
    spec = parse_definition(BOOK_Q, {"bid_prices": "F", "bid_sizes": "J"})
    assert spec.columns == (
        ("time", "p"),
        ("sym", "s"),
        ("bid_prices", "F"),
        ("bid_sizes", "J"),
    )
    assert spec.definition() == BOOK_Q
    assert parse_definition(spec.definition(), {"bid_prices": "F", "bid_sizes": "J"}) == spec


def test_the_element_type_is_part_of_the_tables_identity():
    a = parse_definition(BOOK_Q, {"bid_prices": "F", "bid_sizes": "J"})
    b = parse_definition(BOOK_Q, {"bid_prices": "F", "bid_sizes": "I"})
    assert a.sha != b.sha


@pytest.mark.parametrize(
    ("declared", "why"),
    [
        ({}, "no declared element type"),
        ({"bid_prices": "F"}, r"book\.bid_sizes is an untyped"),
        ({"bid_prices": "F", "bid_sizes": " "}, "holding any value"),
        ({"bid_prices": "F", "bid_sizes": "?"}, "not a meta type character"),
    ],
)
def test_a_nested_column_without_a_concrete_element_type_is_refused(declared, why):
    with pytest.raises(SchemaError, match=why):
        parse_definition(BOOK_Q, declared)


@pytest.mark.parametrize(
    ("line", "expect"),
    [
        (
            'nested[`book;`bid_prices`bid_sizes!"FJ"]',
            ("book", {"bid_prices": "F", "bid_sizes": "J"}),
        ),
        (
            '.qetl.plant.nested[`book;`bid_prices`bid_sizes!"FJ"];',
            ("book", {"bid_prices": "F", "bid_sizes": "J"}),
        ),
        ('nested[`xs;(enlist `route)!enlist "S"]', ("xs", {"route": "S"})),
        ('nested[`xs;`old`new!"  "]', ("xs", {"old": " ", "new": " "})),
        ('/ @eg nested[`x;`a!"F"]', None),
        ("book:([] time:`timestamp$())", None),
    ],
)
def test_nested_lines_are_read_as_text(line, expect):
    assert parse_nested(line) == expect


def test_a_nested_line_it_cannot_read_is_an_error_not_a_guess():
    with pytest.raises(SchemaError, match="cannot read the nested"):
        parse_nested('nested[`book;`bid_prices`bid_sizes!"F"]')


def test_a_nested_column_matches_an_empty_and_a_populated_table_but_not_another_type():
    spec = parse_definition(BOOK_Q, {"bid_prices": "F", "bid_sizes": "J"})
    base = [["time", "p"], ["sym", "s"]]
    assert differences(spec, [*base, ["bid_prices", " "], ["bid_sizes", " "]]) == []
    assert differences(spec, [*base, ["bid_prices", "F"], ["bid_sizes", "J"]]) == []
    assert differences(spec, [*base, ["bid_prices", "S"], ["bid_sizes", "J"]]) == [
        "column bid_prices is type 'S', expected 'F'"
    ]


def test_a_bundle_whose_tables_q_declares_the_element_types_attaches(tmp_path):
    bundle = make_bundle(tmp_path / "b", BOOK_Q + "\n" + BOOK_NESTED, table="book")
    att = service.create("pb-uat", "pb_general_ledger", bundle, 6000, ROOT, runtime_only=True)
    assert dict(att.tables[0].columns)["bid_prices"] == "F"
    fake = FakeStack(6000)
    host = facts.Host(lambda h, p: False, ("testhost",))
    assert plan.build(att, fake, ROOT, facts.Facts(host=host)).ok


def test_the_plant_tables_nested_declarations_carry_into_their_specs():
    from uqs.attach import jobs

    (spec,) = jobs.from_tree("fx_orderbook_feed", ROOT).tables
    assert spec.name == "fx_orderbook"
    assert dict(spec.columns)["bid_prices"] == "F" and dict(spec.columns)["ask_sizes"] == "F"
