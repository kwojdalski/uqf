"""The set of browsable column types has one authority: catalog.QType (#1364).

`uqs job new --schema qty:int` is accepted by the scaffold, the gates then
require the new table to be described, and the browser's catalog used to
answer 500 for every table because it did not know `int`. Three homes held
the set - the scaffold's types, catalog.QType, and the browser's api.ts - and
nothing compared them. These tests do, from the declarations in
src/etl/plant_tables.q rather than a list typed here.
"""

from __future__ import annotations

import datetime as dt
import typing
from pathlib import Path

import polars as pl
import pytest
from fastapi.testclient import TestClient

from uqf_frontend import models, queries
from uqf_frontend.app import _rows, create_app
from uqf_frontend.catalog import UNFILTERABLE, QType, _qtype
from uqf_frontend.config import Settings
from uqf_frontend.errors import ValidationFailed
from uqf_frontend.gateway import FakeGateway, KolaGateway
from uqf_frontend.queries import coerce
from uqs.model.schemas import _DEFINITION
from uqs.paths import TABLES_FILE
from uqs.scaffold.columns import TYPES, definition_columns, type_char

ROOT = Path(__file__).resolve().parents[3]
BROWSE_Q = ROOT / "scripts" / "torqcode" / "gateway" / "browse.q"


def test_a_scaffolded_int_column_does_not_take_the_catalog_down():
    """The consequence, end to end: `qty:int` described and browsed. On master
    this was a ValueError out of /catalog."""
    gw = FakeGateway(
        {
            queries.CATALOG: [{"table": "fills", "description": "Fills"}],
            queries.SCHEMA: [
                {"table": "fills", "column": "sym", "kind": "s"},
                {"table": "fills", "column": "qty", "kind": "i"},
                {"table": "fills", "column": "lots", "kind": "h"},
                {"table": "fills", "column": "flag", "kind": "c"},
                {"table": "fills", "column": "day", "kind": "d"},
                {"table": "fills", "column": "at", "kind": "t"},
            ],
        }
    )
    client = TestClient(create_app(gateway=gw, settings=Settings(max_rows=5000)))
    response = client.get("/catalog")
    assert response.status_code == 200, response.text
    columns = {c["name"]: c for c in response.json()["tables"][0]["columns"]}
    assert {n: c["type"] for n, c in columns.items()} == {
        "sym": "symbol",
        "qty": "int",
        "lots": "short",
        "flag": "char",
        "day": "date",
        "at": "time",
    }
    assert columns["qty"]["filterable"] and columns["day"]["filterable"]
    assert columns["flag"]["filterable"] is False


def test_every_type_the_scaffold_accepts_is_a_browsable_type():
    """The scaffold may only offer types the browser can show."""
    missing = [t.name for t in TYPES if _qtype(t.char) is None]
    assert not missing, f"scaffold types the browser's catalog cannot type: {missing}"


def test_every_plant_table_column_type_is_a_browsable_type():
    """From plant_tables.q itself: a described table never reaches a column
    the catalog refuses."""
    text = (ROOT / TABLES_FILE).read_text()
    tables = {m.group(1): m.group(0) for m in _DEFINITION.finditer(text)}
    assert len(tables) >= 30
    bad = {
        f"{table}.{col}": literal
        for table, definition in tables.items()
        for col, literal in definition_columns(definition)
        if _qtype(type_char(literal)) is None
    }
    assert not bad, bad


def test_column_info_type_is_exactly_the_qtype_words():
    words = set(typing.get_args(models.ColumnType))
    assert words == {q.value for q in QType}


@pytest.mark.parametrize(
    ("qtype", "value", "expected"),
    [
        (QType.INT, 5, 5),
        (QType.SHORT, -7, -7),
        (QType.DATE, "2026-09-16", dt.date(2026, 9, 16)),
        (QType.TIME, "14:30:00", dt.time(14, 30)),
        (QType.TIME, "14:30:00.25", dt.time(14, 30, 0, 250000)),
    ],
)
def test_coerce_the_new_types(qtype, value, expected):
    assert coerce(value, qtype, "c", as_list=False) == expected


@pytest.mark.parametrize(
    ("qtype", "value", "message"),
    [
        (QType.INT, 2**31, "outside"),
        (QType.INT, 1.5, "expected an integer"),
        (QType.INT, True, "expected an integer"),
        (QType.SHORT, 40000, "outside"),
        (QType.DATE, "2026-13-01", "not one"),
        (QType.DATE, "20260101", "YYYY-MM-DD"),
        (QType.TIME, "14:30", "HH:MM:SS"),
        (QType.TIME, "14:30:00.1234", "HH:MM:SS"),
        (QType.TIME, "25:00:00", "not one"),
    ],
)
def test_coerce_refuses_a_bad_value_for_the_new_types(qtype, value, message):
    with pytest.raises(ValidationFailed, match=message):
        coerce(value, qtype, "c", as_list=False)


