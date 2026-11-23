"""What `uqs job remove` may take: which files and tables are the job's, what
it refuses, and the one refusal it may be told past (#1852) with its guardrail.

`uqs job remove` only undoes what the scaffold wrote. A file is the job's to
delete only when the scaffold provably wrote it - the job file names it, or it
carries the scaffold's marker - and it is still as written; anything else
needs `--override even-if-edited`. Anything another job still depends on is
refused, with the dependent named. Every such decision is made here, once;
scaffold/remove.py plans the edits from them.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from uqs.guardrails import Guardrail
from uqs.model.declarations import (
    declaration_calls,
    declared_outputs,
    reaction_calls,
    read_declarations,
    read_file,
    read_file_text,
    symbols,
)
from uqs.model.pipeline import PipelineKind
from uqs.paths import (
    BUNDLE_LEDGER,
    CATALOG_FILE,
    REACTION_DIR,
    STACK_TABLES_TEST,
    TABLES_FILE,
    TEST_DIR,
    WORKER_DIR,
    UqsError,
)
from uqs.scaffold.external import EXTERNAL_DIR, connector_mark, external_files, feed_module
from uqs.scaffold.jobs import test_namespace
from uqs.scaffold.profile import PROFILES_FILE
from uqs.scaffold.transform import MODES, streaming_test

MARKER = "SCAFFOLDED"
#: What every scaffolded table definition's comment says until it is written:
#: with it, the definition is still the scaffold's, whoever's job is gone.
_ROW_PLACEHOLDER = "<one line: what a row means>"
BOUNDED = "qetl.job.bounded.define"
#: Files whose mention of a table is bookkeeping, not a use: the scaffold's own
#: appends, and the generated job graph, which is regenerated after a removal.
_BOOKKEEPING = (
    TABLES_FILE,
    CATALOG_FILE,
    STACK_TABLES_TEST,
    Path("src/etl/generated/pipeline_dag.q"),
    Path("src/etl/generated/load_plan.q"),
)

#: The one refusal `uqs job remove` may be told past (#1852).
EVEN_IF_EDITED = "even-if-edited"
GUARDRAILS = (
    Guardrail(
        EVEN_IF_EDITED,
        ("job remove",),
        "remove a job, or a file it scaffolded, that has been written - no SCAFFOLDED marker "
        "left, or no longer the text it was written with - as if it were still a scaffold",
        risk="the job's own code, not just a scaffold, is deleted from the tree",
        action="commit or copy the job first: git has it, nothing else does",
    ),
)


def refuse_bundled(repo_root: Path, job_file: Path) -> None:
    """Refuse a job a bundle installed, override or not (#2923). Its file, its
    table and catalog entries in the bundle's `/ BEGIN bundle` blocks and the
    ledger's record of all three are the install's: removed here, the file
    went, the blocks were cut into, and the ledger still claimed them. The
    step that exists is the bundle's own - an install replaces whatever an
    earlier version of the same bundle put there."""
    path = repo_root / BUNDLE_LEDGER
    if not path.is_file():
        return
    try:
        ledger = json.loads(path.read_text())
    except ValueError as exc:
        raise UqsError(f"{BUNDLE_LEDGER} is not JSON ({exc}) - fix or remove it by hand") from None
    owners = sorted(n for n, entry in ledger.items() if str(job_file) in entry.get("files", {}))
    if owners:
        raise UqsError(
            f"{job_file} was installed by bundle {owners[0]} ({BUNDLE_LEDGER}), and uqs job "
            "remove only undoes a scaffold: take the job out of the bundle's folder and install "
            "the bundle again (`uqs job install <folder>`, or `uqs runtime prepare` for a "
            "runtime's bundles), which removes what its earlier version put there"
        )


def refuse_written(
    deletes: list[Path], repo_root: Path, force: bool, proven: set[Path], exact: dict[Path, str]
) -> None:
    """Refuse, without `force`, to delete a file the scaffold wrote and someone
    has since written: its MARKER gone, or its text no longer `exact`'s (#2832)."""
    if force:
        return
    written = [
        str(p)
        for p in deletes
        if p not in proven
        and MARKER not in (text := (repo_root / p).read_text())
        and text != exact.get(p)
    ]
    if written:
        raise UqsError(
            f"{', '.join(written)} no longer read as the scaffold wrote them - they have been "
            "written, and uqs job remove only undoes a scaffold. Pass --override even-if-edited "
            "--reason REASON to remove them anyway"
        )


def refuse_dependents(
    repo_root: Path, job_file: Path, fn: str, job: str, fields: dict[str, str]
) -> None:
    """Refuse while another job resolves only through `job` (#2834): a twin
    whose `twin_of` names it, or a reaction watching the dataset only it
    fills. Either fails the load once `job` is gone, as `uqs job new` would
    have refused to scaffold it - so it is named, to be removed first. And a
    job subscribed to a table only `job` publishes (#2925): it would start
    with nothing feeding it, in every profile that holds both."""
    workers = _workers(repo_root, job_file)
    found = []
    if fn == BOUNDED:
        dataset = declared_outputs(fields, job)[:1]
        if dataset and not any(declared_outputs(f, w)[:1] == dataset for _, w, f in workers):
            for path in sorted((repo_root / REACTION_DIR).glob("*.q")):
                found += [
                    f"reaction {r.name} ({path.relative_to(repo_root)}) watches {dataset[0]}, "
                    f"which only {job} fills"
                    for r in reaction_calls(path.read_text())
                    if r.dataset == dataset[0]
                ]
    else:
        found = [
            f"{w} ({path}) declares twin_of {job}"
            for path, w, f in workers
            if symbols(f.get("twin_of", ""))[:1] == (job,)
        ] + _unfed_subscribers(repo_root, job_file, job)
    if found:
        raise UqsError(
            f"{job} cannot be removed while another job depends on it: {'; '.join(found)} "
            "- remove it first"
        )


def refuse_still_profiled(text: str, proc: str) -> None:
    """Refuse when profiles.py's `text`, as the removal leaves it, still lists
    `proc` in a profile - a layout the edit does not read. A profile naming a
    process that is gone cannot start, and the removal reported success (#2926)."""
    for node in ast.parse(text).body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == "PROFILES":
            profiles = ast.literal_eval(node.value) if node.value is not None else {}
            if any(proc in leaves for leaves in profiles.values()):
                raise UqsError(
                    f"{PROFILES_FILE} still lists {proc} in a profile this cannot edit - take "
                    "it out by hand, then run it again"
                )


def _unfed_subscribers(repo_root: Path, job_file: Path, job: str) -> list[str]:
    """Each other job subscribed to a table `job` publishes that nothing else
    feeds: no other streaming job or normalizer publishes it, and no
    connector produces it (#2925) - the plant's producers, as the profile
    closure check (test_profiles.py) counts them."""
    from uqs.model.dependencies import external_producers

    others = [
        d
        for d in read_declarations(repo_root)
        if d.path != repo_root / job_file and d.kind is not PipelineKind.BACKFILL
    ]
    fed = {t for d in others for t in d.publishes} | set(external_producers(repo_root))
    publishes = [t for d in read_file(repo_root / job_file) for t in d.publishes]
    return [
        f"{d.name} ({d.path.relative_to(repo_root)}) subscribes to {table}, which only {job} "
        "publishes"
        for table in publishes
        if table not in fed
        for d in others
        if table in d.subscribe_to
    ]


def generated_test(job: str, fields: dict[str, str], exact: dict[Path, str]) -> None:
    """Record the test a `--transform` scaffold of `job` wrote. A passthrough's
    passes as written, so it holds no MARKER: its text is the proof."""
    transform = symbols(fields.get("transform", ""))[:1]
    sub, pub = symbols(fields.get("subscribe_to", ""))[:1], declared_outputs(fields, job)[:1]
    modes = [m for m in MODES if transform == (f"{job}_{m}",)]
    if modes and sub and pub:
        exact[TEST_DIR / f"test_{job}.q"] = streaming_test(
            job, test_namespace(job), modes[0], sub[0], pub[0]
        )


def _workers(repo_root: Path, job_file: Path) -> list[tuple[Path, str, dict[str, str]]]:
    """(file, worker, fields) for every bounded worker but `job_file`'s."""
    return [
        (path.relative_to(repo_root), worker, fields)
        for path in sorted((repo_root / WORKER_DIR).glob("*.q"))
        if path.relative_to(repo_root) != job_file
        for fn, worker, fields in declaration_calls(path.read_text())
        if fn == BOUNDED
    ]


def connector_files(
    repo_root: Path, job: str, fields: dict[str, str], notes: list[str], exact: dict[Path, str]
) -> list[Path]:
    """The Python half a connector scaffold of `job` wrote - none when no
    connector scaffold did (#2831): a plain job called `kafka` does not own
    the hand-written kafka_streamer.py. The feed module records which
    scaffold wrote it, and the text it was written with is known exactly."""
    present = [Path(f) for f in external_files(job) if (repo_root / f).is_file()]
    feed = Path(f"{EXTERNAL_DIR}/{job}_feed.py")
    if not present:
        return []
    if feed not in present or connector_mark(job) not in (repo_root / feed).read_text():
        notes.append(
            f"kept {', '.join(str(p) for p in present)}: no connector scaffold of {job} wrote them"
        )
        return []
    raw, out = symbols(fields.get("subscribe_to", ""))[:1], declared_outputs(fields, job)[:1]
    if raw and out:
        exact[feed] = feed_module(job, raw[0], out[0])
    return present


def user_of(repo_root: Path, name: str, gone: set[Path]) -> Path | None:
    """The first q file that stays and names `name` (a table or a source) as a symbol."""
    pattern = re.compile(rf"`{re.escape(name)}\b")
    for directory in ("src", "scripts", "tests"):
        for path in sorted((repo_root / directory).rglob("*.q")):
            rel = path.relative_to(repo_root)
            if rel in gone or rel in _BOOKKEEPING:
                continue
            if pattern.search(path.read_text(errors="replace")):
                return rel
    return None


def living_names(repo_root: Path, job_file: Path) -> set[str]:
    """Every job and process the tree declares, but `job_file`'s."""
    return {
        n
        for d in read_declarations(repo_root)
        if d.path != repo_root / job_file
        for n in (d.name, d.procname)
    }


def defined_by(repo_root: Path, table: str, owners: set[str], living: set[str]) -> bool:
    """Whether plant_tables.q's definition of `table` is one a scaffold of
    `owners` wrote: the comment above it opens "/ <owner>'s", as every
    scaffold's does. References cannot tell a table's owner from a guest that
    publishes onto it (#1585), so an authored definition is kept.

    Or a scaffold of a job no longer in the tree - none of `living` - whose
    comment is still unwritten: the job that defined it went first, while
    this one still used it, and nobody else is left to take it (#2842)."""
    path = repo_root / TABLES_FILE
    if not path.is_file():
        return False
    lines = path.read_text().splitlines()
    at = [
        i for i, line in enumerate(lines) if re.match(rf"(?:\.qetl\.plant\.)?{table}:\(\[\]", line)
    ]
    if len(at) != 1:
        return False
    start = at[0]
    while start > 0 and lines[start - 1].startswith("/"):
        start -= 1
    comment = lines[start : at[0]]
    named = {m.group(1) for line in comment if (m := re.match(r"/ (\w+)'s ", line))}
    if named & owners:
        return True
    return bool(named) and not named & living and any(_ROW_PLACEHOLDER in c for c in comment)


def orphaned_inputs(
    repo_root: Path,
    job_file: Path,
    text: str,
    decided: list[str],
    gone: set[Path],
    living: set[str],
) -> list[str]:
    """The tables the job in `job_file` (source `text`) only reads that go with
    it: none of `decided` (its outputs, already ruled on), nothing outside
    `gone` names them, and their definition is a scaffold of a job no longer
    in the tree. #2842's rule for the last writer, applied to the last reader:
    the producer went first, while this one still read the table, and nobody
    else is left to take it (#2924)."""
    declared = read_file_text(text, repo_root / job_file)
    reads = declared[0].subscribe_to if declared else ()
    return [
        t
        for t in reads
        if t not in decided
        and user_of(repo_root, t, gone) is None
        and defined_by(repo_root, t, set(), living)
    ]
