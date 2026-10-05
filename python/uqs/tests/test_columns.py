"""One place decides what a column type means (#353), held to the real tables.

`scaffold/columns.py` replaced three hand-synced dicts. What keeps it honest:

* every table plant_tables.q and the vendored database.q define parses into
  types it describes, and renders back to the same columns;
* the `--columns` mini-language can say every shape plant_tables.q uses,
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
from uqs.model.schemas import _DEFINITION
from uqs.paths import TABLES_FILE, UqsError
from uqs.scaffold.columns import (
    TYPES,
    columns_spec,
    definition_columns,
    nested_declaration,
    parse_columns,
    resolve_shape,
    table_columns,
    table_definition,
)

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


def test_an_ungrouped_sym_has_no_spelling_and_says_so():
    """The vendored `packets` table: `sym:symbol` would read back GROUPED,
    so the spec refuses rather than disagree with its table."""
    packets = table_columns(EVERY["packets"])
    assert ("sym", "`symbol$()") in packets
    with pytest.raises(UqsError, match="ungrouped sym"):
        columns_spec(packets)


def test_columns_from_copies_a_table_exactly_even_one_the_spec_cannot_say():
    assert resolve_shape(None, "packets", EVERY) == table_columns(EVERY["packets"])
    assert resolve_shape(None, "quotes", EVERY) == table_columns(EVERY["quotes"])


@pytest.mark.parametrize(
    ("columns", "columns_from", "message"),
    [
        ("px:float", "quotes", "pick one"),
        (None, "nope", "not a plant table"),
    ],
)
def test_columns_from_refuses(columns, columns_from, message):
    with pytest.raises(UqsError, match=message):
        resolve_shape(columns, columns_from, EVERY)


def test_without_columns_from_the_spec_passes_through():
    assert resolve_shape("px:float", None, EVERY) == "px:float"


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
    assert line.startswith('nested[`book;`bids`asks!"FF"];')
    assert "SCAFFOLDED" in line
    assert line.endswith("\n")


def test_one_list_column_is_declared_with_enlist():
    # `bids!"F"` would be an atom dictionary, which .qetl.plant.nested refuses.
    line = nested_declaration("book", parse_columns("bids:list"))
    assert line.startswith('nested[`book;(enlist `bids)!enlist "F"];')


def test_every_plant_table_with_list_columns_is_declared_beside_it():
    """The text side of test_plant_tables.q's gate: each of this tree's tables
    with a () column has a nested[...] line, so the gate does not need q to
    say which one is missing."""
    text = (UQF_ROOT / TABLES_FILE).read_text()
    declared = set(re.findall(r"^nested\[`([a-z_0-9]+);", text, re.MULTILINE))
    with_lists = {
        name
        for name, line in _tables(UQF_ROOT / TABLES_FILE).items()
        if any(lit == "()" for _, lit in definition_columns(line))
    }
    assert with_lists - declared == set()
