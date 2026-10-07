"""Undoing a scaffold: `uqs job remove NAME`.

A scaffold is seven or eight edits across the tree - the job file, its test,
a table, three lists and a catalog entry - and a typo in the job name used to
mean reversing each by hand. This works the edits out from the TREE rather
than from the command that made them, so it needs nothing but the name.

WHAT IT REMOVES. The job's file and its test, the test's nsList entry, and
the profile lines naming its process. For a backfill, its source too, when no
other worker reads it. A table the job publishes (or fills) goes with its
definition, its `expected` entry and its catalog line - but only when nothing
else in the tree mentions it, so a table another job reads is never taken.
A reaction is only its file, its test and the test's nsList entry: it has no
process, so no table, profile or port.
An external feed (#715) also loses its Python publisher, its feed module and
their test, and the raw table the publisher writes - on the same rule as any
table: only when nothing else mentions it.
A backfill also loses the worked example its scaffold generated under
scripts/examples/, and every process the Showcase card and stack.md comment
its scaffold wrote (#713) - while they still say SCAFFOLDED. A hand-written
example and written prose are left, and the reference report lists them.

WHAT IT REFUSES. A job whose file carries no SCAFFOLDED marker any more: it
has been written, and deleting written work is not undoing a scaffold.
`--force` overrides that, and nothing else.

WHAT IT REPORTS (#717). Every line elsewhere in the tree that still names
what it removes - an example script, a docs page, a diagram, another suite's
test - with its line number, because it knows only the files a scaffold
writes. `uqs job remove --strict` refuses while any remain. See
scaffold/references.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from uqs.model.declarations import declaration_calls, symbols
from uqs.paths import (
    CATALOG_FILE,
    REACTION_DIR,
    RUN_TESTS_FILE,
    SOURCE_DIR,
    STACK_TABLES_TEST,
    STREAM_DIR,
    TABLES_FILE,
    TEST_DIR,
    WORKER_DIR,
    UqsError,
)
from uqs.scaffold.docs import SHOWCASE_PAGE, STACK_PAGE, without_stubs
from uqs.scaffold.example import GENERATED_BY, example_path
from uqs.scaffold.external import external_files
from uqs.scaffold.profile import PROFILES_FILE
from uqs.scaffold.references import Reference, stale_references

MARKER = "SCAFFOLDED"
_DECLARES = ("qetl.job.stream.define", "qetl.job.stream.normalize", "qetl.job.bounded.define")
_NAMESPACE = re.compile(r"^\\d\s+\.(\w+)\s*$", re.MULTILINE)
#: Files whose mention of a table is bookkeeping, not a use: the scaffold's own
#: appends, and the generated job graph, which is regenerated after a removal.
_BOOKKEEPING = (
    TABLES_FILE,
    CATALOG_FILE,
    STACK_TABLES_TEST,
    Path("src/etl/generated/pipeline_dag.q"),
)


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
    """What removing job `name` takes out of the tree at `repo_root`."""
    job_file = _job_file(repo_root, name)
    text = (repo_root / job_file).read_text()
    if MARKER not in text and not force:
        raise UqsError(
            f"{job_file} has no {MARKER} marker left - it has been written, and uqs job remove "
            "only undoes a scaffold. Pass --force to remove it anyway"
        )
    if job_file.parent == REACTION_DIR:
        removal = _reaction_removal(repo_root, name, job_file)
        return _with_references(removal, repo_root, {name})
    fn, job, fields = _declaration(job_file, text)
    proc = (symbols(fields.get("procname", "")) or (f"{job}1",))[0]
    removal = Removal(job)
    removal.deletes.append(job_file)
    gone = {job_file}
    # Everything this takes away, by name: the argument as given (a backfill's
    # NAME, its worker being NAME_backfill), the job and its process. The
    # source and the tables join below only if they go too.
    names = {name, job, proc}
    # What stays, by name. A feed is usually named after its table, and when
    # that table is kept, every mention of the name is a use of the table.
    kept: set[str] = set()

    if fn == "qetl.job.bounded.define":
        tables = list(symbols(fields.get("dataset", ""))[:1])
        source = symbols(fields.get("source", ""))[:1]
        if source:
            src_file = SOURCE_DIR / f"{source[0]}.q"
            if _source_is_ours(repo_root, source[0], src_file, job_file, force):
                removal.deletes.append(src_file)
                gone.add(src_file)
                names.add(source[0])
            else:
                # NAME is the source's name too: another worker reads it, so
                # its mentions are that worker's.
                kept.add(source[0])
        # Only the example a scaffold generated; a hand-written one is the
        # reference report's to list.
        example = example_path(job.removesuffix("_backfill"))
        if (repo_root / example).is_file() and GENERATED_BY in (repo_root / example).read_text():
            removal.deletes.append(example)
            gone.add(example)
    elif fn == "qetl.job.stream.normalize":
        tables = [job]
    else:
        tables = list(symbols(fields.get("publishes", "")))
        # An external feed (#715): its Python publisher, the feed module the CLI
        # discovers, their test - and the raw table the publisher writes, which
        # is the job's subscription.
        py_files = [Path(f) for f in external_files(job) if (repo_root / f).is_file()]
        if py_files:
            removal.deletes += py_files
            gone |= set(py_files)
            tables = list(symbols(fields.get("subscribe_to", ""))) + tables

    gone |= _remove_test(removal, repo_root, job)

    for table in tables:
        if _mentioned_elsewhere(repo_root, table, gone):
            removal.notes.append(f"kept table {table}: something else in the tree uses it")
            kept.add(table)
            continue
        names.add(table)
        _edit(removal, repo_root, TABLES_FILE, lambda t, tb=table: _drop_definition(t, tb))
        _edit(
            removal,
            repo_root,
            STACK_TABLES_TEST,
            lambda t, tb=table: _drop_token(t, "expected:", f"`{tb}"),
        )
        _edit(removal, repo_root, CATALOG_FILE, lambda t, tb=table: _drop_catalog(t, tb))

    if (repo_root / PROFILES_FILE).is_file():
        _edit(removal, repo_root, PROFILES_FILE, lambda t: _drop_from_profiles(t, proc))
    removal.notes.append(
        f"{proc}'s port offset stays in scripts/processes/process_ports.csv, which is append-only "
        "so an offset is never handed to another process"
    )
    # The stubs a scaffold wrote go while they still say SCAFFOLDED; anything
    # else that names the process is listed by the reference report below.
    for page in (STACK_PAGE, SHOWCASE_PAGE):
        _edit(removal, repo_root, page, lambda t: without_stubs(t, proc))
    return _with_references(removal, repo_root, names - kept)


def _with_references(removal: Removal, repo_root: Path, names: set[str]) -> Removal:
    """`removal`, with every line its edits leave still naming one of `names`."""
    removal.references = stale_references(repo_root, names, removal.deletes, removal.rewrites)
    return removal


def _reaction_removal(repo_root: Path, name: str, job_file: Path) -> Removal:
    """A reaction is its file and its test: no table, profile or port, since
    it has no process of its own."""
    removal = Removal(name)
    removal.deletes.append(job_file)
    _remove_test(removal, repo_root, name)
    return removal


def _remove_test(removal: Removal, repo_root: Path, job: str) -> set[Path]:
    """Queue `job`'s test file and its nsList entry; return what goes."""
    test_file = TEST_DIR / f"test_{job}.q"
    if not (repo_root / test_file).is_file():
        return set()
    ns = _NAMESPACE.search((repo_root / test_file).read_text())
    removal.deletes.append(test_file)
    if ns:
        _edit(
            removal,
            repo_root,
            RUN_TESTS_FILE,
            lambda t: _drop_token(t, "nsList:", f"`.{ns.group(1)}"),
        )
    return {test_file}


