"""Undoing a scaffold: `uqs job remove NAME`.

A scaffold is seven or eight edits across the tree - the job file, its test,
a table, three lists and a catalog entry - and a typo in the job name used to
mean reversing each by hand. This works the edits out from the TREE rather
than from the command that made them, so it needs nothing but the name.

WHAT IT REMOVES. The job's file and its test (nothing registers a test: the
suite loader finds it by what it holds, so deleting the file is the whole of
it), and the profile lines naming its process. For a backfill, its source too, when
nothing else in the tree names it - another worker, or a streaming feed reading it
through .qetl.source.for_source (#2833). A table the job publishes (or fills) goes with its
definition, its `expected` entry and its catalog line - but only when nothing
else in the tree mentions it, so a table another job reads is never taken, and
only when this scaffold defined it (its comment opens "/ <process>'s"), so a
table it merely publishes onto stays (#1585) - unless the job that defined it
is gone and the definition is still unwritten, when the last job on it - writer
or reader - takes it (#2842, #2924).
A reaction is only its file and its test: it has no process, so no table,
profile or port.
An external feed (#715) also loses its Python publisher, its feed module and
their test - when its feed module says a connector scaffold of that name wrote
it, so a plain job named like a shipped connector leaves its modules (#2831) -
and the raw table the publisher writes - on the same rule as any
table: only when nothing else mentions it.
A backfill also loses the worked example its scaffold generated under
scripts/examples/, and every process the Showcase card and stack.md comment
its scaffold wrote (#713) - while they still say SCAFFOLDED. A hand-written
example and written prose are left, and the reference report lists them.

WHAT IT REFUSES. A job whose file - or any file it would delete - is no
longer as the scaffold wrote it: its SCAFFOLDED marker gone, or, for a file
written complete (a feed module, a passthrough's test), its text changed
(#2832). It has been written, and deleting written work is not undoing a
scaffold. `--override even-if-edited --reason TEXT` overrides that, and nothing
else (a bare `--even-if-edited` until #1852). With or without it: a job a bundle
installed, which is the bundle's to take (#2923), and a job another job resolves
through - a twin's `twin_of`, the dataset a reaction watches that no other worker
fills (#2834), or a table only it publishes that another job subscribes to (#2925).

WHEN THE JOB FILE IS GONE (#2945) - deleted by hand, or swept by a `git clean`
- the registrations its scaffold made are still found by name: its test, the
tables whose definition opens "/ <process>'s", its profile lines and doc stubs,
and for a backfill its scaffold-written source and example, on the same rules
as above. The process is the scaffold's default, NAME1: without the file there
is no declaration to read a --procname from.

WHAT IT REPORTS (#717). Every line elsewhere in the tree that still names
what it removes - an example script, a docs page, a diagram, another suite's
test - with its line number, because it knows only the files a scaffold
writes. `uqs job remove --strict` refuses while any remain. See
scaffold/references.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from uqs.model.declarations import declaration_calls, declared_outputs, symbols
from uqs.model.kinds import DECLARATION_VERBS
from uqs.paths import (
    REACTION_DIR,
    SOURCE_DIR,
    STREAM_DIR,
    TEST_DIR,
    WORKER_DIR,
    UqsError,
)
from uqs.scaffold.docs import SHOWCASE_PAGE, STACK_PAGE, without_stubs
from uqs.scaffold.example import GENERATED_BY, example_path
from uqs.scaffold.profile import PROFILES_FILE, UNPROFILED_FILE
from uqs.scaffold.references import Reference, stale_references
from uqs.scaffold.remove_edits import drop_from_profiles, drop_table, edit, tables_defined_by
from uqs.scaffold.remove_policy import (
    BOUNDED,
    MARKER,
    connector_files,
    defined_by,
    generated_test,
    living_names,
    orphaned_inputs,
    refuse_bundled,
    refuse_dependents,
    refuse_written,
    user_of,
)

_DECLARES = DECLARATION_VERBS


@dataclass
class Removal:
    """What removing one job deletes and rewrites, before any of it happens."""

    name: str
    deletes: list[Path] = field(default_factory=list)
    rewrites: dict[Path, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    #: Lines outside these edits that still name what goes - left as they are.
    references: list[Reference] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"remove {self.name}:"]
        lines += [f"  delete {p}" for p in self.deletes]
        lines += [f"  edit {p}" for p in self.rewrites]
        lines += [f"  note: {n}" for n in self.notes]
        if self.references:
            lines.append(
                f"  still named in {len(self.references)} line(s) this does not edit "
                "- remove or rewrite them by hand:"
            )
            lines += [f"    {r.render()}" for r in self.references]
        return "\n".join(lines)

    def apply(self, repo_root: Path) -> None:
        for path, text in self.rewrites.items():
            (repo_root / path).write_text(text)
        for path in self.deletes:
            (repo_root / path).unlink()


def plan_removal(repo_root: Path, name: str, *, force: bool = False) -> Removal:
    """What removing job `name` takes out of the tree at `repo_root`.

    A file goes only when the scaffold provably wrote it - its job file names
    it, or it carries the scaffold's marker - and it is still as written;
    one that has been written since is refused without `force` (#2832). A
    job another job resolves through is refused outright (#2834).
    """
    if not any((repo_root / c).is_file() for c in _candidates(name)):
        leftovers = _leftovers_removal(repo_root, name, force)
        if leftovers is not None:
            return leftovers
    job_file = _job_file(repo_root, name)
    refuse_bundled(repo_root, job_file)
    text = (repo_root / job_file).read_text()
    if MARKER not in text and not force:
        raise UqsError(
            f"{job_file} has no {MARKER} marker left - it has been written, and uqs job remove "
            "only undoes a scaffold. Pass --override even-if-edited --reason REASON to remove it "
            "anyway"
        )
    # Files already shown to be the scaffold's, and files whose unedited text
    # is known exactly - every other delete must still carry MARKER.
    proven = {job_file}
    exact: dict[Path, str] = {}
    if job_file.parent == REACTION_DIR:
        removal = _reaction_removal(repo_root, name, job_file)
        refuse_written(removal.deletes, repo_root, force, proven, exact)
        return _with_references(removal, repo_root, {name})
    fn, job, fields = _declaration(job_file, text)
    refuse_dependents(repo_root, job_file, fn, job, fields)
    proc = (symbols(fields.get("procname", "")) or (f"{job}1",))[0]
    removal = Removal(job)
    removal.deletes.append(job_file)
    gone = {job_file} | _remove_test(removal, repo_root, job)
    # Everything this takes away, by name: the argument as given (a backfill's
    # NAME, its worker being NAME_backfill), the job and its process. The
    # source and the tables join below only if they go too.
    names = {name, job, proc}
    # What stays, by name. A feed is usually named after its table, and when
    # that table is kept, every mention of the name is a use of the table.
    kept: set[str] = set()

    if fn == BOUNDED:
        tables = list(declared_outputs(fields, job)[:1])
        # Only the example a scaffold generated; a hand-written one is the
        # reference report's to list.
        example = example_path(job.removesuffix("_backfill"))
        if (repo_root / example).is_file() and GENERATED_BY in (repo_root / example).read_text():
            removal.deletes.append(example)
            gone.add(example)
            proven.add(example)
        source = symbols(fields.get("source", ""))[:1]
        if source:
            src_file = SOURCE_DIR / f"{source[0]}.q"
            # Any q file that stays and names the source reads it - another
            # worker, or a streaming feed through .qetl.source.for_source (#2833).
            reader = user_of(repo_root, source[0], gone | {src_file})
            if reader is None and (repo_root / src_file).is_file():
                removal.deletes.append(src_file)
                gone.add(src_file)
                names.add(source[0])
            else:
                # NAME is the source's name too: something else reads it, so
                # its mentions are that reader's.
                kept.add(source[0])
                if reader is not None:
                    removal.notes.append(f"kept source {source[0]}: {reader} reads it")
    elif fn == "qetl.job.stream.normalize":
        tables = [job]
    else:
        tables = list(declared_outputs(fields, job))
        generated_test(job, fields, exact)
        # An external feed (#715): its Python publisher, the feed module the CLI
        # discovers, their test - and the raw table the publisher writes, which
        # is the job's subscription. What the feed produces goes with its module
        # (#1776): there is no list elsewhere to edit.
        py_files = connector_files(repo_root, job, fields, removal.notes, exact)
        if py_files:
            removal.deletes += py_files
            gone |= set(py_files)
            raw = list(symbols(fields.get("subscribe_to", "")))
            tables = raw + tables

    living = living_names(repo_root, job_file)
    for table in tables:
        user = user_of(repo_root, table, gone)
        if user is not None:
            removal.notes.append(f"kept table {table}: {user} uses it")
            kept.add(table)
            continue
        if not defined_by(repo_root, table, {job, proc}, living):
            # A guest writer (#1585): the job published onto a table it did not
            # define, so the definition is someone else's to remove.
            removal.notes.append(f"kept table {table}: this scaffold did not define it")
            kept.add(table)
            continue
        names.add(table)
        drop_table(removal, repo_root, table)
    # A table it only reads, when it is the last user of a scaffold whose job is gone (#2924).
    for table in orphaned_inputs(repo_root, job_file, text, tables, gone, living):
        removal.notes.append(f"removed table {table}: its job is gone, and this read it last")
        names.add(table)
        drop_table(removal, repo_root, table)

    for rel in (PROFILES_FILE, UNPROFILED_FILE):
        if (repo_root / rel).is_file():
            edit(removal, repo_root, rel, lambda t: drop_from_profiles(t, proc))
    removal.notes.append(
        f"{proc}'s port offset stays in scripts/processes/process_ports.csv, which is append-only "
        "so an offset is never handed to another process"
    )
    # The stubs a scaffold wrote go while they still say SCAFFOLDED; anything
    # else that names the process is listed by the reference report below.
    for page in (STACK_PAGE, SHOWCASE_PAGE):
        edit(removal, repo_root, page, lambda t: without_stubs(t, proc))
    refuse_written(removal.deletes, repo_root, force, proven, exact)
    return _with_references(removal, repo_root, names - kept, {job})


def _leftovers_removal(repo_root: Path, name: str, force: bool) -> Removal | None:
    """What a scaffold of `name` left registered once its job file is gone
    (#2945), or None when it left nothing. Without that file `uqs job new`
    refused its rerun at each leftover in turn, and nothing here could undo
    them. `name` may be a backfill's NAME, its worker NAME_backfill; when both
    left something, which was meant is unknown and None is returned."""
    jobs = [name] if name.endswith("_backfill") else [name, f"{name}_backfill"]
    found = [r for r in (_leftovers_of(repo_root, name, j, force) for j in jobs) if r]
    return found[0] if len(found) == 1 else None


def _leftovers_of(repo_root: Path, name: str, job: str, force: bool) -> Removal | None:
    proc = f"{job}1"
    removal = Removal(job)
    gone = _remove_test(removal, repo_root, job)
    names = {name, job, proc}
    kept: set[str] = set()
    proven: set[Path] = set()
    if job.endswith("_backfill"):
        base = job.removesuffix("_backfill")
        example = example_path(base)
        if (repo_root / example).is_file() and GENERATED_BY in (repo_root / example).read_text():
            removal.deletes.append(example)
            gone.add(example)
            proven.add(example)
        src_file = SOURCE_DIR / f"{base}.q"
        if (repo_root / src_file).is_file():
            reader = user_of(repo_root, base, gone | {src_file})
            if reader is None:
                removal.deletes.append(src_file)
                gone.add(src_file)
            else:
                kept.add(base)
                removal.notes.append(f"kept source {base}: {reader} reads it")
    for table in tables_defined_by(repo_root, {job, proc}):
        user = user_of(repo_root, table, gone)
        if user is not None:
            removal.notes.append(f"kept table {table}: {user} uses it")
            kept.add(table)
            continue
        names.add(table)
        drop_table(removal, repo_root, table)
    for rel in (PROFILES_FILE, UNPROFILED_FILE):
        if (repo_root / rel).is_file():
            edit(removal, repo_root, rel, lambda t: drop_from_profiles(t, proc))
    for page in (STACK_PAGE, SHOWCASE_PAGE):
        edit(removal, repo_root, page, lambda t: without_stubs(t, proc))
    if not removal.deletes and not removal.rewrites:
        return None
    removal.notes.insert(
        0,
        f"no job file for {job}: removing what its scaffold left registered, for process {proc}",
    )
    refuse_written(removal.deletes, repo_root, force, proven, {})
    return _with_references(removal, repo_root, names - kept, {job})


def _with_references(
    removal: Removal, repo_root: Path, names: set[str], jobs: set[str] | None = None
) -> Removal:
    """`removal`, with every line its edits leave still naming one of `names`
    or calling into one of `jobs`' namespaces."""
    removal.references = stale_references(repo_root, names, removal.deletes, removal.rewrites, jobs)
    return removal


def _reaction_removal(repo_root: Path, name: str, job_file: Path) -> Removal:
    """A reaction is its file and its test: no table, profile or port, since
    it has no process of its own."""
    removal = Removal(name)
    removal.deletes.append(job_file)
    _remove_test(removal, repo_root, name)
    return removal


def _remove_test(removal: Removal, repo_root: Path, job: str) -> set[Path]:
    """Queue `job`'s test file; return what goes.

    Only the file: the suite is found by what it holds (tests/lib/discovery.q),
    so no list names it and nothing else needs editing.
    """
    test_file = TEST_DIR / f"test_{job}.q"
    if not (repo_root / test_file).is_file():
        return set()
    removal.deletes.append(test_file)
    return {test_file}


def _candidates(name: str) -> list[Path]:
    """The job files `name` may name: exactly, then as NAME_backfill."""
    exact = [STREAM_DIR / f"{name}.q", WORKER_DIR / f"{name}.q", REACTION_DIR / f"{name}.q"]
    return [*exact, WORKER_DIR / f"{name}_backfill.q"]


def _job_file(repo_root: Path, name: str) -> Path:
    """The one job file `name` names. A job named exactly wins over the
    NAME_backfill convenience, or a stream job sharing its name with a
    backfill could never be removed by the name `uqs job list` gives it (#2840)."""
    candidates = _candidates(name)
    exact = candidates[:3]
    found = [c for c in exact if (repo_root / c).is_file()] or [
        c for c in candidates if (repo_root / c).is_file()
    ]
    if len(found) != 1:
        where = ", ".join(str(c) for c in candidates)
        raise UqsError(
            f"{len(found)} job files match {name!r} (looked for {where}) - name exactly one job"
        )
    return found[0]


def _declaration(path: Path, text: str) -> tuple[str, str, dict[str, str]]:
    calls = [c for c in declaration_calls(text) if c[0] in _DECLARES]
    if len(calls) != 1:
        raise UqsError(f"{path} makes {len(calls)} job declarations - remove it by hand")
    return calls[0]
