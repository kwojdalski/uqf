"""#1451 on a REAL TorQ 5.2.12 stack: what the wdb needs from an attached table.

A mixed column (`col:()`) lets each batch decide its stored type; once the wdb
has written one shape, another makes its append fail, and TorQ's writedown
re-raises it for every table. So an untyped column is refused before the stack is
touched (here), and what IS accepted - concrete types and nested columns with a
declared element type - is shown to survive the writedown shapes that matter:
repeated flushes with different row counts and vector lengths, then end of day.

Opt-in like the other stack tests (`-m live_stack`).
"""

from __future__ import annotations

import dataclasses

import pytest
from attach_stack_support import CORE, IsolatedStack, requires_stack
from attach_support import make_bundle

from uqs.attach import apply as applying
from uqs.attach import config, plan, service
from uqs.attach.config import AttachmentError
from uqs.attach.schema import TableSpec
from uqs.paths import repo_root

pytestmark = requires_stack()
ROOT = repo_root()
BOOK = "depth_book"
BOOK_Q = (
    f"{BOOK}:([] time:`timestamp$(); sym:`symbol$(); bid_prices:(); bid_sizes:(); "
    "note:`symbol$())\n"
    f'nested[`{BOOK};`bid_prices`bid_sizes!"FJ"]'
)
UNTYPED_Q = f"{BOOK}:([] time:`timestamp$(); sym:`symbol$(); note:())"
TRADE = (
    "{.u.upd[`trade;(enlist `AAA;enlist 1.0;enlist 1i;enlist 0b;"
    'enlist "N";enlist "X";enlist `buy)]}'
)


@pytest.fixture
def stack(tmp_path):
    s = IsolatedStack(tmp_path / "stack")
    try:
        s.start(*CORE)
        yield s
    finally:
        s.teardown()


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


def upd(stack, rows: str) -> None:
    stack.q("stp1", "{.u.upd[`" + BOOK + ";" + rows + "]}", None)


def tables(stack, name: str) -> list[str]:
    return sorted(str(t) for t in stack.q(name, "string each tables[]"))


def test_an_untyped_column_is_refused_before_the_stack_is_touched(stack, tmp_path):
    before = {p: tables(stack, p) for p in ("stp1", "rdb1", "wdb1")}
    schema_before = stack.schema_file.read_text()
    bundle = make_bundle(tmp_path / "b", UNTYPED_Q, table=BOOK)
    with pytest.raises(AttachmentError, match=rf"{BOOK}\.note is an untyped.*EVERY table"):
        attachment(stack, bundle)
    assert {p: tables(stack, p) for p in before} == before
    assert stack.schema_file.read_text() == schema_before


def test_a_saved_attachment_with_an_untyped_column_cannot_be_applied(stack, tmp_path):
    att = attachment(stack, make_bundle(tmp_path / "b", BOOK_Q, table=BOOK))
    old = TableSpec(BOOK, (("time", "p"), ("sym", "s"), ("note", " ")))
    legacy = dataclasses.replace(att, tables=(old,))
    p = plan.build(legacy, stack.ipc(), ROOT)
    assert any(f.code == "UNTYPED_COLUMN" for f in p.findings) and not p.ok
    config.save(legacy, stack.root / "attachments")
    with pytest.raises(applying.ApplyRefused, match="UNTYPED_COLUMN"):
        applying.apply(legacy, stack.ipc(), ROOT, state_dir=stack.root / "attachments")
    assert BOOK not in tables(stack, "stp1")


@pytest.mark.parametrize("process", ["stp1", "rdb1", "wdb1"])
def test_the_operations_refuse_an_untyped_column_themselves(stack, process):
    request = {
        "table": BOOK,
        "columns": [["time", "p"], ["sym", "s"], ["note", " "]],
        "token": "t1",
        "attachment": "pb-uat",
    }
    op = "stp_register" if process == "stp1" else "consumer_attach"
    answer = stack.ipc().call(stack.endpoint(process), op, request)
    assert answer["status"] == "refused" and "untyped column(s) note" in answer["detail"]
    assert BOOK not in tables(stack, process)