def _job_file(repo_root: Path, name: str) -> Path:
    candidates = [
        STREAM_DIR / f"{name}.q",
        WORKER_DIR / f"{name}.q",
        WORKER_DIR / f"{name}_backfill.q",
        REACTION_DIR / f"{name}.q",
    ]
    found = [c for c in candidates if (repo_root / c).is_file()]
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


def _source_is_ours(
    repo_root: Path, source: str, src_file: Path, job_file: Path, force: bool
) -> bool:
    """Delete a source only when this worker alone reads it, and it is still a scaffold."""
    if not (repo_root / src_file).is_file():
        return False
    for path in sorted((repo_root / WORKER_DIR).glob("*.q")):
        if path.relative_to(repo_root) == job_file:
            continue
        for fn, _, fields in declaration_calls(path.read_text()):
            if fn == "qetl.job.bounded.define" and symbols(fields.get("source", ""))[:1] == (
                source,
            ):
                return False
    return force or MARKER in (repo_root / src_file).read_text()


def _mentioned_elsewhere(repo_root: Path, table: str, gone: set[Path]) -> bool:
    """Whether any q file that stays names `table` as a symbol."""
    pattern = re.compile(rf"`{re.escape(table)}\b")
    for directory in ("src", "scripts", "tests"):
        for path in (repo_root / directory).rglob("*.q"):
            rel = path.relative_to(repo_root)
            if rel in gone or rel in _BOOKKEEPING or rel == RUN_TESTS_FILE:
                continue
            if pattern.search(path.read_text(errors="replace")):
                return True
    return False


