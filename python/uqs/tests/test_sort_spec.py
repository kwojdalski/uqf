"""A backfill's partition sort must agree with TorQ's sort.csv (#517).

Two writers produce the same HDB partitions:

- **End of day** sorts and sets attributes through TorQ's `.sort.sorttab`,
  which reads `$KDBCONFIG/sort.csv`: `lib/torq/config/sort.csv`.
- **A bounded worker** sorts on its own when it finishes a partition:
  `.qetl.io.finish_parts` in `src/etl/core/io_manager.q`, sym then time with
  `p#sym`. Reactions write through the same IO manager.

They agree today, but by coincidence: nothing tied them together. A per-table
row in sort.csv would be honoured by end of day and ignored by every backfill
of that table, which would then hold partitions sorted the old way: queries
relying on the attribute run slower, or return rows out of the documented
order. This test ties them together. It needs no q: both sides are read as
text.

When it fails, either teach the IO manager to read the spec, or change the
sort.csv row.
"""

from __future__ import annotations

import csv
import io
import re

import pytest

from uqs.model.declarations import reaction_calls, strip_q_comments
from uqs.paths import repo_root

ROOT = repo_root()
SORT_CSV = ROOT / "lib" / "torq" / "config" / "sort.csv"
IO_MANAGER = ROOT / "src" / "etl" / "core" / "io_manager.q"

#: What `.qetl.io.finish_parts` applies, as sort.csv rows (att, column, sort):
#: `p#` on sym, sorted by sym then time.
IO_MANAGER_SPEC = (("p", "sym", "1"), ("", "time", "1"))

#: The code that applies IO_MANAGER_SPEC. Held here so a change to the IO
#: manager's sort fails this test too, not only a change to sort.csv.
IO_MANAGER_CODE = ("order:(`sym`time inter c);", "if[`sym in c; @[part;`sym;`p#]];")

Spec = tuple[tuple[str, str, str], ...]


def sort_specs(text: str) -> dict[str, Spec]:
    """sort.csv as table -> its rows, `default` included."""
    specs: dict[str, list[tuple[str, str, str]]] = {}
    for row in csv.DictReader(io.StringIO(text)):
        specs.setdefault(row["tabname"].strip(), []).append(
            (row["att"].strip(), row["column"].strip(), row["sort"].strip())
        )
    return {table: tuple(rows) for table, rows in specs.items()}


def effective(specs: dict[str, Spec], table: str) -> Spec:
    """The spec end of day applies to `table`: its own rows, else `default`."""
    return specs.get(table, specs["default"])


def mismatches(sort_csv_text: str, tables: list[str]) -> list[str]:
    """Each table whose end-of-day spec differs from the IO manager's, named."""
    specs = sort_specs(sort_csv_text)
    return [
        f"{t}: sort.csv applies {effective(specs, t)}, a backfill applies {IO_MANAGER_SPEC}"
        for t in tables
        if effective(specs, t) != IO_MANAGER_SPEC
    ]


def backfilled_tables() -> list[str]:
    """Every table the IO manager writes: each source's `target`, and each
    reaction's declared output."""
    found: set[str] = set()
    for path in sorted((ROOT / "src" / "etl" / "sources").glob("*.q")):
        match = re.search(r"(?m)^target:`([A-Za-z_][A-Za-z0-9_]*)", path.read_text())
        if match:
            found.add(match.group(1))
    for path in sorted((ROOT / "src" / "etl" / "reactions").glob("*.q")):
        for reaction in reaction_calls(strip_q_comments(path.read_text())):
            found.update(reaction.writes)
    return sorted(found)


def test_every_backfilled_table_is_sorted_as_end_of_day_sorts_it():
    tables = backfilled_tables()
    assert tables, "no backfilled tables found - the scan is looking in the wrong place"
    assert mismatches(SORT_CSV.read_text(), tables) == []


def test_the_io_manager_still_applies_the_spec_this_test_holds():
    source = IO_MANAGER.read_text()
    missing = [line for line in IO_MANAGER_CODE if line not in source]
    assert not missing, (
        f"src/etl/core/io_manager.q no longer contains {missing}; update IO_MANAGER_SPEC "
        "to what finish_parts now applies"
    )


def test_a_per_table_row_that_disagrees_is_named():
    # The issue's own example: quote sorted by time with g#sym.
    text = SORT_CSV.read_text().rstrip("\n") + "\nquote,g,sym,0\nquote,,time,1\n"
    found = mismatches(text, ["quote", "demo_deals"])
    assert len(found) == 1 and found[0].startswith("quote:")


@pytest.mark.parametrize("table", ["demo_deals", "deal_positions"])
def test_tables_with_no_row_of_their_own_take_the_default(table):
    assert (
        effective(sort_specs(SORT_CSV.read_text()), table)
        == sort_specs(SORT_CSV.read_text())["default"]
    )


def test_the_scan_finds_the_known_targets():
    tables = backfilled_tables()
    assert {"demo_deals", "imported_trades", "deal_positions"} <= set(tables)
