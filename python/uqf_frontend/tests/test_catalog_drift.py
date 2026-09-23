"""Guard against the catalog drifting from the generated database.q schemas.

The catalog is hand-written because this package must not depend on
torq_orchestrator at runtime. This test closes the loop by reading the schema
constants out of core.py as *text* - no import, so no dependency on
torq_orchestrator's own environment, and no way for this gate to silently
skip itself.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from uqf_frontend.catalog import TABLES, QType

_Q_TO_CATALOG = {
    "timestamp": QType.TIMESTAMP,
    "timespan": QType.TIMESPAN,
    "float": QType.FLOAT,
    "long": QType.LONG,
    "symbol": QType.SYMBOL,
    "boolean": QType.BOOLEAN,
    "guid": QType.GUID,
}

#: Repository root, for the q files that own every schema this test reads.
REPO = Path(__file__).resolve().parents[3]

#: The tickerplant table definitions, read as TEXT - no import and no q
#: process, so this gate cannot silently skip itself because an environment
#: was broken.
#:
#: They lived in core.py, then in model/schemas.py when core.py was split, and now
#: in q - which is where q table definitions belong, and which turns this
#: check from Python-against-Python into catalog-against-q. That history is
#: why `test_the_schema_source_is_where_this_test_expects_it` exists below: a
#: stale path here would make every parametrised case fail loudly, which is
#: the behaviour to keep.
TABLES_Q = REPO / "scripts" / "processes" / "uqf_stack_tables.q"

#: q type characters, as they appear in a source contract's `types` string.
_CHAR_TO_CATALOG = {
    "p": QType.TIMESTAMP,
    "n": QType.TIMESPAN,
    "f": QType.FLOAT,
    "j": QType.LONG,
    "s": QType.SYMBOL,
    "b": QType.BOOLEAN,
}


def _tickerplant_schema(table: str) -> str:
    """Pull one `name:([]...)` definition out of the q file, by table name.

    Line-anchored: every comment in that file starts with `/`, so a definition
    is the only thing that can begin a line with `name:([]`.
    """
    source = TABLES_Q.read_text()
    m = re.search(rf"^{table}:\(\[\].*$", source, re.M)
    assert m, f"{table} is not defined in {TABLES_Q}"
    return m.group(0)


def _parse(schema: str) -> dict[str, QType]:
    """Parse ``tbl:([]a:`type$(); b:())`` into {column: QType}."""
    body = schema[schema.index("([]") + 3 : schema.rindex(")")]
    out: dict[str, QType] = {}
    for part in body.split(";"):
        part = part.strip()
        if not part or ":" not in part:
            continue
        name, spec = part.split(":", 1)
        m = re.search(r"`(\w+)\$\(\)", spec)
        if m:
            out[name.strip()] = _Q_TO_CATALOG[m.group(1)]
        elif spec.strip() == "()":
            out[name.strip()] = QType.LIST
    return out


def _q_table_schema(path: Path, table: str) -> dict[str, QType]:
    """Parse a ``([]a:`type$(); ...)`` literal out of a q file, by table name.

    Read as text, like core.py above: no q process, so this gate cannot skip
    itself because an interpreter was missing.
    """
    source = (REPO / path).read_text()
    m = re.search(rf"`{table} set (\(\[\].*?\))\s*\]", source, re.S)
    assert m, f"no `{table} set ([]...) literal found in {path}"
    return _parse(m.group(1))


def _q_contract_columns(path: Path, fields_const: str, types_const: str) -> dict[str, QType]:
    """Parse a source contract's ``fields``/``types`` pair into {column: QType}."""
    source = (REPO / path).read_text()
    fm = re.search(rf"^{fields_const}:((?:`\w+)+)", source, re.M)
    tm = re.search(rf'^{types_const}:"(\w+)"', source, re.M)
    assert fm and tm, f"{fields_const}/{types_const} not found in {path}"
    names = fm.group(1).lstrip("`").split("`")
    chars = tm.group(1)
    assert len(names) == len(chars), (
        f"{path}: {len(names)} field(s) but {len(chars)} type char(s) - the q side "
        f"itself is inconsistent"
    )
    return {n: _CHAR_TO_CATALOG[c] for n, c in zip(names, chars, strict=True)}


def test_the_schema_source_is_where_this_test_expects_it():
    """If the schemas move again, this gate must fail loudly rather than skip.

    They have moved twice: core.py to model/schemas.py when core.py was split, and
    model/schemas.py to q when it became clear that q table definitions living as
    Python string literals were read by no q parser until stp1 started. This
    assertion is what turns the next move into one clear failure instead of
    five confusing ones.
    """
    assert TABLES_Q.is_file(), f"expected the tickerplant table definitions at {TABLES_Q}"


#: Catalog tables whose schema is a tickerplant table in scripts/processes/uqf_stack_tables.q.
#: ONE list, read by the parametrize below and by the closed-loop gate at the
#: bottom: the gate used to carry its own copy, which is the drift it exists
#: to catch.
_TICKERPLANT_TABLES = [
    "trades",
    "position",
    "execution_quality",
    "quotes",
    "crypto_trades",
    "crypto_book",
    "crypto_sim_fills",
    "wide_book",
    "mkt_orderbook",
    "databento_book",
    "executions",
    "marks",
    "market_data",
    "superbook",
    "arbitrage",
    "cross_arbitrage",
    "config_change",
    "orders",
    "fx_position",
    "fx_limit_breach",
]

#: Tickerplant tables the desk catalog deliberately does NOT carry, with the
#: reason. The gate below holds this list closed in the other direction: a
#: table published onto the plant and absent from both this list and the
#: catalog is an OMISSION, not a decision, and that is how five of them went
#: unbrowsable without anything noticing.
_NOT_IN_CATALOG = {
    "databento_mbp10": (
        "the RAW Databento feed, published by an external Python handler and "
        "consumed only by databento1, which folds it into databento_book. A desk "
        "browsing a book wants the folded one; this is the input to that fold."
    ),
}


@pytest.mark.parametrize("table_name", _TICKERPLANT_TABLES)
def test_catalog_matches_the_generated_schema(table_name):
    expected = _parse(_tickerplant_schema(table_name))
    actual = TABLES[table_name].columns
    assert actual == expected, (
        f"{table_name} catalog has drifted from {TABLES_Q.name}; "
        f"missing={set(expected) - set(actual)} extra={set(actual) - set(expected)}"
    )


#: Catalog tables whose schema is owned by a q file rather than by core.py.
#: (table name) -> (q file, how to read the columns out of it)
_Q_OWNED = {
    "etl_coverage": lambda: _q_table_schema(Path("src/etl/core/materialisation.q"), "etl_coverage"),
    "demo_deals": lambda: _q_contract_columns(
        Path("src/etl/sources/demo_deals.q"), "fields", "types"
    ),
    "event_tape": lambda: _q_contract_columns(
        Path("src/etl/sources/demo_events.q"), "fields", "types"
    ),
}


@pytest.mark.parametrize("table_name", sorted(_Q_OWNED))
def test_catalog_matches_the_q_side_schema(table_name):
    """The ETL tables are declared in q, so cross-check them there.

    etl_coverage's shape is unverified (#60), which makes this check *more*
    important rather than less: while the shape is a guess, the catalog and
    the q writer must at least be telling the same story, so that correcting
    it is one edit rather than a hunt.
    """
    expected = _Q_OWNED[table_name]()
    actual = TABLES[table_name].columns
    assert actual == expected, (
        f"{table_name} catalog has drifted from its q declaration; "
        f"missing={set(expected) - set(actual)} extra={set(actual) - set(expected)}"
    )


def test_every_catalog_table_is_cross_checked():
    """No table may sit in the catalog without a drift check.

    This is the gate's own loophole, and it was open: the parametrize list
    above only checks the tables it names, so adding a table to the catalog
    silently escaped the check entirely - two did, and 233 tests passed. A
    drift gate that does not know what it is failing to check is the same
    failure mode as a lint hook scoped to a stale path.
    """
    checked = set(_TICKERPLANT_TABLES) | set(_Q_OWNED)
    unchecked = set(TABLES) - checked
    assert not unchecked, (
        f"catalog table(s) {sorted(unchecked)} have no drift check. Add the table "
        f"to _TICKERPLANT_TABLES if scripts/processes/uqf_stack_tables.q defines it, or the q "
        f"file to _Q_OWNED."
    )


def test_every_published_table_is_in_the_catalog_or_explicitly_not():
    """The other direction, and the one that was missing.

    The gate above asks "is every catalog table checked". It cannot notice a
    table that is published and absent from the catalog entirely - and five
    were: wide_book, mkt_orderbook, databento_book, executions and marks.
    The desk view browses the catalog, so those tables simply did not exist
    as far as FE-07 was concerned, and no test could say so.

    A published table must therefore be in the catalog, or in _NOT_IN_CATALOG
    with a reason. "We forgot" is not one of the two.
    """
    published = set(re.findall(r"^(\w+):\(\[\]", TABLES_Q.read_text(), re.M))
    missing = published - set(TABLES) - set(_NOT_IN_CATALOG)
    assert not missing, (
        f"table(s) {sorted(missing)} are published onto the tickerplant but are not in "
        f"the desk catalog, so FE-07 cannot browse them. Add them to "
        f"python/uqf_frontend/catalog/, or to _NOT_IN_CATALOG with the reason they "
        f"are deliberately absent."
    )


def test_the_deliberate_omissions_are_all_real_tables():
    """So the exemption list cannot rot: a name that no longer exists on the
    plant is a stale excuse, and would hide the next real omission behind it.
    """
    published = set(re.findall(r"^(\w+):\(\[\]", TABLES_Q.read_text(), re.M))
    stale = set(_NOT_IN_CATALOG) - published
    assert not stale, (
        f"_NOT_IN_CATALOG names {sorted(stale)}, which {TABLES_Q.name} no longer "
        f"publishes - remove the entry rather than leaving a dead exemption."
    )
