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
        ({"columns": None}, "needs --schema"),
        ({"publishes": "x"}, "drop --publishes"),
        ({"subs": ("ev", "nosym")}, "carries no sym"),
        ({"subs": ("ev", "gone")}, "no plant definition to read for gone"),
    ],
)
def test_a_bad_request_is_refused_by_name(kw, message):
    with pytest.raises(UqsError, match=message):
        plan(**kw)


# #1584: horizon.q refuses a horizon that is not a positive timespan at load,
# so the scaffold must refuse it before writing. q truncates a fraction past
# nanoseconds, so 0D00:00:00.0000000001 is zero too, not "a little positive".
@pytest.mark.parametrize(
    "horizon",
    ["0D00:00:00", "0D00:00:00.000", "00D00:00:00", "0D00:00:00.0000000001"],
)
def test_a_horizon_that_q_reads_as_zero_is_refused(horizon):
    with pytest.raises(UqsError, match="must be positive"):
        plan(horizon=horizon)


@pytest.mark.parametrize("horizon", ["0D00:00:00.000000001", "0D00:00:10", "1D00:00:00"])
def test_a_positive_horizon_is_written(horizon):
    assert any(horizon in a.body for a in plan(horizon=horizon).actions)


# #1573: a horizon job's namespace holds `events`, `reference`, `score` and
# its output NAME; a NAME equal to one of the three overwrites it.
@pytest.mark.parametrize("name", ["events", "reference", "score"])
def test_a_name_that_collides_with_a_generated_one_is_refused(name):
    with pytest.raises(UqsError, match="collides"):
        horizon_job(
            name,
            ["ev", "ref"],
            parse_columns("sym:symbol, px:float"),
            TABLES,
            "0D00:00:10",
            known_tables=set(TABLES),
        )


# #1596: as for a normalizer - an output of time alone is empty once the
# plant's time is dropped, and the registry refuses it at load.
def test_an_output_of_time_alone_is_refused():
    with pytest.raises(UqsError, match="besides time"):
        plan(columns=parse_columns("time:timestamp"))


def test_one_payload_column_beside_time_is_enough():
    assert plan(columns=parse_columns("time:timestamp, px:float")).actions
