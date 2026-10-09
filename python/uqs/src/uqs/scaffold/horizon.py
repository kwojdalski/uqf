"""Scaffolding a horizon job: an event evaluated once its horizon has passed (#949).

`uqs job new NAME --kind horizon --subscribe-to EVENTS,REFERENCE --horizon 0D00:00:10
--columns ...` writes one file: the two inputs (each table's whole plant
schema, to narrow), the output table NAME, a scoring function that throws until
written, the two-input `.qetl.transform` it is declared as, and the
`.qetl.job.stream.at_horizons` that registers the job - see
src/etl/core/horizon.q for the kind, and src/etl/streaming/crypto_markout.q for
a finished one.

THE FILE MUST LOAD, as a normalizer's must: the transform's example is one
typed row of each input and of the output, so it loads, and the scoring
function throws - the transform suite then fails on it, which is the red a
scaffold leaves. Readiness, windows, identity and expiry (event_time,
ready_on, legs, lookback, identity, expire_after) are left for the author to
declare: which an event needs is the job's to say, not the scaffold's to guess.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from uqs.paths import STREAM_DIR, TABLES_FILE, TEST_DIR, UqsError
from uqs.scaffold.catalog import catalog_actions
from uqs.scaffold.columns import (
    TIME_COLUMN,
    is_known,
    nested_declaration,
    sample_value,
    table_definition,
)
from uqs.scaffold.docs import ANALYTICS, doc_stub_actions
from uqs.scaffold.jobs import (
    _DOCS_NOTE,
    _check_name,
    _expected_table_action,
    _nslist_action,
    start_with_all_field,
    test_namespace,
)
from uqs.scaffold.plan import FileAction, ScaffoldPlan, WriteMode
from uqs.scaffold.profile import membership, profile_names
from uqs.scaffold.templates import test_stub

#: A q timespan literal, as `--horizon` takes it: 0D00:00:10, 0D00:01:00.5.
_TIMESPAN = re.compile(r"^\d+D\d\d:\d\d:\d\d(\.\d+)?$")


def _empty(cols: list[tuple[str, str]]) -> str:
    return "([] " + "; ".join(f"{c}:{lit}" for c, lit in cols) + ")"


def _row(cols: list[tuple[str, str]]) -> str:
    return "([] " + "; ".join(f"{c}:enlist {sample_value(lit)}" for c, lit in cols) + ")"


def horizon_job(
    name: str,
    subscribe_to: list[str],
    columns: list[tuple[str, str]] | None,
    source_columns: dict[str, list[tuple[str, str]]],
    horizon: str | None,
    *,
    known_tables: set[str],
    publishes: str | None = None,
    procname: str | None = None,
    start_with_all: bool = False,
    profile: str | None = None,
    unprofiled: str | None = None,
    known_profiles: Iterable[str] | None = None,
) -> ScaffoldPlan:
    """Plan a horizon job publishing `name`: `subscribe_to` is the events
    table then the reference table; `columns` the new output table, `time`
    included as `parse_columns` reads it; `source_columns` each plant table's
    columns, read from the tree."""
    _check_name(name, "horizon job name")
    proc = procname or f"{name}1"
    _check_name(proc, "procname")
    if len(subscribe_to) != 2:
        raise UqsError(
            "--kind horizon needs --subscribe-to EVENTS,REFERENCE: the table whose rows "
            "are evaluated, then the table they are evaluated against"
        )
    if publishes:
        raise UqsError("a horizon job publishes its own NAME - drop --publishes")
    if not columns:
        raise UqsError("--kind horizon needs --columns: the table it publishes")
    if not horizon or not _TIMESPAN.match(horizon):
        raise UqsError(
            f"--kind horizon needs --horizon, a q timespan such as 0D00:00:10, not {horizon!r}"
        )
    if name in known_tables:
        raise UqsError(f"{name!r} is already a plant table - a horizon job owns its output")
    events, reference = subscribe_to
    missing = [t for t in subscribe_to if t not in source_columns]
    if missing:
        raise UqsError(f"no plant definition to read for {', '.join(missing)}")
    if "sym" not in {c for c, _ in source_columns[reference]}:
        raise UqsError(
            f"{reference} carries no sym, which a horizon job keys its reference on by "
            "default - write this one by hand and declare `by`"
        )
    unsampled = sorted(
        {lit for t in subscribe_to for _, lit in source_columns[t] if not is_known(lit)}
    )
    if unsampled:
        raise UqsError(
            f"a column type has no sample value to scaffold an example with: "
            f"{', '.join(unsampled)} - write this horizon job by hand"
        )
    output = [(c, lit.replace("`g#", "")) for c, lit in columns if c != TIME_COLUMN]
    ev = [(c, lit.replace("`g#", "")) for c, lit in source_columns[events]]
    ref = [(c, lit.replace("`g#", "")) for c, lit in source_columns[reference]]
    swa_key, swa_value = start_with_all_field(start_with_all)
    body = f"""/ {name}.q - the `{name}` horizon job: <one line: what is evaluated, and when>