def test_an_in_list_for_a_short_column_is_a_short_vector():
    """KDB-X refuses `1 2h in 1 2j` with 'type, so the longs a list of ints
    becomes cannot filter a short column (#1391): the list goes as an Int16
    series, which kola sends as a short vector."""
    got = coerce([1, -3], QType.SHORT, "c", as_list=True)
    assert got.dtype == pl.Int16 and got.to_list() == [1, -3]
    with pytest.raises(ValidationFailed, match="outside"):
        coerce([1, 40000], QType.SHORT, "c", as_list=True)


def test_unfilterable_types_are_exactly_list_and_char():
    assert UNFILTERABLE == {QType.LIST, QType.CHAR}


@pytest.fixture(scope="module")
def browse_port(start_q, tmp_path_factory):
    script = tmp_path_factory.mktemp("types") / "browse.q"
    script.write_text(BROWSE_Q.read_text())
    with start_q(str(script)) as port:
        yield port


#: A table with one of each new type, filtered by the pair .uqf.browse builds.
FILTER = """{[c;o;v]
    t:([] qi:1 2 3i; qh:1 2 3h; qd:2026.01.01+0 1 2; qt:00:00:01.000 00:00:02.250 00:00:03.000);
    p:.uqf.browse_pair[o;v];
    exec qi from ?[t;enlist (p 0;c;p 1);0b;()]}"""


@pytest.mark.parametrize(
    ("column", "qtype", "op", "value", "matched"),
    [
        ("qi", QType.INT, "eq", 2, [2]),
        ("qi", QType.INT, "in", [1, 3], [1, 3]),
        ("qh", QType.SHORT, "gt", 1, [2, 3]),
        ("qh", QType.SHORT, "in", [1, 3], [1, 3]),
        ("qh", QType.SHORT, "in", [2], [2]),
        ("qd", QType.DATE, "eq", "2026-01-02", [2]),
        ("qd", QType.DATE, "in", ["2026-01-01", "2026-01-03"], [1, 3]),
        ("qt", QType.TIME, "ge", "00:00:02.250", [2, 3]),
        ("qt", QType.TIME, "in", ["00:00:01", "00:00:03"], [1, 3]),
    ],
)
def test_the_new_types_filter_correctly_over_a_real_kola_connection(
    browse_port, column, qtype, op, value, matched
):
    v = coerce(value, qtype, column, as_list=op == "in")
    gw = KolaGateway(Settings(port=browse_port))
    assert list(gw.call(FILTER, column, op, v)) == matched


def test_rows_of_the_new_types_serialise(browse_port):
    gw = KolaGateway(Settings(port=browse_port))
    got = gw.call(
        '{[u] ([] i:1 2i; h:3 4h; c:"ab"; d:2026.01.01 2026.01.02; t:00:00:01.250 00:00:02.000)}', 0
    )
    assert _rows(got) == [
        {"i": 1, "h": 3, "c": "a", "d": dt.date(2026, 1, 1), "t": dt.time(0, 0, 1, 250000)},
        {"i": 2, "h": 4, "c": "b", "d": dt.date(2026, 1, 2), "t": dt.time(0, 0, 2)},
    ]


def test_the_catalog_s_characters_are_q_s_for_every_qtype_but_list():
    """`_QTYPE_BY_CHAR` is q's own character -> name map (uqs.generated.q_facts,
    #1785) kept to QType's words: every QType but LIST has its q character, and a
    q type QType does not name - byte, real, month, ... - is refused (None)."""
    from uqf_frontend.catalog import _QTYPE_BY_CHAR
    from uqs.generated.q_facts import Q_TYPE_NAMES

    assert {q.value for q in _QTYPE_BY_CHAR.values()} == {q.value for q in QType} - {"list"}
    for char, qtype in _QTYPE_BY_CHAR.items():
        assert Q_TYPE_NAMES[char] == qtype.value
    unsupported = {c for c, n in Q_TYPE_NAMES.items() if n not in {q.value for q in QType}}
    assert unsupported == set("xemzuv")
    assert all(_qtype(c) is None for c in unsupported)
    assert _qtype("F") is QType.LIST and _qtype(" ") is QType.LIST
