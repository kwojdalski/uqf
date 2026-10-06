"""/coverage end to end over real kola: the rows q returns go back to q.

The API tests stage .qetl.coverage's answers through FakeGateway. What they
cannot show is the round trip this route now depends on: the COVERAGE rows
arrive as a DataFrame, are sent BACK to the gateway as an argument, and
.qetl.coverage.compose and gaps read them there as a table. Here the q side
is real - src/etl/core/intervals.q, the file gateway1 loads - and two tiers
each hold half of a range, which is why the merge has to happen where both
halves meet.
"""

from __future__ import annotations

from typing import Any

import pytest

from uqf_frontend.app import _coverage
from uqf_frontend.config import Settings
from uqf_frontend.gateway import KolaGateway
from uqs.interpreter import PEACHQ, q_impl

SERVER_SCRIPT = """
\\l src/etl/core/intervals.q
/ Each tier's etl_coverage: the RDB holds the 14th, the HDB the 13th.
row:{[f;t] ([] dataset:enlist `trades; partition:enlist `; source_version:enlist `v1;
    range_from:enlist f; range_to:enlist t;
    recorded_at:enlist 2026.09.01D00:00; superseded_at:enlist 0Wp)};
tier_rows:`rdb`hdb!(row[2026.09.14D00:00;2026.09.15D00:00]; row[2026.09.13D00:00;2026.09.14D00:00]);
/ Stands in for TorQ's gateway: run the query on each tier, raze the results.
on_tier:{[query;tier] `etl_coverage set tier_rows tier; value query};
.gw.syncexec:{[query;tiers] raze on_tier[query] each tiers};
"""


@pytest.fixture(scope="module")
def gateway(start_q, tmp_path_factory) -> Any:
    if q_impl() == PEACHQ:
        pytest.skip("PeachQ cannot load a nested `\\d` such as intervals.q's .qetl.coverage")
    script = tmp_path_factory.mktemp("cov") / "gateway.q"
    script.write_text(SERVER_SCRIPT)
    with start_q(str(script)) as port:
        yield KolaGateway(Settings(port=port))


def test_two_tiers_halves_compose_into_one_interval(gateway):
    body = _coverage(gateway, "trades", "", "v1", None, None)
    assert [(c.range_from, c.range_to) for c in body.covered] == [
        ("2026-09-13T00:00:00+00:00", "2026-09-15T00:00:00+00:00")
    ]


def test_the_gap_is_what_neither_tier_covers(gateway):
    body = _coverage(gateway, "trades", "", "v1", "2026-09-12T00:00:00Z", "2026-09-16T00:00:00Z")
    assert [(g.range_from, g.range_to) for g in body.gaps] == [
        ("2026-09-12T00:00:00+00:00", "2026-09-13T00:00:00+00:00"),
        ("2026-09-15T00:00:00+00:00", "2026-09-16T00:00:00+00:00"),
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
        ("2026-09-13T00:00:00+00:00", "2026-09-14T00:00:00+00:00")
    ]
