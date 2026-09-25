"""Where everything lives, and whether it is runnable.

UqsPaths is the one place that knows the layout of the vendored trees
and the writable data directory; every other module takes it as a parameter
rather than recomputing paths, so a relocated demo is one change here."""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from uqs.logger import get_logger

log = get_logger(__name__)


#: The variable that chooses the q interpreter, everywhere (#414). It is the
#: name `torq.sh` already starts every stack process with, so one setting now
#: reaches the stack, its HDB filler, the backfill launcher and every test
#: lane. There were four rules before - `Q`, `QBIN`, `UQFQ` and a bare `q` on
#: PATH - and the stack and its HDB could run different binaries.
Q_INTERPRETER_ENV = "QCMD"


def q_interpreter(env: Mapping[str, str] | None = None) -> Path:
    """The q interpreter: ``$QCMD`` if set, otherwise ``~/.kx/bin/q``.

    Nothing else - no PATH lookup, because whatever ``q`` is first on PATH
    would be chosen for you, and the README says choosing an interpreter the
    tree is not verified on is explicit. Whether it exists is the caller's
    question: a test skips, a script refuses, the stack reports.
    """
    source = os.environ if env is None else env
    chosen = source.get(Q_INTERPRETER_ENV)
    return Path(chosen) if chosen else Path.home() / ".kx" / "bin" / "q"


#: ---------------------------------------------------------------------------
#: Where this repository keeps each kind of file, relative to its root.
#: ---------------------------------------------------------------------------
#: One home, because `src/etl/streaming` was spelled in four: `pipeline_edges`
#: as `Path("src") / "etl" / "streaming"`, `scaffold` as
#: `Path("src/etl/streaming")`, and twice more in q (`src/etl/init.q`,
#: `tests/lib/testutil.q`). Four spellings of one fact is four places to edit
#: and three chances to miss one.
#:
#: RELATIVE, because every consumer joins them to a root it already has - the
#: scaffold to the repo root it is writing into, the edge verifier to the one
#: it is reading. An absolute constant would decide that for them.
ETL_DIR = Path("src/etl")
STREAM_DIR = ETL_DIR / "streaming"
SOURCE_DIR = ETL_DIR / "sources"
WORKER_DIR = ETL_DIR / "workers"

TEST_DIR = Path("tests/q")
RUN_TESTS_FILE = Path("tests/run_tests.q")
#: The q suite's list of every tickerplant table - the deliberate gate a new
#: table passes through, and the list the scaffold appends a new table to.
STACK_TABLES_TEST = TEST_DIR / "test_stack_tables.q"

PROCESS_SCRIPTS_DIR = Path("scripts/processes")
TABLES_FILE = PROCESS_SCRIPTS_DIR / "uqs_tables.q"

#: The desk catalog's authored half: what each table is for, and which are
#: deliberately not browsable. Loaded by gateway1 (VENDORED_LOAD_OVERLAY in
#: stack/procs.py) and served to the front end over IPC.
#:
#: Only the PROSE. A table's columns and their types come from `meta` on a
#: running process, so there is no copy here to keep in step - which is what
#: the deleted python/uqf_frontend/catalog/columns.csv was, and why it needed
#: a drift test. tests/q/test_catalog.q refuses a published table that is
#: neither described here nor explicitly hidden.
CATALOG_FILE = PROCESS_SCRIPTS_DIR / "uqs_catalog.q"
#: Every process's port offset, append-only. Generated: a process's port is
#: the one fact about it no declaration can supply, because it has to survive
#: other processes being added around it.
PROCESS_PORTS_FILE = PROCESS_SCRIPTS_DIR / "process_ports.csv"

#: Regenerates the files derived from the registry (the process table and
#: src/etl/generated/pipeline_dag.q). CI runs it with --check, so a registry
#: edit that is not followed by this fails the build.
OPERATIONAL_DOCS_SCRIPT = Path("scripts/generate/generate_operational_docs.py")
#: Regenerates docs/man.q from the qDoc blocks under src/, which a scaffolded
#: q file adds to. Also run by CI with --check.
MAN_REGISTRY_SCRIPT = Path("scripts/generate/generate_man_registry.py")

#: This package. Spelled here rather than as a string literal in the modules
#: that need it, which a package rename would silently break.
PACKAGE_DIR = Path("python/uqs")


class UqsError(RuntimeError):
    """Raised for anything that stops the demo from being runnable as-is."""


