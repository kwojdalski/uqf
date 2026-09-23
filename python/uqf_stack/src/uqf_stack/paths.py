"""Where everything lives, and whether it is runnable.

UqfStackPaths is the one place that knows the layout of the vendored trees
and the writable data directory; every other module takes it as a parameter
rather than recomputing paths, so a relocated demo is one change here."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from uqf_stack.logger import get_logger

log = get_logger(__name__)


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
ETL_INIT = ETL_DIR / "init.q"

TEST_DIR = Path("tests/q")
RUN_TESTS_FILE = Path("tests/run_tests.q")
#: The q suite's list of every tickerplant table - the deliberate gate a new
#: table passes through, and the list the scaffold appends a new table to.
STACK_TABLES_TEST = TEST_DIR / "test_stack_tables.q"

PROCESS_SCRIPTS_DIR = Path("scripts/processes")
TABLES_FILE = PROCESS_SCRIPTS_DIR / "uqf_stack_tables.q"

#: Regenerates the files derived from the registry (the process table and
#: src/etl/generated/pipeline_dag.q). CI runs it with --check, so a registry
#: edit that is not followed by this fails the build.
OPERATIONAL_DOCS_SCRIPT = Path("scripts/generate/generate_operational_docs.py")
#: Regenerates docs/man.q from the qDoc blocks under src/, which a scaffolded
#: q file adds to. Also run by CI with --check.
MAN_REGISTRY_SCRIPT = Path("scripts/generate/generate_man_registry.py")

#: This package, and the registry the scaffold appends to. Spelled here rather
#: than in `scaffold/jobs.py`, which hardcoded its own location as a string - a
#: self-reference that the package rename would silently break.
PACKAGE_DIR = Path("python/uqf_stack")
REGISTRY_FILE = PACKAGE_DIR / "src" / "uqf_stack" / "model" / "registry.py"


class UqfStackError(RuntimeError):
    """Raised for anything that stops the demo from being runnable as-is."""


@dataclass(frozen=True)
class UqfStackPaths:
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
    def extra_processes_path(self) -> Path:
        return self.orchestrator_dir / "extra_processes.csv"

    @property
    def extra_schema_path(self) -> Path:
        return self.orchestrator_dir / "extra_schema.q"

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
    "this file: .../uqf_stack/core.py" long after the split moved it to
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
    raise UqfStackError(
        f"cannot find the repository root above {here}: no parent holds both "
        + " and ".join(str(m) for m in _ROOT_MARKERS)
    )


def default_paths() -> UqfStackPaths:
    root = repo_root()
    return UqfStackPaths(
        repo_root=root,
        torqhome=root / "lib" / "torq",
        torqapphome=root / "lib" / "torq-finance-starter-pack",
        torqdata=root / "scripts" / "output" / "uqf-stack",
        scripts_dir=root / "scripts",
        orchestrator_dir=root / PACKAGE_DIR,
    )


def check_prerequisites(paths: UqfStackPaths) -> None:
    if not (paths.torqhome / "torq.q").is_file():
        raise UqfStackError(f"{paths.torqhome} not found or missing torq.q - is lib/torq vendored?")
    if not (paths.torqapphome / "database.q").is_file():
        raise UqfStackError(
            f"{paths.torqapphome} not found or missing database.q - "
            "is lib/torq-finance-starter-pack vendored?"
        )
    for tool in ("envsubst", "rlwrap"):
        if shutil.which(tool) is None:
            raise UqfStackError(
                f"'{tool}' not found on PATH - torq.sh needs it "
                "(macOS: brew install gettext rlwrap)"
            )


def clean(paths: UqfStackPaths) -> None:
    if paths.torqdata.exists():
        log.info("Removing {}", paths.torqdata)
        shutil.rmtree(paths.torqdata)
    else:
        log.info("{} does not exist, nothing to clean", paths.torqdata)
