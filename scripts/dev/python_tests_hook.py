"""The `python-tests` pre-push hook: the Python tests a push reaches (#1879, #1965).

It runs at `git push`, not at commit (#1965): pre-commit hands it the pushed
range in PRE_COMMIT_FROM_REF/PRE_COMMIT_TO_REF, and the paths that range
changes stand where the staged paths stood. Run by hand or at commit it still
reads what is staged.

Never the whole suite by default: CI runs that, on every pull request that can
reach a Python test (scripts/dev/ci_scope.py), on a clean Linux checkout. Two
records say what each test depends on, both at the repository root and both
gitignored, and a commit runs the union of what they select:

* testmon's `.testmondata` - the Python each test EXECUTES. When a .py file is
  staged, `pytest --testmon --testmon-forceselect` runs the tests whose
  recorded execution touches what changed (and any new or changed test).
* `.testreach.json` (scripts/dev/test_reach.py) - what each test READS,
  LISTS and STARTS, which testmon cannot see. When anything else is staged -
  a .q, a CSV, a document, a config - the record picks the tests that read
  it, list its directory (when it is added or deleted), or start a process
  that can see it: a q, for a .q file or a file a q file names
  (scripts/dev/test_impact.py's q_readers, the literal edge of its graph), and
  a Python child or an unknown program, for any file. A .py file testmon has
  no record of - a gate a test runs as a script - picks the tests that start
  a Python child.

A q change therefore runs every Python test that starts a q, not just those
whose q loads the changed module: a Python-started q loads the whole tree
(src/init.q, or a TorQ process), and what it loads is outside both testmon's
record and the q graph, whose nodes are q files. The q suites themselves are
the q-tests hook's, selected by the graph.

THE WHOLE SUITE ONLY WHEN THIS CANNOT DECIDE:

* UQF_HOOKS_FULL=1 (scripts/dev/hooks_full.py) - full mode, asked for;
* nothing staged - a manual run, or `pre-commit run --all-files`;
* a file that shapes every test: a conftest.py, pyproject.toml, uv.lock, or
  the recorder and this hook themselves;
* no record yet to decide with.

Every run updates both records - the whole suite rewrites them - so a run
leaves them current for the next. They are shared between the worktrees of
one clone (SHARED, in git's common directory): a worktree without its own
starts from the last one any worktree wrote, and each run writes its own
back. testmon's record names files relative to the root and compares their
content, so another worktree's record selects soundly here: what differs
between the two trees is a change like any other.

Every run leaves out the q_backend tests: q-tests runs the worked examples,
q-docs the docs' q blocks, each on the interpreter it requires (#1808).

A test that JUDGES the reach record (marked `judges_reach_record`, today
test_ci_scope's check that CI and the record agree about web/) runs after
the others, once their reads are merged into the record (#2273). Run beside
them, it judged the record the push was about to correct: a branch adding a
web/ file failed the check against a record from before the file existed,
and an identical second push passed. A scan the push does not reach but
whose record predates a web/ file the tree now has (one master added) is
rerun before the judges too (test_reach.stale_scans, #2276).

    uv run python scripts/dev/python_tests_hook.py            # what the hook runs
    uv run python scripts/dev/python_tests_hook.py --dry-run  # say what it would run
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def _sibling(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return module


_git = _sibling("_git")
hooks_full = _sibling("hooks_full")
test_reach = _sibling("test_reach")

#: pytest-testmon's database, at the rootdir it records against.
TESTMON_DATA = REPO / ".testmondata"
#: What each test reads, lists and starts (scripts/dev/test_reach.py).
REACH_DATA = test_reach.RECORD
#: Where the records are shared between worktrees, under git's common directory.
SHARED = "uqf-test-records"

#: A change here shapes every test, so every test runs.
SHAPES_EVERY_TEST = {
    "pyproject.toml": "the pytest configuration and the dependencies",
    "uv.lock": "the dependencies",
    "scripts/dev/test_reach.py": "the recorder this hook selects by",
    "scripts/dev/python_tests_hook.py": "this hook",
}

#: The tests a q hook runs at commit (the q_backend marker, conftest.py) are
#: left out: q-tests runs the worked examples and q-docs the docs' q blocks,
#: each on the interpreter it requires, so a commit runs each test once (#1808).
NOT_Q_HOOKS = "not q_backend"
#: The marker of a test that judges the reach record: it runs after the record
#: is rewritten, never beside the tests that rewrite it (#2273).
JUDGES_RECORD = "judges_reach_record"
#: The tree the judges hold the record to: test_ci_scope's web/ rule.
JUDGED = "web/"
PYTEST = ["uv", "run", "pytest", "-q"]
#: The tests themselves, spread over the cores; a judge runs alone after them.
SPREAD = ["-n", "auto", "--dist", "loadgroup"]
#: pytest's exit status when nothing was selected: nothing failed.
NO_TESTS = 5


def staged_changes(environ: dict[str, str] | None = None) -> list[tuple[str, str]]:
    """(status letter, path) for each path the push - or, outside a push, the
    commit - adds, changes or deletes; a rename is its old path deleted and its
    new one added. A pre-push hook gets the pushed range from pre-commit."""
    env = os.environ if environ is None else environ
    start, end = env.get("PRE_COMMIT_FROM_REF"), env.get("PRE_COMMIT_TO_REF")
    which = [start, end] if start and end else ["--cached"]
    return _git.changed(*which, cwd=REPO)


def testmon_files(data: Path = TESTMON_DATA) -> set[str] | None:
    """The source files testmon's record says some test executed, or None
    when there is no record it can be asked about."""
    if not data.is_file():
        return None
    try:
        with sqlite3.connect(f"file:{data}?mode=ro", uri=True) as db:
            rows = db.execute("SELECT DISTINCT filename FROM file_fp").fetchall()
    except sqlite3.Error:
        return None
    return {row[0] for row in rows}


def _is_test_module(path: str) -> bool:
    """A test file pytest collects: testmon runs a new or changed one itself."""
    return Path(path).name.startswith("test_") and "/tests/" in f"/{path}"


@dataclass
class Decision:
    """What a commit runs: `full`, or `testmon`'s selection and/or `tests`
    (nodeid -> why) from the reach record; nothing when neither is asked for."""

    why: str
    full: bool = False
    testmon: bool = False
    tests: dict[str, str] = field(default_factory=dict)

    @property
    def mode(self) -> str:
        if self.full:
            return "full"
        return "picked" if self.testmon or self.tests else "none"


def decide(
    changes: list[tuple[str, str]],
    *,
    full_mode: bool,
    known: set[str] | None,
    record: dict | None,
    q_readers: Callable[[Iterable[str]], dict[str, list[str]]],
) -> Decision:
    """Which tests `changes` reach; the whole suite only when that cannot be
    decided (see the module docstring)."""
    if full_mode:
        return Decision(f"{hooks_full.ENV}=1", full=True)
    if not changes:
        return Decision("nothing staged (a manual or --all-files run)", full=True)
    for _, path in changes:
        if Path(path).name == "conftest.py":
            return Decision(f"a conftest changed ({path}), which shapes the tests under it", True)
        if path in SHAPES_EVERY_TEST:
            return Decision(f"{path} changed: {SHAPES_EVERY_TEST[path]}", full=True)
    python = [(s, p) for s, p in changes if p.endswith(".py")]
    other = [(s, p) for s, p in changes if not p.endswith(".py")]
    if python and known is None:
        return Decision(f"no testmon record yet ({TESTMON_DATA.name}) to select with", True)
    unseen = [
        p for s, p in python if s != "D" and not _is_test_module(p) and p not in (known or ())
    ]
    if (other or unseen) and record is None:
        return Decision(f"no record yet ({REACH_DATA.name}) of what the tests read and start", True)

    tests: dict[str, str] = {}
    if other or unseen:
        assert record is not None
        reads = q_readers([p for _, p in other]) if other else {}
        tests = test_reach.reached(record, other + [(s, p) for s, p in python], reads)
        for path in unseen:
            for kind in ("python", "other"):
                for nodeid in test_reach.starters(record, kind):
                    tests.setdefault(nodeid, f"starts a child that may run {path}")
    parts = []
    if python:
        parts.append(f"testmon for {len(python)} Python file(s)")
    if other or unseen:
        parts.append(
            f"{len(tests)} test(s) the record says reach {len(other) + len(unseen)} other file(s)"
        )
    return Decision("; ".join(parts), testmon=bool(python), tests=tests)


# --- the records, shared between worktrees ------------------------------------


def shared_dir(root: Path = REPO) -> Path | None:
    """SHARED under git's common directory - one per clone - or None outside git."""
    try:
        common = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True,
            text=True,
            check=True,
            cwd=root,
        ).stdout.strip()
    except subprocess.CalledProcessError, FileNotFoundError:
        return None
    return Path(common) / SHARED if common else None