@dataclass(frozen=True)
class UqsPaths:
    repo_root: Path
    torqhome: Path
    torqapphome: Path
    torqdata: Path
    scripts_dir: Path
    orchestrator_dir: Path

    @property
    def generated_procs(self) -> Path:
        return self.torqdata / "process.csv"

    @property
    def generated_schema(self) -> Path:
        return self.torqdata / "database.q"

    @property
    def generated_setenv(self) -> Path:
        return self.torqdata / "setenv.sh"

    @property
    def overrides_path(self) -> Path:
        return self.orchestrator_dir / "process_overrides.csv"

    @property
    def crypto_recorder_pid_path(self) -> Path:
        return self.orchestrator_dir / "crypto_recorder.pid"

    @property
    def crypto_recorder_config_path(self) -> Path:
        return self.torqdata / "crypto_recorder_config.yaml"

    @property
    def crypto_fills_recorder_pid_path(self) -> Path:
        return self.orchestrator_dir / "crypto_fills_recorder.pid"

    @property
    def databento_feed_pid_path(self) -> Path:
        """The live Databento handler's pid file.

        Beside the crypto recorders' because it is the same kind of thing:
        an external publisher this repository starts, which torq.sh knows
        nothing about and so cannot stop.
        """
        return self.orchestrator_dir / "databento_feed.pid"


#: Directories that together identify this repository's root and nothing else.
#: Both, because either alone is a plausible name inside some other tree.
_ROOT_MARKERS = (Path("lib") / "torq", ETL_DIR)


def repo_root() -> Path:
    """This repository's root, found by searching upward for a marker.

    NOT by counting directory levels, which is what three modules did
    separately - `paths.py` (`parents[2]` then `parents[1]`), `model/schemas.py`
    (`parents[4]`) and `model/pipeline_edges.py` (`parents[4]`). Each count is a fact
    about how deep that particular file sits, so moving a module one directory
    changes its count: the path still resolves, to the wrong place, and no type
    checker can see it.

    That is not hypothetical. `default_paths`' own comment said
    "this file: .../uqs/core.py" long after the split moved it to
    `paths.py` - the count stayed right by luck, and the comment describing it
    went stale unnoticed.

    Searching for a marker is indifferent to how deep the caller is, which is
    what makes moving modules into subdirectories safe - and that was then
    done: thirty modules went into six folders, every one of them a directory
    deeper than the counts above assumed, and nothing here had to change.
    Three level-counts survived the move in `scripts/` and the tests, and they
    are correct because neither tree moved.
    """
    here = Path(__file__).resolve()
    for candidate in (here, *here.parents):
        if all((candidate / marker).is_dir() for marker in _ROOT_MARKERS):
            return candidate
    raise UqsError(
        f"cannot find the repository root above {here}: no parent holds both "
        + " and ".join(str(m) for m in _ROOT_MARKERS)
    )


def paths_for_root(root: Path) -> UqsPaths:
    """Every path the stack uses, for a repository checked out at `root`.

    The one place the layout is spelled. uqf_frontend used to build its own
    copy for a configured root, which is how a move of the data directory
    would have left the frontend starting and stopping a stack in the old one.
    """
    return UqsPaths(
        repo_root=root,
        torqhome=root / "lib" / "torq",
        torqapphome=root / "lib" / "torq-finance-starter-pack",
        # output/, with everything else the repository generates at runtime -
        # not scripts/output/, where it used to live beside the source.
        torqdata=root / "output" / "uqs",
        scripts_dir=root / "scripts",
        orchestrator_dir=root / PACKAGE_DIR,
    )


def default_paths() -> UqsPaths:
    return paths_for_root(repo_root())


#: Where the data directory has lived before, newest first, relative to the
#: repository root. Kept only so the guard below can recognise them; nothing
#: reads from them.
_FORMER_DATA_DIRS = (
    Path("scripts") / "output" / "uqs",
    Path("scripts") / "output" / "uqf-stack",
)


def check_data_dir_was_migrated(paths: UqsPaths) -> None:
    """Refuse to run against a fresh data directory while an old one exists.

    The data directory holds the HDB, the tickerplant logs and the write-down
    database - 6.5GB of it on the machine this was written on - and it has
    moved twice: renamed with the package (`scripts/output/uqf-stack` to
    `scripts/output/uqs`), then out of `scripts/` into `output/uqs`. Either
    move changes where every process looks, and nothing would have ERRORED:
    bootstrap regenerates process.csv and database.q on every command, so
    the stack would have started cleanly
    against an empty HDB and every historical query would have returned no
    rows. A silent empty answer is the worst failure this tree has, and it is
    the one a rename of a data path produces by default.

    So: old present and new absent means the move has not happened, and that
    is refused with the command that does it. Both present is not refused -
    someone may have copied rather than moved, and choosing for them would be
    worse than letting them proceed.

    THIS BLOCKS `stop` TOO, AND THAT IS DELIBERATE. `stop` reaches here
    through `bootstrap`, and exempting it would be worse than the
    inconvenience: `process.csv` lives INSIDE the data directory, so a
    bootstrap allowed through would create the new directory, both would then
    exist, this check would never fire again, and the old HDB would be
    orphaned in silence - the exact outcome it exists to prevent.

    Which is why the message must not say "stop the stack first": that is an
    order this function makes impossible. The move needs no downtime. Every
    location is inside the one checkout, so `mv` is a rename on one filesystem
    - inodes are unchanged and every open file descriptor follows - and a
    restart afterwards is for reopening at the new path, not for safety.
    """
    if paths.torqdata.exists():
        return
    for relative in _FORMER_DATA_DIRS:
        former = paths.repo_root / relative
        if not former.is_dir():
            continue
        raise UqsError(
            f"{former} exists but {paths.torqdata} does not: the data directory "
            f"has moved, and nothing has moved it yet.\n\n"
            f"Run this now - it is safe with the stack up, because both paths are "
            f"on one filesystem, so it is a rename and every running process keeps "
            f"the files it already has open:\n"
            f"    mkdir -p {paths.torqdata.parent} && mv {former} {paths.torqdata}\n\n"
            f"Then restart the stack when convenient, so each process reopens at "
            f"the new path.\n\n"
            f"Skipping this would not fail - the stack would start against an "
            f"empty HDB and every historical query would return no rows."
        )


