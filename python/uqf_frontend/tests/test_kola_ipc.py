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

from uqf_frontend.config import Settings
from uqf_frontend.errors import GatewayReloading, GatewayUnavailable, QueryRejected, QueryTimedOut
from uqf_frontend.fleet import KolaFleet
from uqf_frontend.gateway import KolaGateway, _classify
from uqf_frontend.stack import Process

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
    # Stand-ins for what a TorQ gateway gives .uqf.browse: a policy capping a
    # read at 3 rows, and a getdata that returns as many rows as it is asked.
    stand_ins = (
        "\n.checkinputs.querypolicyfor:{[t] `maxrange`maxrows!(1D;3)};"
        "\n.dataaccess.getdata:{[req] ([] n:til req`sublist)};\n"
    )
    script.write_text(BROWSE_Q.read_text() + stand_ins)
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


#: A timespan column as a table holds it, and a where clause built from one
#: .uqf.browse_pair - the filter getdata would apply.
SPAN_FILTER = """{[o;v]
    t:([] s:"n"$1000 1234 1500 1999 2000);
    p:.uqf.browse_pair[o;v];
    exec "j"$s from ?[t;enlist (p 0;`s;p 1);0b;()]}"""


@pytest.mark.parametrize(
    ("op", "value", "matched"),
    [
        ("eq", 1234, [1234]),
        ("lt", 1500, [1000, 1234]),
        ("ge", 1500, [1500, 1999, 2000]),
        ("in", [1234, 1999], [1234, 1999]),
    ],
)
def test_a_timespan_filter_reaches_q_to_the_nanosecond(browse_port, op, value, matched):
    """#1329: a timespan filter went to q as a timedelta, rounded to the
    microsecond - `eq 1234` asked for 1000 ns and matched nothing, `lt 1500`
    asked for < 2000 - and since #1317 it was refused instead. It now crosses
    kola as nanoseconds, and .uqf.browse compares with the column exactly."""
    from uqf_frontend.catalog import QType
    from uqf_frontend.queries import coerce

    v = coerce(value, QType.TIMESPAN, "s", as_list=op == "in")
    gw = KolaGateway(Settings(port=browse_port))
    assert list(gw.call(SPAN_FILTER, op, v)) == matched


#: A `.z.p`-stamped column, and a where clause built from one
#: .uqf.browse_pair - the filter getdata would apply.
STAMP_FILTER = """{[o;v]
    t:([] p:2026.10.14D10:00:00.000001 + 0 1234 1999 2000);
    p:.uqf.browse_pair[o;v];
    exec "j"$p-2026.10.14D10:00:00.000001 from ?[t;enlist (p 0;`p;p 1);0b;()]}"""


@pytest.mark.parametrize(
    ("op", "value", "matched"),
    [
        ("eq", "2026-10-14T10:00:00.000002234Z", [1234]),
        ("lt", "2026-10-14T12:00:00.000002999+02:00", [0, 1234]),
        ("ge", "2026-10-14T10:00:00.000002999Z", [1999, 2000]),
        ("in", ["2026-10-14T10:00:00.000002234Z", "2026-10-14T10:00:00.000002999Z"], [1234, 1999]),
    ],
)
def test_a_timestamp_filter_reaches_q_to_the_nanosecond(browse_port, op, value, matched):
    """#1371: a timestamp filter went to q as a datetime, which stops at the
    microsecond, and since #1317 a finer one was refused - so the time the
    Desk showed for a `.z.p`-stamped row could not be asked for. It now
    crosses kola as nanoseconds, and .uqf.browse compares with the column
    exactly."""
    from uqf_frontend.catalog import QType
    from uqf_frontend.queries import coerce

    v = coerce(value, QType.TIMESTAMP, "p", as_list=op == "in")
    gw = KolaGateway(Settings(port=browse_port))
    assert list(gw.call(STAMP_FILTER, op, v)) == matched


def test_a_timestamp_bound_sets_the_read_window_to_the_nanosecond(browse_port):
    """The `time` filters also bound getdata's window (browse_window), which
    a one-item timestamp vector must not turn into a vector."""
    from uqf_frontend.catalog import QType
    from uqf_frontend.queries import coerce

    lo = coerce("2026-10-14T10:00:00.000001234Z", QType.TIMESTAMP, "time", as_list=False)
    hi = coerce("2026-10-14T10:00:00.000001999Z", QType.TIMESTAMP, "time", as_list=False)
    gw = KolaGateway(Settings(port=browse_port))
    window = gw.call("{[a;b] .Q.s1 .uqf.browse_window[`time`time;`ge`lt;(a;b);1D;.z.p]}", lo, hi)
    assert window == "2026.10.14D10:00:00.000001234 2026.10.14D10:00:00.000001999"


def test_a_timestamp_reaches_the_browser_to_the_nanosecond(browse_port):
    """#1371: through real kola and _rows, two `.z.p` stamps 765 ns apart
    stay apart, and 0Wp/-0Wp go as infinities rather than as the dates of
    2262 and 1707 their bit patterns spell."""
    from uqf_frontend.app import _rows

    gw = KolaGateway(Settings(port=browse_port))
    got = gw.call(
        "{[unused] ([] p:2026.10.14D10:00:00.000001234 2026.10.14D10:00:00.000001999,"
        '("p"$0W -0W),0Np)}',
        0,
    )
    assert _rows(got) == [
        {"p": "2026-10-14T10:00:00.000001234Z"},
        {"p": "2026-10-14T10:00:00.000001999Z"},
        {"p": "inf"},
        {"p": "-inf"},
        {"p": None},
    ]


def test_infinities_reach_the_browser_as_infinities_not_nulls(browse_port):
    """#1330: kola turns a long's 0W into null, and pydantic writes a float's
    inf as null, so an infinite level showed as a missing one. Through
    .uqf.browse_infinite and _rows each comes out as INFINITY, signed, and a
    null stays null."""
    from uqf_frontend.app import _rows

    gw = KolaGateway(Settings(port=browse_port))
    got = gw.call(
        '{[unused] .uqf.browse_infinite ([] l:1 0W -0W 0N; f:1.5 0w -0w 0n; s:"n"$5 0W -0W 0N)}',
        0,
    )
    assert _rows(got) == [
        {"l": 1.0, "f": 1.5, "s": 5},
        {"l": "inf", "f": "inf", "s": "inf"},
        {"l": "-inf", "f": "-inf", "s": "-inf"},
        {"l": None, "f": None, "s": None},
    ]


def test_browse_brings_back_the_cap_the_policy_set(browse_port):
    """#1255: over a real kola connection .uqf.browse's answer is the rows
    and the cap it read under - the policy's 3, not the 50 asked for - so
    /query can tell a policy cut from a complete result."""
    gw = KolaGateway(Settings(port=browse_port, data_user="browser", data_passwd="pw"))
    read = gw.browse("t", [], [], [], 50, ["rdb"])
    assert (read.cap, len(read.rows)) == (3, 3)


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("stop: query timeout", QueryTimedOut),
        ("request timed out", QueryTimedOut),
        ("error: unable to query multiple servers during eod reload", GatewayReloading),
        ("error: query did not return prior to eod reload", GatewayReloading),
        ("service not available", QueryRejected),
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
