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