def check_prerequisites(paths: UqsPaths) -> None:
    check_data_dir_was_migrated(paths)
    if not (paths.torqhome / "torq.q").is_file():
        raise UqsError(f"{paths.torqhome} not found or missing torq.q - is lib/torq vendored?")
    if not (paths.torqapphome / "database.q").is_file():
        raise UqsError(
            f"{paths.torqapphome} not found or missing database.q - "
            "is lib/torq-finance-starter-pack vendored?"
        )
    for tool in ("envsubst", "rlwrap"):
        if shutil.which(tool) is None:
            raise UqsError(
                f"'{tool}' not found on PATH - torq.sh needs it "
                "(macOS: brew install gettext rlwrap)"
            )


#: One entry `clean` would remove: its path, and the bytes it holds.
CleanTarget = tuple[Path, int]


def _entry_size(entry: Path) -> int:
    """Bytes under `entry`, following no symlinks and raising on nothing.

    A file that vanishes mid-walk (a live stack rotating a log) contributes
    zero rather than failing the whole listing - the size is here to tell an
    operator how much is about to go, not to be an audited total.
    """
    if entry.is_file() or entry.is_symlink():
        try:
            return entry.lstat().st_size
        except OSError:
            return 0
    total = 0
    for child in entry.rglob("*"):
        try:
            if child.is_file() and not child.is_symlink():
                total += child.lstat().st_size
        except OSError:
            continue
    return total


def clean_targets(paths: UqsPaths, match: str | None = None) -> list[CleanTarget]:
    """What `clean` would remove, deepest-matching-first, with sizes.

    Without `match` this is the whole data directory as a single entry, which
    is what `clean` has always removed. With one, the tree is walked top-down
    and each path is tested as a POSIX-style path RELATIVE to the data
    directory (`logs`, `logs/out_rdb1.log`), so the pattern reads the way the
    operator sees the tree rather than against an absolute path whose prefix
    is different on every machine.

    A directory that matches is taken whole and not descended into: matching
    `^logs$` means the operator asked for the logs, not for a list of 937
    files that happens to be the same thing. A directory that does not match
    is descended, so `logs/out_rdb1` can be reached without naming `logs`.

    `re.search`, not `re.fullmatch`: `--match logs` should find the logs.
    Anchor with `^`/`$` to be exact.
    """
    root = paths.torqdata
    if not root.exists():
        return []
    if match is None:
        return [(root, _entry_size(root))]
    try:
        pattern = re.compile(match)
    except re.error as exc:
        raise UqsError(f"--match is not a valid regular expression: {exc}") from exc

    found: list[CleanTarget] = []

    def walk(directory: Path) -> None:
        for entry in sorted(directory.iterdir()):
            relative = entry.relative_to(root).as_posix()
            if pattern.search(relative):
                found.append((entry, _entry_size(entry)))
            elif entry.is_dir() and not entry.is_symlink():
                walk(entry)

    walk(root)
    return found


def clean(paths: UqsPaths, match: str | None = None, dry_run: bool = False) -> list[CleanTarget]:
    """Remove the data directory, or the parts of it `match` selects.

    Returns what was removed - or, with `dry_run`, what would have been, having
    removed nothing. The caller reports; this decides and acts, so that the
    listing a dry run shows is produced by the same walk that the real
    removal uses and cannot describe a different set.
    """
    targets = clean_targets(paths, match)
    if not targets:
        if match is not None:
            log.info("nothing under {} matches {!r}", paths.torqdata, match)
        else:
            log.info("{} does not exist, nothing to clean", paths.torqdata)
        return []
    for entry, _size in targets:
        if dry_run:
            log.info("would remove {}", entry)
            continue
        log.info("Removing {}", entry)
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink(missing_ok=True)
    return targets
