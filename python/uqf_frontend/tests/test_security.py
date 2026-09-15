"""The F-14 property, asserted directly: client input never reaches q as text.

These are the tests that matter most in this package. Everything else is
plumbing; this is the boundary.
"""

from __future__ import annotations

from uqf_frontend import queries


def test_program_text_is_always_a_package_constant(client, gw):
    """Whatever the caller sends, the q program is one of ours verbatim."""
    client.post("/query", json={"table": "trades", "filters": [], "limit": 10})
    assert gw.last_program == queries.SELECT


def test_hostile_symbol_value_travels_as_an_argument_not_as_text(client, gw):
    """A value containing q code must appear only in the argument list."""
    hostile = "`trades; delete from `trades"
    resp = client.post(
        "/query",
        json={
            "table": "trades",
            "filters": [{"column": "sym", "op": "eq", "value": hostile}],
            "limit": 10,
        },
    )
    assert resp.status_code == 200
    program, args = gw.calls[-1]
    assert program == queries.SELECT
    assert hostile not in program
    # it is present as data, in the values list, and nowhere else
    assert args == ("trades", ["sym"], ["eq"], [hostile], 10)


def test_unknown_table_is_refused_before_any_ipc(client, gw):
    resp = client.post("/query", json={"table": "sys; exit 0", "filters": []})
    assert resp.status_code == 422
    assert gw.calls == [], "nothing may be sent to q after a validation failure"


def test_unknown_column_is_refused_before_any_ipc(client, gw):
    resp = client.post(
        "/query",
        json={"table": "trades", "filters": [{"column": "; exit 0", "op": "eq", "value": "x"}]},
    )
    assert resp.status_code == 422
    assert gw.calls == []


def test_unknown_operator_is_refused_by_the_schema(client, gw):
    resp = client.post(
        "/query",
        json={"table": "trades", "filters": [{"column": "sym", "op": "evil", "value": "x"}]},
    )
    assert resp.status_code == 422
    assert gw.calls == []


def test_vector_column_cannot_be_filtered_on(client, gw):
    resp = client.post(
        "/query",
        json={"table": "quotes", "filters": [{"column": "bid_prices", "op": "eq", "value": 1.0}]},
    )
    assert resp.status_code == 422
    assert "vector" in resp.json()["detail"]
    assert gw.calls == []


def test_every_catalog_operator_exists_in_the_q_program():
    """A catalog operator with no q-side counterpart would fail only at
    runtime, against a live gateway. Catch it here instead.
    """
    from uqf_frontend.catalog import OPERATORS

    ops_line = next(line for line in queries.SELECT.splitlines() if "ops:" in line)
    for op in OPERATORS:
        assert f"`{op}" in ops_line or f"{op}`" in ops_line or op in ops_line, op
