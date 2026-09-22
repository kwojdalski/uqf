"""Scaffolding a new ETL job: the files, and where each line of it goes.

WHAT THIS IS FOR. Adding a job was six hand-edits before the declaration
directories were globbed and `schema` became derived; it is three now, and
two of those three are the actual work. This writes the third - the registry
entry - and gives the other two a correct skeleton to start from.

WHAT IT DELIBERATELY DOES NOT DO. It does not write the job's logic. The
generated `on_batch`/`fetch` THROWS, and the generated test asserts real
behaviour and fails. A scaffold that produced something green-but-empty
would be manufacturing exactly the failure `stack_smoke.py` exists to catch:
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
from dataclasses import dataclass, field
from pathlib import Path

from torq_orchestrator.paths import UqfStackError
from torq_orchestrator.scaffold_templates import (
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

#: Where each kind of file lives. Spelled once so a directory move is one
#: edit here rather than a hunt through format strings.
STREAM_DIR = Path("src/etl/streaming")
SOURCE_DIR = Path("src/etl/sources")
WORKER_DIR = Path("src/etl/workers")
TEST_DIR = Path("tests/q")
TABLES_FILE = Path("scripts/processes/uqf_stack_tables.q")
REGISTRY_FILE = Path("python/torq_orchestrator/src/torq_orchestrator/registry.py")


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class FileAction:
    """One file this scaffold would create, or one block it would append."""

    path: Path
    body: str
    #: "create" refuses an existing file; "append" requires one.
    mode: str = "create"

    def describe(self) -> str:
        verb = "create" if self.mode == "create" else "append to"
        return f"{verb} {self.path} ({len(self.body.splitlines())} lines)"


@dataclass(frozen=True)
class ScaffoldPlan:
    """Everything a new job needs, before any of it is written."""

    name: str
    actions: list[FileAction] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"scaffold {self.name}:"]
        lines += [f"  {a.describe()}" for a in self.actions]
        lines += [f"  note: {n}" for n in self.notes]
        return "\n".join(lines)


#: q type CHARACTERS, as `meta` reports them - which is what a source's
#: `types` string is compared against at registration. Note `j` for a long,
#: not `l`: the first draft of demo_events.q wrote "l" and was refused,
#: correctly.


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
                mode="append",
            )
        )
    elif columns:
        raise UqfStackError("--columns given with no --publishes: there is no table to define")

    actions.append(
        FileAction(
            REGISTRY_FILE,
            registry_entry_streaming(name, proc, is_feed, publishes),
            mode="append",
        )
    )
    actions.append(
        FileAction(
            TEST_DIR / f"test_{name}.q",
            test_stub(name, f"{name}test", f"the {name} streaming job"),
        )
    )
    notes.append(f"implement .qsub.{name}.{handler}, then replace the scaffolded test")
    if not is_feed:
        notes.append("start it with its producers: " + " ".join(sorted(set(subscribes))))
    return ScaffoldPlan(name=name, actions=actions, notes=notes)


def bounded_worker(
    name: str,
    dataset: str,
    columns: str,
    width: str = "1D",
    source: str | None = None,
    procname: str | None = None,
) -> ScaffoldPlan:
    """Plan a new bounded worker: its source, its worker, its registry entry.

    A backfill is three declarations rather than one - the source says what
    the rows are and how to window them, the worker says which source feeds
    which dataset how wide, and the transform sits between. They are
    scaffolded together because a worker whose source does not exist aborts
    at load: `.qbw.define` resolves it at define time.
    """
    _check_name(name, "worker name")
    src = source or name
    _check_name(src, "source name")
    _check_name(dataset, "dataset")
    worker = f"{name}_backfill"
    proc = procname or f"{name}_backfill1"
    cols = parse_columns(columns)
    field_names = [c for c, _ in cols]

    actions = [
        FileAction(SOURCE_DIR / f"{src}.q", source_body(src, dataset, cols)),
        FileAction(WORKER_DIR / f"{worker}.q", worker_body(worker, src, dataset, width)),
        FileAction(
            TABLES_FILE,
            f"\n/ {proc}'s target. <one line: what a row means>\n"
            f"{table_definition(dataset, cols)}\n",
            mode="append",
        ),
        FileAction(REGISTRY_FILE, registry_entry_backfill(proc, worker), mode="append"),
        FileAction(
            TEST_DIR / f"test_{worker}.q",
            test_stub(worker, f"{name}bftest", f"the {worker} bounded worker"),
        ),
    ]
    notes = [
        f"write .qfeed.{src}.query - parameterised, never concatenated (ETL-08)",
        f"write .qfeed.{src}.fixture - deterministic, same contract as the live source",
        f"declared fields: {', '.join(field_names)}",
        "the window is half-open [from;to): >= on the lower bound, < on the upper",
    ]
    return ScaffoldPlan(name=worker, actions=actions, notes=notes)


def apply_plan(plan: ScaffoldPlan, repo_root: Path) -> list[Path]:
    """Write a plan, or refuse the whole thing.

    Every target is checked BEFORE anything is written: a scaffold that
    created three files and then refused the fourth would leave a tree that
    neither loads nor reverts cleanly, and the half of it that did land
    registers itself on load.
    """
    for action in plan.actions:
        target = repo_root / action.path
        if action.mode == "create" and target.exists():
            raise UqfStackError(
                f"{action.path} already exists - pick another name, or remove it first"
            )
        if action.mode == "append" and not target.is_file():
            raise UqfStackError(f"{action.path} does not exist, so there is nothing to append to")

    written: list[Path] = []
    for action in plan.actions:
        target = repo_root / action.path
        if action.mode == "create":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(action.body)
        else:
            target.write_text(_appended(target.read_text(), action))
        written.append(action.path)
    return written


def _appended(existing: str, action: FileAction) -> str:
    """`existing` with the action's body added where that file wants it.

    registry.py is the special case: its content belongs INSIDE the PIPELINES
    tuple, so the body goes before the closing paren rather than at the end
    of the file.
    """
    if action.path != REGISTRY_FILE:
        return existing.rstrip("\n") + "\n" + action.body
    marker = "\n)\n"
    if not existing.endswith(marker):
        raise UqfStackError(
            "registry.py does not end with the PIPELINES tuple's closing paren, so "
            "this scaffold cannot tell where an entry goes - add it by hand"
        )
    return existing[: -len(marker)] + "\n" + action.body + ")\n"
