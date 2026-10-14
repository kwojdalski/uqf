"""/coverage end to end over real kola, against the ledger FILE (#1081).

The API tests stage .qetl.coverage's answers through FakeGateway. What they
cannot show is the round trip this route depends on: .qetl.coverage.claims
reads the ledger the workers persist, its rows arrive as a DataFrame, are
sent BACK to the gateway as an argument, and .qetl.coverage.compose and gaps
read them there as a table. Here the q side is real - the files gateway1
loads, and a ledger file in a status directory, written as a worker's
durable_set writes it, holding two adjacent claims.

It used to stand two tiers in for the ledger, each holding half a range. No
tier holds etl_coverage in the stack: that is the bug #1081 fixed.
"""

from __future__ import annotations

from typing import Any

import pytest

from uqf_frontend.app import _coverage
from uqf_frontend.config import Settings
from uqf_frontend.gateway import KolaGateway
from uqs.interpreter import PEACHQ, q_impl
from uqs.stack.procs import VENDORED_LOAD_OVERLAY

# What gateway1 loads from src/ is declared once, in VENDORED_LOAD_OVERLAY. This
# test stands in for gateway1, so it loads exactly that list (#1370): dropping
# a file from the overlay now fails here instead of breaking /coverage at
# runtime. The uqs_catalog.q entry is under ${UQF_SCRIPTS}, not src/, and
# serves the catalog endpoints this server does not exercise.
_ROOT = "${UQF_ROOT}/"
GATEWAY_SRC_LOADS = [
    path.removeprefix(_ROOT)
    for path in VENDORED_LOAD_OVERLAY["gateway1"][1]
    if path.startswith(_ROOT)
]

SERVER_SCRIPT = (
    """
setenv[`UQF_STATUS_DIR;"{status}"];
"""
    + "".join(f"\\l {path}\n" for path in GATEWAY_SRC_LOADS)
    + """
/ The ledger a worker would have persisted: the 13th and the 14th, v1.
row:{{[f;t] ([] dataset:enlist `trades; partition:enlist `; source_version:enlist `v1;
    range_from:enlist f; range_to:enlist t; rows_published:enlist 1;
    recorded_at:enlist 2026.09.01D00:00; superseded_at:enlist 0Wp; run_id:enlist 0Ng)}};
.qetl.job.bounded.state.durable_set[.qetl.coverage.ledger_path[];
    row[2026.09.13D00:00;2026.09.14D00:00],row[2026.09.14D00:00;2026.09.15D00:00]];
"""
)


@pytest.fixture(scope="module")
def gateway(start_q, tmp_path_factory) -> Any:
    if q_impl() == PEACHQ:
        pytest.skip("PeachQ cannot load a nested `\\d` such as intervals.q's .qetl.coverage")
    root = tmp_path_factory.mktemp("cov")
    status = root / "status"
    status.mkdir()
    script = root / "gateway.q"
    script.write_text(SERVER_SCRIPT.format(status=status))
    with start_q(str(script)) as port:
        yield KolaGateway(Settings(port=port))


def test_two_claims_in_the_ledger_compose_into_one_interval(gateway):
    body = _coverage(gateway, "trades", "", "v1", None, None)
    assert [(c.range_from, c.range_to) for c in body.covered] == [
        ("2026-09-13T00:00:00.000000000Z", "2026-09-15T00:00:00.000000000Z")
    ]


def test_the_gap_is_what_no_claim_covers(gateway):
    body = _coverage(gateway, "trades", "", "v1", "2026-09-12T00:00:00Z", "2026-09-16T00:00:00Z")
    assert [(g.range_from, g.range_to) for g in body.gaps] == [
        ("2026-09-12T00:00:00.000000000Z", "2026-09-13T00:00:00.000000000Z"),
        ("2026-09-15T00:00:00.000000000Z", "2026-09-16T00:00:00.000000000Z"),
    ]
    assert body.complete is False


def test_a_covered_range_is_complete(gateway):
    body = _coverage(gateway, "trades", "", "v1", "2026-09-13T00:00:00Z", "2026-09-15T00:00:00Z")
    assert body.gaps == []
    assert body.complete is True


def test_another_release_has_no_coverage_at_all(gateway):
    """An empty result crosses the wire too, and gaps is then the whole range."""
    body = _coverage(gateway, "trades", "", "v2", "2026-09-13T00:00:00Z", "2026-09-14T00:00:00Z")
    assert body.covered == []
    assert [(g.range_from, g.range_to) for g in body.gaps] == [
        ("2026-09-13T00:00:00.000000000Z", "2026-09-14T00:00:00.000000000Z")
    ]


def test_a_requested_range_finer_than_a_microsecond_is_answered_exactly(gateway):
    """#1371: a bound the coverage view shows - nine digits - crosses kola as
    nanoseconds and comes back as it went; 1 ns either side of a covered
    range is the gap, rather than a refusal or an empty interval."""
    lo, hi = "2026-09-14T23:59:59.999999999Z", "2026-09-15T00:00:00.000000001Z"
    body = _coverage(gateway, "trades", "", "v1", lo, hi)
    assert body.requested is not None
    assert (body.requested.range_from, body.requested.range_to) == (lo, hi)
    assert [(g.range_from, g.range_to) for g in body.gaps] == [
        ("2026-09-15T00:00:00.000000000Z", hi)
    ]
