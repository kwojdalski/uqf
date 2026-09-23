"""Scaffolding a new ETL job: the files, and where each line of it goes.

WHAT THIS IS FOR. Adding a job was six hand-edits before the declaration
directories were globbed and `schema` became derived; it is three now, and
two of those three are the actual work. This writes the third - the registry
entry - and gives the other two a correct skeleton to start from.

WHAT IT DELIBERATELY DOES NOT DO. It does not write the job's logic. The
generated `on_batch`/`fetch` THROWS, and the generated test asserts real
behaviour and fails. A scaffold that produced something green-but-empty
would be manufacturing exactly the failure `checks/stack_smoke.py` exists to catch:
a process that is `up`, heartbeating, and publishing nothing.

A PLAN, NOT A WRITE. Every entry point returns a `ScaffoldPlan` - a list of
file actions - which the caller renders (`--dry-run`) or applies. That is
what lets the templates be tested without a repository to write into, and
`test_scaffold.py` holds the generated `.qstream.register` block against the
same regex `pipeline_edges` parses real jobs with, so a template that drifts
out of what the tree can read fails the build rather than rotting quietly.
"""

from __future__ import annotations

import re

from uqf_stack.paths import (
    REGISTRY_FILE,
    RUN_TESTS_FILE,
    SOURCE_DIR,
    STACK_TABLES_TEST,
    STREAM_DIR,
    TABLES_FILE,
    TEST_DIR,
    WORKER_DIR,
    UqfStackError,
)
from uqf_stack.scaffold.plan import FileAction, ScaffoldPlan, WriteMode
from uqf_stack.scaffold.templates import (
    GROUPED,
    Q_TYPES,
    TIME_COLUMN,
    registry_entry_backfill,
    registry_entry_streaming,
    source_body,
    table_definition,
    test_stub,
    worker_body,
)

#: The layout comes from `paths`, which is the one place that knows it - see
#: its own docstring. `scaffold` previously spelled all seven here, including
#: its own package path as a string literal.
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")


def test_namespace(base: str, *, bounded: bool = False) -> str:
    """The q namespace the generated test file declares, without its leading dot.

    Computed here and nowhere else, because it is needed TWICE - by the test
    file's own `\\d` line and by the entry that registers it in
    `run_tests.q`'s `nsList` - and the two disagreeing is exactly the bug this
    registration exists to prevent: a namespace the runner does not know about
    means the file loads, its tests never run, and the suite stays green.

    A bounded worker gets `bf` so that a backfill and a streaming job of the
    same base name do not claim one namespace.
    """
    return f"{base}bftest" if bounded else f"{base}test"


def _nslist_action(namespace: str) -> FileAction:
    """Register `namespace` in run_tests.q's nsList.

    The runner globs its test FILES but keeps the namespace list by hand, so
    writing the file is not enough to make its tests run.
    """
    return FileAction(RUN_TESTS_FILE, f"`.{namespace}", mode=WriteMode.APPEND)


#: The one registry consequence no generator writes: the TorQ README is
#: authored prose, and test_generated_docs.py fails until it names the process.
_README_NOTE = "name {proc} in docs/integrations/torq/README.md - authored prose, checked by pytest"


def _expected_table_action(table: str) -> FileAction:
    """Add `table` to test_stack_tables.q's `expected` list - a deliberate
    gate, but the scaffold defines the table and names its owner in the same
    plan, which is the thought the gate asks for.
    """
    return FileAction(STACK_TABLES_TEST, f"`{table}", mode=WriteMode.APPEND)


def _check_name(name: str, what: str) -> str:
    if not _IDENTIFIER.match(name):
        raise UqfStackError(
            f"{what} {name!r} must be lower-case, start with a letter, and hold only "
            "letters, digits and underscores - it becomes a q namespace and a filename"
        )
    return name


def parse_columns(spec: str) -> list[tuple[str, str]]:
    """A `--columns` string as [(name, q empty-column literal)].

    `time` is prepended when absent rather than rejected: every plant table
    has one, `.u.upd` stamps it, and a table scaffolded without it would be
    refused later by a publish path that assumes it.
    """
    out: list[tuple[str, str]] = []
    for part in (p.strip() for p in spec.split(",") if p.strip()):
        if ":" not in part:
            raise UqfStackError(
                f"column {part!r} must be name:type, e.g. 'value:float'. "
                f"Types: {', '.join(sorted(Q_TYPES))}"
            )
        col, _, qtype = (s.strip() for s in part.partition(":"))
        _check_name(col, "column")
        if qtype not in Q_TYPES:
            raise UqfStackError(
                f"column {col!r} has unknown type {qtype!r}. Types: {', '.join(sorted(Q_TYPES))}"
            )
        literal = Q_TYPES[qtype]
        if col in GROUPED and qtype == "symbol":
            literal = "`g#`symbol$()"
        out.append((col, literal))
    if not out:
        raise UqfStackError("--columns is empty: a published table needs at least one column")
    if not any(c == TIME_COLUMN for c, _ in out):
        out.insert(0, (TIME_COLUMN, Q_TYPES["timestamp"]))
    return out


