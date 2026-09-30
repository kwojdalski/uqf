"""Scaffolding a new ETL job: the files, and where each line of it goes.

WHAT THIS IS FOR. Adding a job was six hand-edits before the declaration
directories were globbed and `schema` became derived, and the process
registry is now read from the job's own q declaration - so what is left is
the job file and its test, which are the actual work. This gives both a
correct skeleton, and makes the few appends the tree cannot derive: the
table, and the lists that gate a new table and a new test namespace.

WHAT IT DELIBERATELY DOES NOT DO. It does not write the job's logic. The
generated `on_batch`/`fetch` THROWS, and the generated test asserts real
behaviour and fails. A scaffold that produced something green-but-empty
would be manufacturing exactly the failure `checks/stack_smoke.py` exists to catch:
a process that is `up`, heartbeating, and publishing nothing.

A PLAN, NOT A WRITE. Every entry point returns a `ScaffoldPlan` - a list of
file actions - which the caller renders (`--dry-run`) or applies. That is
what lets the templates be tested without a repository to write into, and
`test_scaffold.py` holds the generated `.qetl.job.stream.define` block against the
same regex `pipeline_edges` parses real jobs with, so a template that drifts
out of what the tree can read fails the build rather than rotting quietly.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from uqs.paths import (
    RUN_TESTS_FILE,
    STACK_TABLES_TEST,
    STREAM_DIR,
    TABLES_FILE,
    TEST_DIR,
    UqsError,
)
from uqs.scaffold.catalog import catalog_actions
from uqs.scaffold.plan import FileAction, ScaffoldPlan, WriteMode
from uqs.scaffold.profile import membership, profile_names
from uqs.scaffold.templates import (
    GROUPED,
    Q_TYPES,
    TIME_COLUMN,
    table_definition,
    test_stub,
)

#: The layout comes from `paths`, which is the one place that knows it - see
#: its own docstring. `scaffold` previously spelled all seven here, including
#: its own package path as a string literal.
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")


def test_namespace(base: str, *, bounded: bool = False, reaction: bool = False) -> str:
    """The q namespace the generated test file declares, without its leading dot.

    Computed here and nowhere else, because it is needed TWICE - by the test
    file's own `\\d` line and by the entry that registers it in
    `run_tests.q`'s `nsList` - and the two disagreeing is exactly the bug this
    registration exists to prevent: a namespace the runner does not know about
    means the file loads, its tests never run, and the suite stays green.

    A bounded worker gets `bf` and a reaction `rx`, so that jobs of different
    kinds with the same base name do not claim one namespace.
    """
    if reaction:
        return f"{base}rxtest"
    return f"{base}bftest" if bounded else f"{base}test"


def _nslist_action(namespace: str) -> FileAction:
    """Register `namespace` in run_tests.q's nsList.

    The runner globs its test FILES but keeps the namespace list by hand, so
    writing the file is not enough to make its tests run.
    """
    return FileAction(RUN_TESTS_FILE, f"`.{namespace}", mode=WriteMode.APPEND)


#: The registry consequences no generator writes, because both are authored
#: prose for a person to read. Each has a test that fails until it is written,
#: and a note here is what stops that failure being a surprise.
_STACK_PAGE_NOTE = "name {proc} in docs/architecture/stack.md - authored prose, checked by pytest"


def _expected_table_action(table: str) -> FileAction:
    """Add `table` to test_stack_tables.q's `expected` list - a deliberate
    gate, but the scaffold defines the table and names its owner in the same
    plan, which is the thought the gate asks for.
    """
    return FileAction(STACK_TABLES_TEST, f"`{table}", mode=WriteMode.APPEND)


def _check_name(name: str, what: str) -> str:
    if not _IDENTIFIER.match(name):
        raise UqsError(
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
            raise UqsError(
                f"column {part!r} must be name:type, e.g. 'value:float'. "
                f"Types: {', '.join(sorted(Q_TYPES))}"
            )
        col, _, qtype = (s.strip() for s in part.partition(":"))
        _check_name(col, "column")
        if qtype not in Q_TYPES:
            raise UqsError(
                f"column {col!r} has unknown type {qtype!r}. Types: {', '.join(sorted(Q_TYPES))}"
            )
        literal = Q_TYPES[qtype]
        if col in GROUPED and qtype == "symbol":
            literal = "`g#`symbol$()"
        out.append((col, literal))
    if not out:
        raise UqsError("--columns is empty: a published table needs at least one column")
    if not any(c == TIME_COLUMN for c, _ in out):
        out.insert(0, (TIME_COLUMN, Q_TYPES["timestamp"]))
    return out


def _symbol_list(names: list[str]) -> str:
    """A q symbol-list literal: `enlist` for one, since a bare `` `a `` is an atom."""
    return f"enlist `{names[0]}" if len(names) == 1 else "`" + "`".join(names)