def _edit(removal: Removal, repo_root: Path, path: Path, change) -> None:
    """Apply `change` to `path`'s pending text, keeping only real changes."""
    if path not in removal.rewrites and not (repo_root / path).is_file():
        return
    current = removal.rewrites.get(path) or (repo_root / path).read_text()
    new = change(current)
    if new != current:
        removal.rewrites[path] = new


def _drop_token(text: str, prefix: str, token: str) -> str:
    """`text` with `token` taken out of its one line starting `prefix`."""
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            lines[i] = re.sub(rf"{re.escape(token)}(?=[`;\s]|$)", "", line, count=1)
    return "".join(lines)


def _drop_definition(text: str, table: str) -> str:
    """`text` without `table`'s definition, the comment above it, and the
    blank line a scaffold puts before that comment."""
    lines = text.splitlines(keepends=True)
    at = [i for i, line in enumerate(lines) if line.startswith(f"{table}:([]")]
    if len(at) != 1:
        return text
    start = at[0]
    while start > 0 and lines[start - 1].startswith("/"):
        start -= 1
    if start > 0 and not lines[start - 1].strip():
        start -= 1
    return "".join(lines[:start] + lines[at[0] + 1 :])


def _drop_catalog(text: str, table: str) -> str:
    """`text` without `table`'s `.qcat.describe` entry and its continuation lines."""
    lines = text.splitlines(keepends=True)
    head = f".qcat.describe[`{table}]:"
    for i, line in enumerate(lines):
        if line.startswith(head):
            end = i + 1
            while end < len(lines) and lines[end][:1] in (" ", "\t"):
                end += 1
            return "".join(lines[:i] + lines[end:])
    return text


def _drop_from_profiles(text: str, proc: str) -> str:
    """profiles.py without `proc` in any one-line profile, or its exemption."""
    quoted = f'"{proc}"'
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    skipping = False
    for line in lines:
        if skipping:
            skipping = line.strip() != "),"
            continue
        if line.startswith(f"    {quoted}: ("):
            skipping = not line.rstrip().endswith("),")
            continue
        if quoted in line and re.match(r'^\s+"\w+": \(.*\),\s*$', line):
            head, inner = line.split(": (", 1)
            members = [m.strip() for m in inner.rsplit("),", 1)[0].split(",") if m.strip()]
            members = [m for m in members if m != quoted]
            joined = ", ".join(members) + ("," if len(members) == 1 else "")
            line = f"{head}: ({joined}),\n"
        out.append(line)
    return "".join(out)
