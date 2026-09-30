"""Scaffolding a bounded worker: its source, its worker and its test.

Split from scaffold/jobs.py when adding the source transport took it past the
400-line limit this package holds its modules to. The seam is the one
`uqs job new --kind` already draws: jobs.py plans a streaming job, this plans
a backfill, and both share jobs.py's naming and registration helpers.
"""

from __future__ import annotations

import re

from uqs.paths import SOURCE_DIR, TABLES_FILE, TEST_DIR, WORKER_DIR, UqsError
from uqs.scaffold.catalog import catalog_actions
from uqs.scaffold.jobs import (
    _STACK_PAGE_NOTE,
    _check_name,
    _expected_table_action,
    _nslist_action,
    parse_columns,
    test_namespace,
)
from uqs.scaffold.plan import FileAction, ScaffoldPlan, WriteMode
from uqs.scaffold.templates import (
    CREDENTIAL_SHAPES,
    TRANSPORTS,
    credential_var,
    source_body,
    table_definition,
    test_stub,
    worker_body,
)

#: A partition is a q symbol: `EURUSD, `binance_spot.
_PARTITION = re.compile(r"^[A-Za-z0-9_]+$")


def bounded_worker(
    name: str,
    dataset: str,
    columns: str | None,
    width: str = "1D",
    source: str | None = None,
    procname: str | None = None,
    *,
    reuse_source: bool = False,
    define_table: bool = True,
    transport: str = "ipc",
    partition: str | None = None,
    check: bool = False,
) -> ScaffoldPlan:
    """Plan a new bounded worker: its source, its worker and its test.

    A backfill is three declarations rather than one - the source says what
    the rows are and how to window them, the worker says which source feeds
    which dataset how wide, and the transform sits between. They are
    scaffolded together because a worker whose source does not exist aborts
    at load: `.qetl.job.bounded.define` resolves it at define time.

    `reuse_source` plans a worker on a source that already exists - a second
    window width or target over rows someone has already declared - so the
    source file is not written. `define_table` is False when the dataset is
    already a plant table, which then must not be defined a second time.
    Both are facts about the tree, so the caller, which has one, decides.

    `transport` is how a new source is reached: `ipc` (a q process, the
    default) or `odbc` (a database, through `.qetl.io.odbc`). It shapes the
    source file, so it is refused for a source that is reused.

    `partition` scopes the worker to one slice of its dataset: coverage is
    kept per (dataset, partition), so it is the only way two workers can fill
    one dataset. `check` scaffolds a quality check that fails a window on bad
    rows - most of the tree's workers declare one.
    """
    _check_name(name, "worker name")
    src = source or name
    _check_name(src, "source name")
    _check_name(dataset, "dataset")
    if partition is not None and not _PARTITION.match(partition):
        raise UqsError(
            f"--partition {partition!r} must be a q symbol - letters, digits and underscores, "
            "e.g. EURUSD"
        )
    worker = f"{name}_backfill"
    proc = procname or f"{name}_backfill1"
    # Columns shape what is WRITTEN - a new source's fields, a new table - so
    # they are required when either is, and refused when neither is, rather
    # than silently ignored.
    needs_columns = define_table or not reuse_source
    if needs_columns and not columns:
        raise UqsError(
            f"--kind backfill needs --columns: they declare the new source {src!r}'s fields "
            f"and, if {dataset!r} is not yet a plant table, its definition"
        )
    if columns and not needs_columns:
        raise UqsError(
            f"--columns has nothing to shape: source {src!r} and table {dataset!r} both "
            "exist already - drop --columns"
        )
    if transport not in TRANSPORTS:
        raise UqsError(f"--transport must be one of {', '.join(TRANSPORTS)}, not {transport!r}")
    if reuse_source and transport != "ipc":
        raise UqsError(
            f"--transport shapes a new source, and {src!r} exists already - drop --transport"
        )
    cols = parse_columns(columns) if columns else []

    actions: list[FileAction] = []
    if not reuse_source:
        actions.append(
            FileAction(SOURCE_DIR / f"{src}.q", source_body(src, dataset, cols, transport))
        )
    actions.append(
        FileAction(
            WORKER_DIR / f"{worker}.q",
            worker_body(worker, src, dataset, width, proc, partition=partition, check=check),
        )
    )
    if define_table:
        actions += [
            FileAction(
                TABLES_FILE,
                f"\n/ {proc}'s target. <one line: what a row means>\n"
                f"{table_definition(dataset, cols)}\n",
                mode=WriteMode.APPEND,
            ),
            _expected_table_action(dataset),
        ]
    actions += [
        FileAction(
            TEST_DIR / f"test_{worker}.q",
            test_stub(worker, test_namespace(name, bounded=True), f"the {worker} bounded worker"),
        ),
        _nslist_action(test_namespace(name, bounded=True)),
    ]
    if reuse_source:
        notes = [f"reuses .qpipe.source.{src}: its query and fixture are already written"]
    else:
        notes = [
            f"write .qpipe.source.{src}.query - parameterised, never concatenated"
            " (see src/etl/core/source_contract.q)",
            f"write .qpipe.source.{src}.fixture - deterministic, same contract as the live source",
            f"declared columns: {', '.join(c for c, _ in cols)}",
            f"a live run reads {credential_var(src)} ({CREDENTIAL_SHAPES[transport]}); "
            "without it the worker runs on the fixture, and warns that it is",
        ]
    notes.append("the window is half-open [from;to): >= on the lower bound, < on the upper")
    if check:
        notes.append(f"write .qpipe.job.{worker}.quality_check - every window fails until you do")
    else:
        notes.append(
            "optional: a quality check that fails a window on bad rows - rerun with --check, "
            "or see quality_check in src/etl/workers/demo_deals_backfill.q"
        )
    notes.append(_STACK_PAGE_NOTE.format(proc=proc))
    if define_table:
        actions += catalog_actions(dataset, cols, notes)
    return ScaffoldPlan(name=worker, actions=actions, notes=notes)