def test_typed_and_nested_columns_survive_repeated_flushes_and_end_of_day(stack, tmp_path):
    """The shapes that broke the wdb with an untyped column: a first flush, then
    batches whose row counts and vector lengths differ (one row with an empty
    ladder), a flush of those, and end of day. Every table reaches the hdb."""
    att = attachment(stack, make_bundle(tmp_path / "b", BOOK_Q, table=BOOK))
    config.save(att, stack.root / "attachments")
    assert applying.apply(att, stack.ipc(), ROOT, state_dir=stack.root / "attachments").state == (
        "applied"
    )
    first = "(`AAA`BBB;(1 2 3f;4 5f);(10 20 30j;40 50j);`a`b)"
    second = "(enlist `AAA;enlist 0#0f;enlist 0#0j;enlist `c)"
    third = "(`AAA`BBB`CCC;(7f;8 9f;1 2 3 4f);(1j;2 3j;4 5 6 7j);`d`e`f)"
    stack.q("stp1", TRADE, None)
    upd(stack, first)
    stack.flush()
    stack.q("wdb1", "{.wdb.numrows:1; .wdb.savetodisk[]; 1b}", None)
    upd(stack, second)
    stack.q("stp1", TRADE, None)
    stack.flush()
    stack.q("wdb1", "{.wdb.savetodisk[]; 1b}", None)
    upd(stack, third)
    stack.flush()
    assert stack.rows("wdb1", BOOK) > 0, "rows are still in the wdb until end of day"
    stack.q("wdb1", "{.wdb.numrows:100000; 1b}", None)
    stack.end_of_day(25)

    days = sorted(
        p for p in stack.hdb_dir.iterdir() if (p / BOOK).is_dir() and (p / "trade").is_dir()
    )
    assert len(days) == 1, "the day's partition holds the attached table AND the vendored one"
    assert int(stack.q("hdb1", f"count select from {BOOK} where date={days[0].name}")) == 6
    assert int(stack.q("hdb1", f"count select from trade where date={days[0].name}")) == 2
    assert stack.rows("wdb1", BOOK) == 0 and stack.rows("wdb1", "trade") == 0


def test_what_the_refusal_prevents_one_mixed_column_stops_end_of_day_for_every_table(tmp_path):
    """The premise, on a stack that was given a mixed-column table directly in its
    schema file (nothing from this workflow): after one shape is on disk, a batch
    of another fails the wdb's append, and end of day writes no partition for ANY
    table - `trade` included."""
    s = IsolatedStack(tmp_path / "hazard")
    s.schema_file.write_text(
        s.schema_file.read_text()
        + "\nmixed_t:([] time:`timestamp$(); sym:`symbol$(); account:`symbol$(); note:())\n"
    )
    try:
        s.start(*CORE)
        strings = '{.u.upd[`mixed_t;(`AAA`BBB;`a1`a2;("n1";"n2"))]}'
        floats = "{.u.upd[`mixed_t;(`AAA`BBB;`a1`a2;3 4f)]}"
        s.q("stp1", TRADE, None)
        s.q("stp1", strings, None)
        s.flush()
        s.q("wdb1", "{.wdb.numrows:1; .wdb.savetodisk[]; 1b}", None)  # a shape is on disk
        s.q("stp1", floats, None)
        s.q("stp1", TRADE, None)
        s.flush()
        failed = s.q("wdb1", "{@[{.wdb.savetodisk[];`ok};::;{x}]}", None)
        assert str(failed) == "type", "the second shape cannot be appended"
        s.q("wdb1", "{.wdb.numrows:100000; 1b}", None)
        before = {p.name for p in s.hdb_dir.iterdir()}
        s.end_of_day(25)
        assert {p.name for p in s.hdb_dir.iterdir()} == before, "no new partition for ANY table"
    finally:
        s.teardown()
