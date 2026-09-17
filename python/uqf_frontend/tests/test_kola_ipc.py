"""The real IPC classes, KolaGateway and KolaFleet, against a live q process.

Every other frontend test runs through FakeGateway and FakeFleet, which is
right for testing the API layer - and means the two classes that actually
open connections sat at 68% and 72%, their error paths never run. What those
paths decide is what an operator sees: an unreachable gateway, a q error, the
EOD reload window and a timeout are four different messages with four
different next steps (FE-12), told apart only by classifying q's error text.

The q process is plain q, not a TorQ gateway. For `route` it defines a
`.gw.syncexec` that simply evaluates the query list it receives - which is
enough to check the thing worth checking there: that the program crosses the
wire as a CHAR VECTOR q can `value`. kola maps a Python str to a symbol, and a
symbol at the head of the list makes `value` look for a variable named the
whole lambda; gateway.py sends bytes for that reason, and nothing tested it.
"""

from __future__ import annotations

from typing import Any

import pytest

from uqf_frontend.config import Process, Settings
from uqf_frontend.errors import GatewayReloading, GatewayUnavailable, QueryRejected, QueryTimedOut
from uqf_frontend.fleet import KolaFleet
from uqf_frontend.gateway import KolaGateway, _classify

SERVER_SCRIPT = """
/ Stands in for TorQ's gateway entry point: evaluate the query list as given.
.gw.syncexec:{[query;tiers] value query};
"""


@pytest.fixture(scope="module")
def q_port(start_q, tmp_path_factory) -> Any:
    script = tmp_path_factory.mktemp("gw") / "gateway.q"
    script.write_text(SERVER_SCRIPT)
    with start_q(str(script)) as port:
        yield port


# --------------------------------------------------------------- KolaGateway


def test_call_evaluates_a_program_with_arguments(q_port):
    assert KolaGateway(Settings(port=q_port)).call("{x+y}", 2, 3) == 5


def test_route_sends_the_program_as_something_q_can_value(q_port):
    """The bytes-not-str rule. Sent as a str, the lambda would arrive as a
    symbol and `value` would look for a variable with that name."""
    out = KolaGateway(Settings(port=q_port)).route("{[a;b] a*b}", (6, 7), ["rdb"])
    assert out == 42


def test_an_unreachable_gateway_is_unavailable_not_rejected(q_port, unused_port):
    """Nothing is listening, so the query was never judged. Reporting it as
    rejected would send the operator to debug a query that is fine."""
    with pytest.raises(GatewayUnavailable, match="not reachable"):
        KolaGateway(Settings(port=unused_port)).call("1")


def test_a_q_error_is_a_rejection_that_carries_q_s_message(q_port):
    with pytest.raises(QueryRejected, match="nope"):
        KolaGateway(Settings(port=q_port)).call("{'`nope}", 1)


def test_a_failing_call_does_not_leak_its_connection(q_port):
    """Connect-per-call only protects against a wedged handle if the handle
    is released on failure too - otherwise the licence's connection cap is
    reached one error at a time."""
    gw = KolaGateway(Settings(port=q_port))
    for _ in range(20):
        with pytest.raises(QueryRejected):
            gw.call("{'`nope}", 1)
    assert gw.call("{x}", 1) == 1


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("stop: query timeout", QueryTimedOut),
        ("request timed out", QueryTimedOut),
        ("EOD in progress", GatewayReloading),
        ("gateway reload running", GatewayReloading),
        ("service not available", GatewayReloading),
        ("type", QueryRejected),
    ],
)
def test_q_error_text_is_classified_into_what_the_operator_does_next(text, kind):
    """FE-12: a reload is transient and retried, a timeout says the query is
    too heavy, anything else is the query's own fault."""
    assert isinstance(_classify(Exception(text)), kind)


def test_classification_ignores_case():
    assert isinstance(_classify(Exception("TIMEOUT")), QueryTimedOut)


# ----------------------------------------------------------------- KolaFleet


def _fleet(q_port: int, *extra: Process) -> KolaFleet:
    return KolaFleet(Settings(processes=(Process("rdb1", "localhost", q_port), *extra)))


def test_one_reaches_a_configured_process(q_port):
    r = _fleet(q_port).one("rdb1", "{x*2}", 21)
    assert (r.ok, r.value, r.process) == (True, 42, "rdb1")


def test_one_reports_an_unconfigured_name_rather_than_guessing_an_address(q_port):
    r = _fleet(q_port).one("typo1", "{x}", 1)
    assert not r.ok
    assert r.error == "not a configured process"


def test_one_down_process_does_not_blank_the_fan_out(q_port, unused_port):
    """The module's design rule. Nine processes and a named tenth is useful;
    an empty view because one is down is not."""
    fleet = _fleet(q_port, Process("hdb1", "localhost", unused_port))
    results = {r.process: r for r in fleet.per_process("{x}", 7)}
    assert results["rdb1"].ok and results["rdb1"].value == 7
    assert not results["hdb1"].ok
    assert "not reachable" in (results["hdb1"].error or "")


def test_a_process_error_is_reported_against_that_process(q_port):
    r = _fleet(q_port).one("rdb1", "{'`broken}", 1)
    assert not r.ok
    assert "broken" in (r.error or "")


def test_probe_reaches_an_address_nothing_declared(q_port):
    """Fleet health probes every process.csv row by address, including ones
    not in the fan-out configuration."""
    r = _fleet(q_port).probe("localhost", q_port, "{x+1}", 1)
    assert (r.ok, r.value, r.process) == (True, 2, f"localhost:{q_port}")


def test_probe_reports_an_unreachable_address(q_port, unused_port):
    port = unused_port
    r = _fleet(q_port).probe("localhost", port, "{x}", 1)
    assert not r.ok
    assert r.process == f"localhost:{port}"


def test_the_process_names_are_the_configured_ones_in_order(q_port):
    fleet = _fleet(q_port, Process("hdb1", "localhost", 1))
    assert fleet.processes == ("rdb1", "hdb1")