def seed(records: Iterable[Path], shared: Path | None) -> list[str]:
    """Copy each record this worktree lacks from `shared`; the names copied."""
    copied = []
    for local in records:
        source = shared / local.name if shared else None
        if not local.exists() and source and source.is_file():
            shutil.copyfile(source, local)
            copied.append(local.name)
    return copied


def publish(records: Iterable[Path], shared: Path | None) -> None:
    """Write each record back to `shared`, whole: a reader never sees half of one."""
    if shared is None:
        return
    shared.mkdir(parents=True, exist_ok=True)
    for local in records:
        if local.is_file():
            tmp = shared / f".{local.name}.{os.getpid()}"
            shutil.copyfile(local, tmp)
            os.replace(tmp, shared / local.name)


# --- running -----------------------------------------------------------------


def commands(
    decision: Decision, ids_file: Path, reach: Path, *, judges: bool = False
) -> list[list[str]]:
    """The pytest runs `decision` takes, each recording into the records.

    `judges` False: every test it selects but those that judge the reach
    record. True: the same selection, only those - run once the first runs
    have written the record they judge (`reach` is then the record itself)."""
    record = ["--testmon", "--record-reach", str(reach)]
    if judges:
        base = [*PYTEST, "-m", f"{JUDGES_RECORD} and {NOT_Q_HOOKS}"]
    else:
        base = [*PYTEST, *SPREAD, "-m", f"{NOT_Q_HOOKS} and not {JUDGES_RECORD}"]
    if decision.full:
        return [[*base, *record]]
    runs = []
    if decision.testmon:
        runs.append([*base, *record, "--testmon-forceselect"])
    if decision.tests:
        modules = sorted({t.split("::", 1)[0] for t in decision.tests})
        modules = [m for m in modules if (REPO / m).is_file()]
        if modules:
            runs.append([*base, *record, "--select-tests", str(ids_file), *modules])
    return runs


