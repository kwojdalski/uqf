"""The real IPC classes, KolaGateway and KolaFleet, against a live q process.

Every other frontend test runs through FakeGateway and FakeFleet, which is
right for testing the API layer - and means the two classes that actually
open connections sat at 68% and 72%, their error paths never run. What those
paths decide is what an operator sees: an unreachable gateway, a q error, the
EOD reload window and a timeout are four different messages with four
different next steps, told apart only by classifying q's error text.

The q process is plain q, not a TorQ gateway. For `route` it defines a
`.gw.syncexec` that simply evaluates the query list it receives - which is
enough to check the thing worth checking there: that the program crosses the
wire as a CHAR VECTOR q can `value`. kola maps a Python str to a symbol, and a
symbol at the head of the list makes `value` look for a variable named the
whole lambda; gateway.py sends bytes for that reason, and nothing tested it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from uqf_frontend.config import Process, Settings
from uqf_frontend.errors import GatewayReloading, GatewayUnavailable, QueryRejected, QueryTimedOut
from uqf_frontend.fleet import KolaFleet
from uqf_frontend.gateway import KolaGateway, _classify

SERVER_SCRIPT = """
/ Stands in for TorQ's gateway entry point: evaluate the query list as given.
.gw.syncexec:{[query;tiers] value query};
/ How many connections have been opened, and a gateway restart as one
/ client sees it: every OTHER client's connection closed under it.
opens:0;
.z.po:{opens+:1};
drop_others:{[unused] h:(key .z.W) except .z.w; hclose each h; count h};
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
    """A failed call's handle is discarded, never returned to the pool - and
    it must be disconnected too, or the licence's connection cap is reached
    one error at a time."""
    gw = KolaGateway(Settings(port=q_port))
    for _ in range(20):
        with pytest.raises(QueryRejected):
            gw.call("{'`nope}", 1)
    assert gw.call("{x}", 1) == 1


def test_requests_reuse_a_connection_rather_than_opening_one_each(q_port):
    """#635: connect-per-call opened 72-84 connections a minute for two tabs
    on the Ops views. Ten calls now open none beyond the first."""
    gw = KolaGateway(Settings(port=q_port))
    before = gw.call("{opens}", 0)
    for i in range(10):
        gw.call("{x}", i)
    assert gw.call("{opens}", 0) == before


def test_a_connection_the_gateway_drops_is_reopened_by_the_next_request(q_port):
    """The acceptance case, against a real q: the server closes the pooled
    connection. The request that finds it dead fails as transient - the
    browser keeps polling - and the next one reconnects."""
    gw = KolaGateway(Settings(port=q_port))
    gw.call("{x}", 1)
    KolaGateway(Settings(port=q_port)).call("{drop_others x}", 0)
    with pytest.raises(GatewayUnavailable):
        gw.call("{x}", 2)
    assert gw.call("{x}", 3) == 3


BROWSE_Q = Path(__file__).resolve().parents[3] / "scripts" / "torqcode" / "gateway" / "browse.q"


@pytest.fixture(scope="module")
def browse_port(start_q, tmp_path_factory) -> Any:
    script = tmp_path_factory.mktemp("browse") / "browse.q"
    script.write_text(BROWSE_Q.read_text())
    with start_q(str(script)) as port:
        yield port


def test_a_guid_filter_value_reaches_q_as_a_guid(browse_port):
    """#1042: kola refuses a uuid.UUID and sends a str as a symbol, which a
    guid column rejects with 'type. coerce sends bytes; over a real kola
    connection they must come out of .uqf.browse's filter as guids - for eq a
    guid atom, for `in` a guid vector."""
    from uqf_frontend.catalog import QType
    from uqf_frontend.queries import coerce

    one = coerce("8c6b8b64-6815-6084-0a3e-178401251b68", QType.GUID, "run_id", as_list=False)
    many = coerce(
        ["8c6b8b64-6815-6084-0a3e-178401251b68", "11111111-2222-3333-4444-555555555555"],
        QType.GUID,
        "run_id",
        as_list=True,
    )
    gw = KolaGateway(Settings(port=browse_port))
    types = gw.call("{[o;v] {type last x} each .uqf.browse_pair'[o;v]}", ["eq", "in"], [one, many])
    assert list(types) == [-2, 2]


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
    """A reload is transient and retried, a timeout says the query is
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
