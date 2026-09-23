"""The FE-14 property, asserted directly: client input never reaches q as text.

These are the tests that matter most in this package. Everything else is
plumbing; this is the boundary.
"""

from __future__ import annotations

from uqf_frontend import queries

#: Programs this package sends that carry no caller input: the catalog's own
#: two questions, asked once to build the whitelist. A refusal test asserts
#: that nothing ELSE was sent - "no IPC at all" stopped being the property
#: when the catalog moved to the stack, and was never the one that mattered.
#: FE-14 is about the caller's bytes, and neither of these carries any.
CATALOG_PROGRAMS = frozenset({queries.CATALOG, queries.SCHEMA})


def _caller_input_reached_q(gw, needle: str) -> bool:
    """Did anything the caller sent leave this process?"""
    for program, args in gw.calls:
        if program not in CATALOG_PROGRAMS and (needle in program or needle in str(args)):
            return True
    for program, args, _ in gw.routed:
        if program not in CATALOG_PROGRAMS and (needle in program or needle in str(args)):
            return True
    return False


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
    program, args, tiers = gw.routed[-1]
    assert program == queries.SELECT
    assert hostile not in program
    # present as data, in the values list, and nowhere else
    assert args == ("trades", ["sym"], ["eq"], [hostile], 10)
    assert tiers == ["rdb", "hdb"]


def test_unknown_table_is_refused_without_the_caller_reaching_q(client, gw):
    hostile = "sys; exit 0"
    resp = client.post("/query", json={"table": hostile, "filters": []})
    assert resp.status_code == 422
    assert not _caller_input_reached_q(gw, hostile), (
        "nothing carrying the caller's input may be sent after a validation failure"
    )
    assert queries.SELECT not in [p for p, _, _ in gw.routed]


def test_unknown_column_is_refused_without_the_caller_reaching_q(client, gw):
    hostile = "; exit 0"
    resp = client.post(
        "/query",
        json={"table": "trades", "filters": [{"column": hostile, "op": "eq", "value": "x"}]},
    )
    assert resp.status_code == 422
    assert not _caller_input_reached_q(gw, hostile)
    assert queries.SELECT not in [p for p, _, _ in gw.routed]


def test_unknown_operator_is_refused_by_the_schema(client, gw):
    resp = client.post(
        "/query",
        json={"table": "trades", "filters": [{"column": "sym", "op": "evil", "value": "x"}]},
    )
    assert resp.status_code == 422
    assert gw.routed == []


def test_vector_column_cannot_be_filtered_on(client, gw):
    resp = client.post(
        "/query",
        json={"table": "quotes", "filters": [{"column": "bid_prices", "op": "eq", "value": 1.0}]},
    )
    assert resp.status_code == 422
    assert "vector" in resp.json()["detail"]
    assert not _caller_input_reached_q(gw, "bid_prices")
    assert queries.SELECT not in [p for p, _, _ in gw.routed]


def test_every_catalog_operator_exists_in_the_q_program():
    """A catalog operator with no q-side counterpart would fail only at
    runtime, against a live gateway. Catch it here instead.
    """
    from uqf_frontend.catalog import OPERATORS

    ops_line = next(line for line in queries.SELECT.splitlines() if "ops:" in line)
    for op in OPERATORS:
        assert f"`{op}" in ops_line or f"{op}`" in ops_line or op in ops_line, op


def test_the_lambda_is_sent_as_bytes_not_as_a_symbol(gw):
    """kola maps a Python str to a q *symbol*. A symbol at the head of the
    query list makes the backend's ``value`` try to resolve a variable named
    the entire lambda text, so the program must go as a char vector.

    Asserted on KolaGateway's own wire form rather than through the fake,
    because this is a serialisation property, not an app-level one.
    """
    from uqf_frontend.config import Settings
    from uqf_frontend.gateway import KolaGateway

    sent: dict = {}

    class Spy(KolaGateway):
        def _exec(self, program, args):
            sent["program"] = program
            sent["args"] = args
            return None

    Spy(Settings()).route(queries.SELECT, ("trades", [], [], [], 0), ["rdb"])
    assert sent["program"] == ".gw.syncexec"
    query_list, tiers = sent["args"]
    assert isinstance(query_list[0], bytes), "the lambda must be a char vector, not a symbol"
    assert query_list[0] == queries.SELECT.encode()
    assert tiers == ["rdb"]