def streaming_job(
    name: str,
    subscribes: list[str],
    publishes: str | None,
    columns: str | None,
    procname: str | None = None,
) -> ScaffoldPlan:
    """Plan a new streaming job: the q file, its table, its registry entry.

    `kind` is derived rather than asked for - a job that subscribes to
    nothing is a feed, and one that subscribes is an etl. There is no third
    answer the caller could give that the edges do not already imply.
    """
    _check_name(name, "job name")
    proc = procname or f"{name}1"
    _check_name(proc, "procname")
    is_feed = not subscribes
    actions: list[FileAction] = []
    notes: list[str] = []

    sub_literal = "`symbol$()" if is_feed else "`" + "`".join(subscribes)
    pub_literal = f"enlist `{publishes}" if publishes else "`symbol$()"
    handler = "on_timer" if is_feed else "on_batch"
    handler_args = "[]" if is_feed else "[t;data]"
    timer = "\n    0D00:00:01;" if is_feed else ""
    timer_key = "`timer_period" if is_feed else ""

    reads = "nothing" if is_feed else ", ".join(f"`{t}`" for t in subscribes)
    writes = f"`{publishes}`" if publishes else "nothing - it keeps its output local"

    body = f"""/ {name}.q - <one line: what this job is for> (.qsub.{name}).
/ .
/ Reads {reads}; publishes {writes}.
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.
/ `time` is not published - .u.upd stamps its own (invariant 1).

\\d .qsub.{name}

/ Where rows go. A stub until .qstream.wire points it at the tickerplant
/ (the runner) or at a recorder (a test). Never call .u.upd from here.
publish:.qstream.unwired `{name};

/ SCAFFOLDED. This throws until it is written - a job that silently did
/ nothing would report `up`, heartbeat, and publish no rows, which is the
/ one failure the stack smoke check exists to find.
{handler}:{{{handler_args}
    '"{name}.{handler}: not implemented";
    }}

\\d .

.qstream.register[`{name};`procname`subscribes`publishes{timer_key}`{handler}!(
    `{proc};
    {sub_literal};
    {pub_literal};{timer}
    .qsub.{name}.{handler})];
"""
    actions.append(FileAction(STREAM_DIR / f"{name}.q", body))

    if publishes:
        if not columns:
            raise UqfStackError(
                f"--publishes {publishes} needs --columns: the plant must define a table "
                "before anything writes to it, or .u.upd discards the rows in silence"
            )
        cols = parse_columns(columns)
        definition = table_definition(publishes, cols)
        actions.append(
            FileAction(
                TABLES_FILE,
                f"\n/ {proc}'s output. <one line: what a row means>\n{definition}\n",
                mode=WriteMode.APPEND,
            )
        )
        actions.append(_expected_table_action(publishes))
    elif columns:
        raise UqfStackError("--columns given with no --publishes: there is no table to define")

    actions.append(
        FileAction(
            REGISTRY_FILE,
            registry_entry_streaming(name, proc, is_feed, publishes),
            mode=WriteMode.APPEND,
        )
    )
    ns = test_namespace(name)
    actions.append(
        FileAction(
            TEST_DIR / f"test_{name}.q",
            test_stub(name, ns, f"the {name} streaming job"),
        )
    )
    actions.append(_nslist_action(ns))
    notes.append(f"implement .qsub.{name}.{handler}, then replace the scaffolded test")
    notes.append(_README_NOTE.format(proc=proc))
    if not is_feed:
        notes.append("start it with its producers: " + " ".join(sorted(set(subscribes))))
    return ScaffoldPlan(name=name, actions=actions, notes=notes)


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
) -> ScaffoldPlan:
    """Plan a new bounded worker: its source, its worker, its registry entry.

    A backfill is three declarations rather than one - the source says what
    the rows are and how to window them, the worker says which source feeds
    which dataset how wide, and the transform sits between. They are
    scaffolded together because a worker whose source does not exist aborts
    at load: `.qbw.define` resolves it at define time.

    `reuse_source` plans a worker on a source that already exists - a second
    window width or target over rows someone has already declared - so the
    source file is not written. `define_table` is False when the dataset is
    already a plant table, which then must not be defined a second time.
    Both are facts about the tree, so the caller, which has one, decides.
    """
    _check_name(name, "worker name")
    src = source or name
    _check_name(src, "source name")
    _check_name(dataset, "dataset")
    worker = f"{name}_backfill"
    proc = procname or f"{name}_backfill1"
    # Columns shape what is WRITTEN - a new source's fields, a new table - so
    # they are required when either is, and refused when neither is, rather
    # than silently ignored.
    needs_columns = define_table or not reuse_source
    if needs_columns and not columns:
        raise UqfStackError(
            f"--kind backfill needs --columns: they declare the new source {src!r}'s fields "
            f"and, if {dataset!r} is not yet a plant table, its definition"
        )
    if columns and not needs_columns:
        raise UqfStackError(
            f"--columns has nothing to shape: source {src!r} and table {dataset!r} both "
            "exist already - drop --columns"
        )
    cols = parse_columns(columns) if columns else []

    actions: list[FileAction] = []
    if not reuse_source:
        actions.append(FileAction(SOURCE_DIR / f"{src}.q", source_body(src, dataset, cols)))
    actions.append(FileAction(WORKER_DIR / f"{worker}.q", worker_body(worker, src, dataset, width)))
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
        FileAction(REGISTRY_FILE, registry_entry_backfill(proc, worker), mode=WriteMode.APPEND),
        FileAction(
            TEST_DIR / f"test_{worker}.q",
            test_stub(worker, test_namespace(name, bounded=True), f"the {worker} bounded worker"),
        ),
        _nslist_action(test_namespace(name, bounded=True)),
    ]
    if reuse_source:
        notes = [f"reuses .qfeed.{src}: its query and fixture are already written"]
    else:
        notes = [
            f"write .qfeed.{src}.query - parameterised, never concatenated (ETL-08)",
            f"write .qfeed.{src}.fixture - deterministic, same contract as the live source",
            f"declared fields: {', '.join(c for c, _ in cols)}",
        ]
    notes.append("the window is half-open [from;to): >= on the lower bound, < on the upper")
    notes.append(_README_NOTE.format(proc=proc))
    return ScaffoldPlan(name=worker, actions=actions, notes=notes)
