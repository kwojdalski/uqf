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
}

CORE_PY = (
    Path(__file__).resolve().parents[2]
    / "torq_orchestrator"
    / "src"
    / "torq_orchestrator"
    / "core.py"
)

#: Repository root, for the q files that own the ETL-side schemas.
REPO = Path(__file__).resolve().parents[3]

#: q type characters, as they appear in a source contract's `types` string.
_CHAR_TO_CATALOG = {
    "p": QType.TIMESTAMP,
    "n": QType.TIMESPAN,
    "f": QType.FLOAT,
    "j": QType.LONG,
    "s": QType.SYMBOL,
    "b": QType.BOOLEAN,
}


def _schema_text(const: str) -> str:
    """Pull one `NAME = (...)` schema constant out of core.py and splice its
    string fragments together, the way Python would.
    """
    source = CORE_PY.read_text()
    m = re.search(rf"^{const} = \((.*?)\n\)", source, re.S | re.M)
    if m is None:
        m = re.search(rf'^{const} = ("(?:[^"\\]|\\.)*")', source, re.M)
        assert m, f"{const} not found in {CORE_PY}"
        return m.group(1).strip('"')
    return "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1)))


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


def test_core_py_is_where_this_test_expects_it():
    """If core.py moves, this gate must fail loudly rather than skip."""
    assert CORE_PY.is_file(), f"expected the generated schemas at {CORE_PY}"


@pytest.mark.parametrize(
    ("table_name", "const"),
    [
        ("trades", "TRADES_TABLE_SCHEMA"),
        ("position", "POSITION_TABLE_SCHEMA"),
        ("execution_quality", "EXECUTION_QUALITY_TABLE_SCHEMA"),
        ("quotes", "QUOTES_TABLE_SCHEMA"),
        ("crypto_trades", "CRYPTO_TRADES_TABLE_SCHEMA"),
    ],
)
def test_catalog_matches_the_generated_schema(table_name, const):
    expected = _parse(_schema_text(const))
    actual = TABLES[table_name].columns
    assert actual == expected, (
        f"{table_name} catalog has drifted from {const}; "
        f"missing={set(expected) - set(actual)} extra={set(actual) - set(expected)}"
    )


#: Catalog tables whose schema is owned by a q file rather than by core.py.
#: (table name) -> (q file, how to read the columns out of it)
_Q_OWNED = {
    "etl_coverage": lambda: _q_table_schema(Path("src/etl/core/coverage.q"), "etl_coverage"),
    "demo_deals": lambda: _q_contract_columns(
        Path("src/etl/sources/demo_deals.q"), "fields", "types"
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
    checked = {
        "trades",
        "position",
        "execution_quality",
        "quotes",
        "crypto_trades",
    } | set(_Q_OWNED)
    unchecked = set(TABLES) - checked
    assert not unchecked, (
        f"catalog table(s) {sorted(unchecked)} have no drift check. Add the schema "
        f"constant to test_catalog_matches_the_generated_schema, or the q file to "
        f"_Q_OWNED - do not just add it here."
    )