def run(decision: Decision, *, dry_run: bool = False) -> int:
    """Run what `decision` selects: the tests, then the record's judges against
    the record those tests have just rewritten. The worst exit status, with
    pytest's "nothing selected" counted a pass."""
    with tempfile.TemporaryDirectory(prefix="python-tests-") as tmp:
        ids_file = Path(tmp) / "tests.txt"
        ids_file.write_text("\n".join(sorted(decision.tests)) + "\n")
        # The whole suite rewrites the reach record rather than adding to it.
        reach = Path(tmp) / REACH_DATA.name if decision.full else REACH_DATA
        if dry_run:
            for cmd in [
                *commands(decision, ids_file, reach),
                *commands(decision, ids_file, REACH_DATA, judges=True),
            ]:
                print(" ".join(cmd))
            return 0
        code = _run_all(commands(decision, ids_file, reach))
        if decision.full and reach.is_file():
            shutil.move(reach, REACH_DATA)
        judges = commands(decision, ids_file, REACH_DATA, judges=True)
        stale = _stale_scans() if judges else set()
        if stale:
            # A scan recorded before a web/ file was added, and not rerun by
            # this push (the file came from master, say): rerun it first (#2276).
            print(
                f"python-tests: {len(stale)} scan(s) recorded before {JUDGED} changed", flush=True
            )
            stale_ids = Path(tmp) / "stale.txt"
            stale_ids.write_text("\n".join(sorted(stale)) + "\n")
            rescan = Decision("stale scans", tests=dict.fromkeys(stale, "stale"))
            code = _run_all(commands(rescan, stale_ids, REACH_DATA)) or code
        judged = _run_all(judges)
    return code or judged


def _stale_scans() -> set[str]:
    """The tests this tree recorded whose scan of JUDGED misses a file it has
    now. Another worktree's are not judged here, so are not rerun either."""
    record = test_reach.load(REACH_DATA)
    if record is None:
        return set()
    files = [f for f in _sibling("test_impact").tracked(REPO) if f.startswith(JUDGED)]
    return test_reach.stale_scans(record, files, test_reach.recorded_here(record, REPO))


def _run_all(runs: list[list[str]]) -> int:
    code = 0
    for cmd in runs:
        rc = subprocess.run(cmd, cwd=REPO, check=False).returncode
        if rc not in (0, NO_TESTS) and code == 0:
            code = rc
    return code


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="say what would run, run nothing")
    args = ap.parse_args()

    shared = shared_dir()
    records = (TESTMON_DATA, REACH_DATA)
    if not args.dry_run:
        for name in seed(records, shared):
            print(f"python-tests: started from the shared {name} ({shared})", flush=True)
    decision = decide(
        staged_changes(),
        full_mode=hooks_full.full(),
        known=testmon_files(),
        record=test_reach.load(REACH_DATA),
        q_readers=_sibling("test_impact").q_readers,
    )
    print(f"python-tests: {decision.mode} - {decision.why}", flush=True)
    for nodeid, why in list(sorted(decision.tests.items()))[:5]:
        print(f"  {nodeid}  ({why})")
    if len(decision.tests) > 5:
        print(f"  ... and {len(decision.tests) - 5} more")

    code = run(decision, dry_run=args.dry_run)
    if not args.dry_run:
        publish(records, shared)
    return code


if __name__ == "__main__":
    sys.exit(main())
