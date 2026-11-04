"""Every scaffold creating a plant table must respect the runtime's names."""

from __future__ import annotations

import pytest

from uqs.paths import UqsError
from uqs.scaffold.columns import parse_columns
from uqs.scaffold.external import external_feed
from uqs.scaffold.horizon import horizon_job
from uqs.scaffold.jobs import streaming_job
from uqs.scaffold.normalizer import normalizer
from uqs.scaffold.worker import bounded_worker

KINDS = ("streaming", "normalizer", "horizon", "backfill", "connector")


def plan(kind: str, table: str):
    columns = parse_columns("sym:symbol, px:float")
    inputs = {"audit_events": columns, "audit_refs": columns}
    if kind == "streaming":
        return streaming_job("audit_job", [], table, columns)
    if kind == "normalizer":
        return normalizer(table, ["audit_events"], columns, inputs, known_tables=set(inputs))
    if kind == "horizon":
        return horizon_job(
            table, list(inputs), columns, inputs, "0D00:00:10", known_tables=set(inputs)
        )
    if kind == "connector":
        return external_feed("audit_job", table, "executions", columns, known_tables={"executions"})
    return bounded_worker("audit_job", table, columns)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "table",
    ["upd", "generateschemas", "loghandles", "tablelist", "endofday", "date", "month", "count"],
)
def test_new_tables_cannot_overwrite_torq_globals_domains_or_keywords(kind, table):
    with pytest.raises(UqsError, match="cannot be a plant table"):
        plan(kind, table)


@pytest.mark.parametrize("kind", KINDS)
def test_ordinary_table_names_are_still_scaffolded(kind):
    assert plan(kind, "audit_output").actions