/ (.qpipe.job.{name}).
/ .
/ Evaluates each `{events}` row {horizon} after it, against `{reference}`;
/ publishes `{name}`. The queue, timer, readiness and eviction are the
/ horizon kind's (src/etl/core/horizon.q): this file is the scoring.
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.

\\d .qpipe.job.{name}

/ SCAFFOLDED. What the scoring reads - each table's whole plant schema.
/ Narrow both to the columns `score` uses: a column it never touches still
/ breaks it when upstream changes that column.
events:{_empty(ev)}
reference:{_empty(ref)}

/ The output. No `time`: the plant stamps it.
{name}:{_empty(output)}

/ SCAFFOLDED. Each event evaluated against the reference, as `{name}` rows.
/ Throws until written.
/ @param events the events whose horizon has passed
/ @param reference the reference rows still held
/ @return {name} rows
score:{{[events;reference]
    '"{name}.score: not implemented";
    }}

\\d .

/ SCAFFOLDED example: one event, one reference row, and the row they become.
.qetl.transform.define[`{name};`inputs`output`fn`examples!(
    `events`reference!(.qpipe.job.{name}.events;.qpipe.job.{name}.reference);
    .qpipe.job.{name}.{name};
    .qpipe.job.{name}.score;
    enlist `inputs`expected!(
        `events`reference!({_row(ev)};{_row(ref)});
        {_row(output)}))];

/ SCAFFOLDED. Optional keys, each off until declared (src/etl/core/horizon.q):
/ event_time (e.g. `source_time), ready_on `reference with legs and lookback,
/ identity, expire_after.
.qetl.job.stream.at_horizons[`{name};`procname`events`reference`transform`publishes`horizon`period{swa_key}`note!(
    `{proc};
    `{events};
    `{reference};
    `{name};
    `{name};
    {horizon};
    0D00:00:01;{swa_value}
    "SCAFFOLDED: say why this exists, and why it does or does not start with the stack")];
"""
    notes = [f"implement .qpipe.job.{name}.score and its example"]
    actions = [
        FileAction(STREAM_DIR / f"{name}.q", body),
        FileAction(
            TABLES_FILE,
            f"\n/ {proc}'s output. <one line: what a row means>\n"
            f"{table_definition(name, columns)}\n{nested_declaration(name, columns)}",
            mode=WriteMode.APPEND,
        ),
        _expected_table_action(name),
    ]
    actions += catalog_actions(name, columns, notes)
    ns = test_namespace(name)
    actions += [
        FileAction(
            TEST_DIR / f"test_{name}.q",
            test_stub(name, ns, f"the {name} horizon job", driver=True),
        ),
        _nslist_action(ns),
    ]
    actions += doc_stub_actions(proc, ANALYTICS)
    notes.append(_DOCS_NOTE.format(proc=proc))
    member_actions, member_notes = membership(
        proc,
        profile=profile,
        unprofiled=unprofiled,
        start_with_all=start_with_all,
        known_profiles=profile_names(known_profiles),
    )
    actions += member_actions
    notes += member_notes
    notes.append(f"start it with its producers: {events} {reference}")
    return ScaffoldPlan(name=name, actions=actions, notes=notes)
