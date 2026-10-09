"""`uqs job new --kind horizon` (#949): what it refuses, and what it writes.
That the written file LOADS is test_scaffold_loads.py's, which runs q."""

from __future__ import annotations

import pytest

from uqs.paths import UqsError
from uqs.scaffold.columns import parse_columns
from uqs.scaffold.horizon import horizon_job

TICKS = [("time", "`timestamp$()"), ("sym", "`g#`symbol$()"), ("px", "`float$()")]
NOSYM = [("time", "`timestamp$()"), ("venue", "`symbol$()")]
TABLES = {"ev": TICKS, "ref": TICKS, "nosym": NOSYM}


def plan(subs=("ev", "ref"), horizon="0D00:00:10", **kw):
    return horizon_job(
        "hz",
        list(subs),
        kw.pop("columns", parse_columns("sym:symbol, px:float")),
        TABLES,
        horizon,
        known_tables=set(TABLES),
        **kw,
    )


def body() -> str:
    return next(a.body for a in plan().actions if str(a.path).endswith("hz.q"))


def test_it_declares_a_horizon_job_over_the_two_tables():
    text = body()
    assert ".qetl.job.stream.at_horizons[`hz;" in text
    assert "`ev;\n    `ref;" in text and "0D00:00:10;" in text
    assert "`events`reference!(.qpipe.job.hz.events;.qpipe.job.hz.reference)" in text
    assert "not implemented" in text, "the scoring throws until it is written"


@pytest.mark.parametrize(
    ("kw", "message"),
    [
        ({"subs": ("ev",)}, "--subscribe-to EVENTS,REFERENCE"),
        ({"horizon": None}, "needs --horizon"),
        ({"horizon": "10s"}, "a q timespan"),
        ({"columns": None}, "needs --columns"),
        ({"publishes": "x"}, "drop --publishes"),
        ({"subs": ("ev", "nosym")}, "carries no sym"),
        ({"subs": ("ev", "gone")}, "no plant definition to read for gone"),
    ],
)
def test_a_bad_request_is_refused_by_name(kw, message):
    with pytest.raises(UqsError, match=message):
        plan(**kw)
