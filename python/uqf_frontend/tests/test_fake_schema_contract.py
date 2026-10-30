"""FakeGateway's FAKE_SCHEMA describes tables q actually declares (#1784).

The fixture is hand-written on purpose (gateway.py says why), so nothing
derived it from q, and it drifted: `trades` lacked `fill_id` and called the
long `pip_factor` a float. Each table is now declared as q's whole table or a
projection of it (FAKE_SCHEMA_SHAPES), and held to q's own metadata:

  complete    the same columns, in the same order, each type compatible
  projection  every column exists in q's table, each type compatible

q's metadata is the contract surface's table_columns.csv, which
scripts/generate/contract_surface.py exports from the LOADED tree under KDB-X
and the "contract surface matches the tree" hook keeps current - so this reads
q's answer without running q, and parses no q source. It is read through that
script's own `read_surface`, the reader test_contract_surface.py uses.

"Compatible" admits one difference: a nested column's `meta` type is blank
while its table is empty - as the surface exports it - and an uppercase letter
once it has rows, which FAKE_SCHEMA spells on purpose (see its comment).
"""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest

from uqf_frontend.gateway import FAKE_CATALOG, FAKE_SCHEMA, FAKE_SCHEMA_SHAPES

ROOT = Path(__file__).resolve().parents[3]
SURFACE = ROOT / "docs" / "reference" / "surfaces" / "current"
SCRIPT = ROOT / "scripts" / "generate" / "contract_surface.py"

_spec = importlib.util.spec_from_file_location("contract_surface", SCRIPT)
assert _spec is not None and _spec.loader is not None
contract_surface = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(contract_surface)

Tables = dict[str, dict[str, list[str]]]


def compatible(fake: str, q: str) -> bool:
    """`fake` is a type q's `q` can report: the same, or - for a nested column,
    blank while empty - blank or the uppercase type of a populated one."""
    return fake == q or (q == " " and (fake == " " or fake.isupper()))


def problems(fake_rows: list[dict[str, str]], shapes: dict[str, str], q: Tables) -> list[str]:
    """Why `fake_rows` misdescribe q's tables `q`, as sentences; empty when
    every table matches as its shape in `shapes` requires."""
    fake: dict[str, list[tuple[str, str]]] = {}
    for row in fake_rows:
        fake.setdefault(row["table"], []).append((row["column"], row["kind"]))
    unclassified = [t for t in fake if t not in shapes]
    found = [f"{t} is in FAKE_SCHEMA but not in FAKE_SCHEMA_SHAPES" for t in unclassified]
    for table, shape in shapes.items():
        if shape not in ("complete", "projection"):
            found.append(f"{table}'s shape is {shape!r}, not complete or projection")
            continue
        if table not in q:
            found.append(f"{table} is not a table q declares")
            continue
        q_types = dict(zip(q[table]["columns"], q[table]["types"], strict=True))
        columns = [c for c, _ in fake.get(table, [])]
        if shape == "complete" and columns != q[table]["columns"]:
            found.append(f"{table}'s columns are {columns}, q's are {q[table]['columns']}")
        for column, kind in fake.get(table, []):
            if column not in q_types:
                found.append(f"{table}.{column} is not a column of q's {table}")
            elif not compatible(kind, q_types[column]):
                found.append(f"{table}.{column} is {kind!r}, q's is {q_types[column]!r}")
    return found


@pytest.fixture(scope="module")
def q_tables() -> Tables:
    assert SURFACE.is_dir(), f"{SURFACE} is tracked; it is missing, so this path is stale"
    return contract_surface.read_surface(SURFACE)["tables"]


def test_every_fake_table_is_classified_and_catalogued():
    """A table added to the fixture has to say which kind it is."""
    assert {r["table"] for r in FAKE_SCHEMA} == set(FAKE_SCHEMA_SHAPES)
    assert {r["table"] for r in FAKE_CATALOG} == set(FAKE_SCHEMA_SHAPES)


def test_the_fake_schema_matches_qs_tables(q_tables):
    assert problems(FAKE_SCHEMA, FAKE_SCHEMA_SHAPES, q_tables) == []


def test_trades_is_complete_so_it_carries_fill_id(q_tables):
    """The discrepancy #1784 found: q's `trades` has `fill_id`, the fixture did not."""
    assert FAKE_SCHEMA_SHAPES["trades"] == "complete"
    assert "fill_id" in q_tables["trades"]["columns"]
    assert {"table": "trades", "column": "fill_id", "kind": "s"} in FAKE_SCHEMA


# --- the check fails on the changes it exists for ------------------------------


def _changed(q_tables: Tables, table: str, change) -> list[str]:
    q = copy.deepcopy(q_tables)
    change(q[table])
    return problems(FAKE_SCHEMA, FAKE_SCHEMA_SHAPES, q)


def test_a_column_q_adds_to_a_complete_table_fails(q_tables):
    def add(t):
        t["columns"].append("venue")
        t["types"].append("s")

    assert any("trades's columns" in p for p in _changed(q_tables, "trades", add))


def test_a_column_q_reorders_in_a_complete_table_fails(q_tables):
    def swap(t):
        t["columns"][2], t["columns"][3] = t["columns"][3], t["columns"][2]
        t["types"][2], t["types"][3] = t["types"][3], t["types"][2]

    assert any("etl_coverage's columns" in p for p in _changed(q_tables, "etl_coverage", swap))


def test_a_type_q_changes_fails(q_tables):
    def retype(t):
        t["types"][t["columns"].index("pip_factor")] = "f"

    assert _changed(q_tables, "trades", retype) == ["trades.pip_factor is 'j', q's is 'f'"]


def test_a_column_q_drops_from_a_projection_fails(q_tables):
    def drop(t):
        i = t["columns"].index("qty")
        del t["columns"][i], t["types"][i]

    assert _changed(q_tables, "position", drop) == ["position.qty is not a column of q's position"]


def test_a_projection_may_omit_columns_but_not_retype_them(q_tables):
    def retype(t):
        t["types"][t["columns"].index("qty")] = "j"

    assert _changed(q_tables, "position", lambda t: None) == []
    assert _changed(q_tables, "position", retype) == ["position.qty is 'f', q's is 'j'"]


def test_a_nested_column_may_be_blank_or_its_populated_type_and_nothing_else():
    assert compatible("F", " ") and compatible(" ", " ")
    assert not compatible("f", " ") and not compatible("F", "f") and not compatible(" ", "F")
