"""One place decides what a column type means (#353), held to the real tables.

`scaffold/columns.py` replaced three hand-synced dicts. What keeps it honest:

* every table plant_tables.q and the vendored database.q define parses into
  types it describes, and renders back to the same columns;
* the `--schema` mini-language can say every shape plant_tables.q uses,
  vector columns and grouped non-`sym` columns included;
* each type's `meta` character and sample value are what q itself reports -
  checked against a running q, since a wrong character is refused only when
  a scaffolded source registers.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from uqs.interpreter import q_interpreter
from uqs.model import plant_metadata
from uqs.model.schemas import _DEFINITION
from uqs.paths import TABLES_FILE, UqsError
from uqs.scaffold.columns import (
    TYPES,
    Shape,
    as_columns,
    columns_spec,
    definition_columns,
    nested_declaration,
    parse_columns,
    plant_declarations,
    resolve_shape,
    sorting_declaration,
    table_columns,
    table_definition,
)
from uqs.scaffold.raw_columns import parse_raw_columns

UQF_ROOT = Path(__file__).resolve().parents[3]
VENDORED = UQF_ROOT / "lib" / "torq-finance-starter-pack" / "database.q"


def _tables(path: Path) -> dict[str, str]:
    return {m.group(1): m.group(0) for m in _DEFINITION.finditer(path.read_text())}


OURS = _tables(UQF_ROOT / TABLES_FILE)
EVERY = {**_tables(VENDORED), **OURS}


def test_there_are_tables_to_check():
    """A reader that matched nothing would make every test below vacuous."""
    assert len(OURS) >= 30 and {"quote", "trade", "packets"} <= set(EVERY)


@pytest.mark.parametrize("table", sorted(EVERY))
def test_every_plant_table_round_trips_through_q(table):
    cols = table_columns(EVERY[table])
    assert cols, table
    assert table_columns(table_definition(table, cols)) == cols


@pytest.mark.parametrize("table", sorted(OURS))
def test_the_mini_language_says_every_shape_uqs_tables_uses(table):
    cols = table_columns(OURS[table])
    assert parse_columns(columns_spec(cols)) == cols


def test_a_grouped_column_other_than_sym_is_spelled_with_g():
    assert parse_columns("venue:g#symbol")[1] == ("venue", "`g#`symbol$()")
    assert columns_spec([("time", "`timestamp$()"), ("venue", "`g#`symbol$()")]) == (
        "time:timestamp, venue:g#symbol"
    )


def test_sym_is_grouped_without_asking():
    assert dict(parse_columns("sym:symbol"))["sym"] == "`g#`symbol$()"


def test_a_vector_column_is_a_list_and_cannot_be_grouped():
    assert dict(parse_columns("bid_prices:list"))["bid_prices"] == "()"
    with pytest.raises(UqsError, match="cannot carry g#"):
        parse_columns("bid_prices:g#list")


@pytest.mark.parametrize("word", ["value", "count", "last", "type", "from"])
def test_a_q_word_is_refused_as_a_column_name(word):
    """`([] value:...)` is refused by q with `assign` (#1588): a table named
    with one cannot load, so the spec is refused before anything is written."""
    with pytest.raises(UqsError, match="q keyword"):
        parse_columns(f"sym:symbol, {word}:float")
    with pytest.raises(UqsError, match="q keyword"):
        parse_raw_columns(f"ID:long, {word}:float")


def test_the_example_a_bad_column_points_at_is_itself_accepted():
    with pytest.raises(UqsError, match=r"e\.g\. '(\w+):(\w+)'") as err:
        parse_columns("nocolon")
    found = re.search(r"e\.g\. '([^']+)'", str(err.value))
    assert found is not None
    assert parse_columns(found.group(1))


@pytest.mark.parametrize(
    "spec",
    ["sym:symbol, time:timestamp, px:float", "time:float, sym:symbol, px:float", "time:g#symbol"],
)
def test_a_time_column_that_is_not_a_leading_timestamp_is_refused(spec):
    """Every plant table leads with a timestamp `time` (#1574): `.u.upd`
    stamps it and every consumer reads it positionally. A spec that names it
    elsewhere, or as another type, is refused rather than written as asked."""
    with pytest.raises(UqsError, match="time"):
        parse_columns(spec)


def test_a_leading_timestamp_time_is_kept_as_given():
    assert parse_columns("time:timestamp, sym:symbol")[0] == ("time", "`timestamp$()")


@pytest.mark.parametrize(
    "spec",
    ["sym:symbol, px:float, px:long", "px:float, px:float", "time:timestamp, time:timestamp"],
)
def test_a_column_named_twice_is_refused(spec):
    """q renames the second `px` to `px1` (#1575): the table that loads is not
    the one asked for, so the spec is refused before anything is written."""
    with pytest.raises(UqsError, match="named twice"):
        parse_columns(spec)


def test_a_raw_column_named_twice_is_refused():
    with pytest.raises(UqsError, match="named twice"):
        parse_raw_columns("ID:long, PX:float, PX:long")
    assert [c for c, _ in parse_raw_columns("PX:float, Px:float")] == ["PX", "Px"]


def test_an_ungrouped_sym_has_no_spelling_and_says_so():
    """The vendored `packets` table: `sym:symbol` would read back GROUPED,
    so the spec refuses rather than disagree with its table."""
    packets = table_columns(EVERY["packets"])
    assert ("sym", "`symbol$()") in packets
    with pytest.raises(UqsError, match="ungrouped sym"):
        columns_spec(packets)


def _metadata() -> dict[str, plant_metadata.Table]:
    meta = plant_metadata.load(UQF_ROOT)
    return {**meta.vendored(VENDORED), **meta.own()}


def test_columns_from_copies_a_table_exactly_even_one_the_spec_cannot_say():
    tables = _metadata()
    for name in ("packets", "fx_orderbook"):
        shape = resolve_shape(None, name, tables)
        assert isinstance(shape, Shape)
        assert as_columns(shape) == table_columns(EVERY[name])


def test_columns_from_copies_what_each_list_column_holds_and_the_sort_rule():
    """#2450: market_data's sort rule and kafka_message's string columns
    were dropped - the copy was sorted by nothing and held floats."""
    tables = _metadata()
    market_data = resolve_shape(None, "market_data", tables)
    assert isinstance(market_data, Shape)
    assert market_data.sorting == (("sym", "p"), ("source", ""), ("source_time", ""))
    kafka = resolve_shape(None, "kafka_message", tables)
    assert isinstance(kafka, Shape)
    assert dict(kafka.nested) == {"msg_key": "C", "payload": "C"}


@pytest.mark.parametrize(
    ("columns", "columns_from", "message"),
    [
        ("px:float", "fx_orderbook", "pick one"),
        (None, "nope", "not a plant table"),
    ],
)
def test_columns_from_refuses(columns, columns_from, message):
    with pytest.raises(UqsError, match=message):
        resolve_shape(columns, columns_from, _metadata())


def test_without_columns_from_the_spec_passes_through():
    assert resolve_shape("px:float", None, _metadata()) == "px:float"


def test_a_type_no_one_describes_is_refused_strictly_but_read_leniently():
    odd = "odd:([]time:`timestamp$(); g:`guid$())"
    assert definition_columns(odd)[1] == ("g", "`guid$()"), "the normalizer refuses it itself"
    with pytest.raises(UqsError, match="guid"):
        table_columns(odd)


def test_every_type_agrees_with_q():
    """The `meta` character of each empty literal, and of a column built from
    each sample value, are what q reports - not what this file believes."""
    q = q_interpreter(os.environ)
    if q is None:
        pytest.skip("no q interpreter - set $QCMD, or put q on PATH")
    lines = []
    for t in TYPES:
        lines.append(f'-1 "{t.name} literal ",first exec t from meta ([] x:{t.literal});')
        if t.literal != "()":
            lines.append(f'-1 "{t.name} sample ",first exec t from meta ([] x:enlist {t.sample});')
    lines.append("exit 0")
    env = {**os.environ, "QHOME": os.environ.get("QHOME", str(Path.home() / ".kx"))}
    out = subprocess.run(
        [str(q), "-q"],
        input="\n".join(lines) + "\n",
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    ).stdout
    # Each line is "<type> literal|sample X", X one character - a space for a
    # general column, so the key is everything before the last two characters.
    reported = {line[:-2]: line[-1] for line in out.splitlines() if len(line) > 2}
    for t in TYPES:
        assert reported.get(f"{t.name} literal") == t.char, (t.name, out)
        if t.literal != "()":
            assert reported.get(f"{t.name} sample") == t.char, (t.name, out)


def test_a_table_without_list_columns_needs_no_nested_declaration():
    assert nested_declaration("t", parse_columns("sym:symbol, px:float")) == ""


def test_list_columns_are_declared_float_vectors_and_marked_for_review():
    line = nested_declaration("book", parse_columns("sym:symbol, bids:list, asks:list"))
    assert line.startswith('.qetl.plant.nested[`book;`bids`asks!"FF"];')
    assert "SCAFFOLDED" in line
    assert line.endswith("\n")


def test_one_list_column_is_declared_with_enlist():
    # `bids!"F"` would be an atom dictionary, which .qetl.plant.nested refuses.
    line = nested_declaration("book", parse_columns("bids:list"))
    assert line.startswith('.qetl.plant.nested[`book;(enlist `bids)!enlist "F"];')


_BOOK = tuple(parse_columns("sym:symbol, src:symbol, bids:list, venues:list"))


def test_a_copied_shape_declares_its_element_types_exactly_and_unmarked():
    """A `--schema-from` copy knows what each list column holds, so there
    is nothing for the user to fill in - and nothing marked SCAFFOLDED."""
    line = nested_declaration("book", Shape(_BOOK, (("bids", "F"), ("venues", "S"))))
    assert line == '.qetl.plant.nested[`book;`bids`venues!"FS"];\n'


def test_a_list_column_the_source_left_undeclared_is_still_guessed_and_marked():
    line = nested_declaration("book", Shape(_BOOK, (("venues", "S"),)))
    assert line.startswith('.qetl.plant.nested[`book;`bids`venues!"FS"];')
    assert "SCAFFOLDED" in line


@pytest.mark.parametrize(
    ("rule", "line"),
    [
        ((("sym", "p"), ("src", "")), ".qetl.plant.sorting[`book;`sym`src!`p`];\n"),
        ((("sym", "p"),), ".qetl.plant.sorting[`book;(enlist `sym)!enlist `p];\n"),
        ((("src", ""),), ".qetl.plant.sorting[`book;(enlist `src)!enlist `];\n"),
    ],
)
def test_a_copied_sort_rule_is_declared_beside_the_table(rule, line):
    assert sorting_declaration("book", Shape(_BOOK, sorting=rule)) == line


def test_a_spec_has_no_sort_rule_to_declare():
    assert sorting_declaration("book", "sym:symbol, px:float") == ""
    assert "sorting" not in plant_declarations("book", "sym:symbol, px:float")


def test_every_plant_table_with_list_columns_is_declared_beside_it():
    """The text side of test_plant_tables.q's gate: each of this tree's tables
    with a () column has a nested[...] line, so the gate does not need q to
    say which one is missing."""
    text = (UQF_ROOT / TABLES_FILE).read_text()
    declared = set(re.findall(r"^\.qetl\.plant\.nested\[`([a-z_0-9]+);", text, re.MULTILINE))
    with_lists = {
        name
        for name, line in _tables(UQF_ROOT / TABLES_FILE).items()
        if any(lit == "()" for _, lit in definition_columns(line))
    }
    assert with_lists - declared == set()