#: A q timespan as a declaration writes it: `0D00:00:05`, `0D00:00:00.500`.
_PERIOD = re.compile(r"^\d+D\d{2}:\d{2}(:\d{2}(\.\d{1,9})?)?$")


def check_period(period: str) -> str:
    """Refuse a period `.qetl.job.stream.define` would refuse at load.

    define wants a positive timespan; a malformed one is a q parse error in
    the generated file, and a zero one a refusal - both found only when the
    tree next loads, far from the typo.
    """
    if not _PERIOD.match(period):
        raise UqsError(
            f"--period {period!r} must be a q timespan, e.g. 0D00:00:05 or 0D00:00:00.500"
        )
    if not any(c in "123456789" for c in period):
        raise UqsError(f"--period {period!r} must be positive - a timer that never waits spins")
    return period


def start_with_all_field(start_with_all: bool) -> tuple[str, str]:
    """The declaration key and value line for `start_with_all`, or nothing.

    Written only when it is on: absent means on demand, and a scaffold that
    wrote `0b` would be a line saying what the default already says.
    """
    return ("`start_with_all", "\n    1b;") if start_with_all else ("", "")


def streaming_job(
    name: str,
    subscribe_to: list[str],
    publishes: str | None,
    columns: str | None,
    procname: str | None = None,
    *,
    known_tables: set[str] | None = None,
    start_with_all: bool = False,
    period: str | None = None,
    profile: str | None = None,
    unprofiled: str | None = None,
    known_profiles: Iterable[str] | None = None,
) -> ScaffoldPlan:
    """Plan a new streaming job: the q file, its table and its test.

    `kind` is derived rather than asked for - a job that subscribes to
    nothing is a feed, and one that subscribes is an etl. There is no third
    answer the caller could give that the edges do not already imply.

    `publishes` is one table or a comma-separated list. `known_tables` is
    every table the plant defines - a fact about the tree, so the caller,
    which has one, supplies it, as it supplies `define_table` to
    `bounded_worker`. With it, a subscription to a table nothing defines is
    refused here rather than found idle after `uqs start`, and a published
    table that already exists is published onto rather than defined twice.
    Without it every published table is new.

    `start_with_all` declares the job part of `uqs start all`. Off by default,
    as in `.qetl.job.stream.define`: a new process joins the default start
    only once someone has decided the connection budget has room for it.

    `period` is how often the job's timer fires. A feed always has one - it
    is what makes the feed publish - and ticks every second unless told
    otherwise. An etl has one only when asked, as markout and fx_positions
    do: an `on_timer` beside its `on_batch`, for work due on the clock rather
    than on a batch.

    `profile`, `unprofiled` and `known_profiles` place the process in a start
    profile or exempt it - see scaffold/profile.py.
    """
    _check_name(name, "job name")
    if period is not None:
        check_period(period)
    proc = procname or f"{name}1"
    _check_name(proc, "procname")
    for table in subscribe_to:
        _check_name(table, "subscribed table")
    pubs = [p.strip() for p in (publishes or "").split(",") if p.strip()]
    for table in pubs:
        _check_name(table, "published table")
    if known_tables is not None:
        unknown = [t for t in subscribe_to if t not in known_tables]
        if unknown:
            raise UqsError(
                f"--subscribe-to names {', '.join(unknown)}, which no plant table defines - a job "
                "subscribed to it would start, heartbeat and never receive a row. Scaffold the "
                "job that publishes it first, or check the spelling"
            )
    new_tables = [t for t in pubs if known_tables is None or t not in known_tables]
    is_feed = not subscribe_to
    actions: list[FileAction] = []
    notes: list[str] = []

    # `enlist` for one table, as for `publishes` below and as the tree's own
    # jobs write it: a bare `trades is an atom, which define accepts but which
    # reads as a different shape from every other declaration.
    sub_literal = "`symbol$()" if is_feed else _symbol_list(subscribe_to)
    pub_literal = _symbol_list(pubs) if pubs else "`symbol$()"
    # (handler, its arguments) in declaration order: a batch handler when the
    # job subscribes, then a timer when it is a feed or was given a period.
    handlers = [] if is_feed else [("on_batch", "[t;x]")]
    tick = period or ("0D00:00:01" if is_feed else None)
    if tick:
        handlers.append(("on_timer", "[]"))
    keys = "".join(f"`{h}" if h != "on_timer" else "`period`on_timer" for h, _ in handlers)
    values = "".join(
        f"\n    .qpipe.job.{name}.{h};"
        if h != "on_timer"
        else f"\n    {tick};\n    .qpipe.job.{name}.{h};"
        for h, _ in handlers
    )
    stubs = "\n\n".join(
        f"""{h}:{{{args}
    '"{name}.{h}: not implemented";
    }}"""
        for h, args in handlers
    )
    swa_key, swa_value = start_with_all_field(start_with_all)

    reads = "nothing" if is_feed else ", ".join(f"`{t}`" for t in subscribe_to)
    writes = ", ".join(f"`{t}`" for t in pubs) if pubs else "nothing - it keeps its output local"

    body = f"""/ {name}.q - <one line: what this job is for> (.qpipe.job.{name}).
/ .
/ Reads {reads}; publishes {writes}.
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.
/ `time` is not published - .u.upd stamps its own (invariant 1).

\\d .qpipe.job.{name}

/ Where rows go. A stub until .qetl.job.stream.wire points it at the tickerplant
/ (the runner) or at a recorder (a test). Never call .u.upd from here.
publish:.qetl.job.stream.unwired `{name};

/ SCAFFOLDED. This throws until it is written - a job that silently did
/ nothing would report `up`, heartbeat, and publish no rows, which is the
/ one failure the stack smoke check exists to find.
{stubs}

\\d .

/ The process registry is read from this declaration: `procname` is the
/ process that runs it, and `start_with_all` whether `uqs start all` starts it
/ (absent: on demand, until the connection budget has room).
.qetl.job.stream.define[`{name};`procname`subscribe_to`publishes{keys}{swa_key}`note!(
    `{proc};
    {sub_literal};
    {pub_literal};{values}{swa_value}
    "SCAFFOLDED: say why this exists, and why it does or does not start with the stack")];
"""
    actions.append(FileAction(STREAM_DIR / f"{name}.q", body))

    # --columns shapes ONE new table. A table that already exists is published
    # onto as it is; two new ones would need two column specs this option
    # cannot carry, so that is refused rather than guessed at.
    if len(new_tables) > 1:
        raise UqsError(
            f"--publishes names {len(new_tables)} tables the plant does not define yet "
            f"({', '.join(new_tables)}), and --columns can shape only one - scaffold with one "
            "new table and add the others to scripts/processes/uqs_tables.q by hand"
        )
    if new_tables:
        if not columns:
            raise UqsError(
                f"--publishes {new_tables[0]} needs --columns: the plant must define a table "
                "before anything writes to it, or .u.upd discards the rows in silence"
            )
        cols = parse_columns(columns)
        definition = table_definition(new_tables[0], cols)
        actions.append(
            FileAction(
                TABLES_FILE,
                f"\n/ {proc}'s output. <one line: what a row means>\n{definition}\n",
                mode=WriteMode.APPEND,
            )
        )
        actions.append(_expected_table_action(new_tables[0]))
        actions += catalog_actions(new_tables[0], cols, notes)
    elif columns:
        raise UqsError(
            "--columns has nothing to shape: "
            + (
                f"{', '.join(pubs)} already defined by the plant - drop --columns"
                if pubs
                else "no --publishes, so there is no table to define"
            )
        )

    ns = test_namespace(name)
    actions.append(
        FileAction(
            TEST_DIR / f"test_{name}.q",
            test_stub(name, ns, f"the {name} streaming job", driver=bool(subscribe_to and pubs)),
        )
    )
    actions.append(_nslist_action(ns))
    notes.append(
        f"implement {', '.join(f'.qpipe.job.{name}.{h}' for h, _ in handlers)}, "
        "then replace the scaffolded test"
    )
    notes.append(_STACK_PAGE_NOTE.format(proc=proc))
    member_actions, member_notes = membership(
        proc,
        profile=profile,
        unprofiled=unprofiled,
        start_with_all=start_with_all,
        known_profiles=profile_names(known_profiles),
    )
    actions += member_actions
    notes += member_notes
    if not is_feed:
        notes.append("start it with its producers: " + " ".join(sorted(set(subscribe_to))))
    return ScaffoldPlan(name=name, actions=actions, notes=notes)
