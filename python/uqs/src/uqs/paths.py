"""Where everything lives, and whether it is runnable.

UqsPaths is the one place that knows the layout of the vendored trees
and the writable data directory; every other module takes it as a parameter
rather than recomputing paths, so a relocated demo is one change here."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from uqs.logger import get_logger
from uqs.runtimes import DEFAULT_RUNTIME, RUNTIME_ENV, RUNTIMES, Runtime

log = get_logger()


#: ---------------------------------------------------------------------------
#: Where this repository keeps each kind of file, relative to its root.
#: ---------------------------------------------------------------------------
#: One home, because `src/etl/streaming` was spelled four ways (pipeline_edges,
#: scaffold, src/etl/init.q, tests/lib/support.q): four places to edit.
#:
#: RELATIVE, because every consumer joins them to a root it already has - the
#: scaffold to the repo root it is writing into, the edge verifier to the one
#: it is reading. An absolute constant would decide that for them.
ETL_DIR = Path("src/etl")
STREAM_DIR = ETL_DIR / "streaming"
SOURCE_DIR = ETL_DIR / "sources"
WORKER_DIR = ETL_DIR / "workers"
#: Reactions: handlers a bounded worker's published window fires. Not jobs
#: with a process of their own - they run inside whichever process publishes.
REACTION_DIR = ETL_DIR / "reactions"

TEST_DIR = Path("tests/q")
#: The q suite's list of every tickerplant table - the deliberate gate a new
#: table passes through, and the list the scaffold appends a new table to.
STACK_TABLES_TEST = TEST_DIR / "test_stack_tables.q"
#: That list's q name: the suite mirrors `.qetl.plant`, whose tables these are,
#: and the file defines it from the root with the whole path (#1412). A line
#: starts with it, then `:`.
STACK_TABLES_LIST = ".qt.qetl.plant.tables.expected"

PROCESS_SCRIPTS_DIR = Path("scripts/processes")
TABLES_FILE = ETL_DIR / "plant_tables.q"
#: The .qetl.plant API TABLES_FILE's declarations call (schema, nested, sorting,
#: ...), loaded just before it. Split out so the declarations - which every
#: table-defining `uqs job new` appends to - have room under the q-module-size
#: cap (#2503).
PLANT_REGISTRY_FILE = ETL_DIR / "plant_registry.q"
#: What each installed sidecar bundle put in the tree, and which runtimes it
#: is in (stack/bundles.py writes it; model/runtime_members.py reads it).
BUNDLE_LEDGER = ETL_DIR / "installed_bundles.json"
#: Where an installed bundle's connectors are copied, one folder per bundle, and
#: imported from - never from the bundle's own folder (stack/bundle_connectors.py,
#: #1776). Not named *_DIR: those are the folders `uqs job new` writes q into.
INSTALLED_CONNECTORS = ETL_DIR / "connectors"

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

#: The repository's one command for derived artifacts, `derive.py TARGET
#: [--check]` (#1804): `operational-docs` regenerates the files derived from the
#: registry (the process table, src/etl/generated/pipeline_dag.q). CI runs it
#: with --check, so a registry edit that is not followed by it fails the build.
DERIVE_SCRIPT = Path("scripts/generate/derive.py")

#: This package. Spelled here rather than as a string literal in the modules
#: that need it, which a package rename would silently break.
PACKAGE_DIR = Path("python/uqs")


class UqsError(RuntimeError):
    """Raised for anything that stops the demo from being runnable as-is."""


class UqsUsageError(UqsError):
    """A command line wrong as given: exits 2, as Click's usage errors do (#1885)."""


class UqsUnknownProcess(UqsError):
    """A process name the runtime does not declare (procs.unknown_processes):
    a type of its own so the frontend answers it 404, not 422 (#2224)."""


def runtime_from_env() -> str:
    """The runtime UQS_RUNTIME names, or the default; an unknown one is refused."""
    name = os.environ.get(RUNTIME_ENV, "").strip() or DEFAULT_RUNTIME
    if name not in RUNTIMES:
        raise UqsError(
            f"{RUNTIME_ENV}={name!r} is not a runtime - choose one of: {', '.join(RUNTIMES)}"
        )
    return name


def torq_log_stem(procname: str, stream: str) -> str:
    """The stem TorQ names a process's logs with: `<stream>_<procname>`, as
    the alias `<stem>.log` and each dated roll `<stem>_<stamp>.log`. Named
    once, here, for every reader of those files (#819)."""
    return f"{stream}_{procname}"


@dataclass(frozen=True)
class UqsPaths:
    repo_root: Path
    torqhome: Path
    torqapphome: Path
    torqdata: Path
    scripts_dir: Path
    orchestrator_dir: Path
    runtime: str = DEFAULT_RUNTIME  #: which of RUNTIMES bootstrap composes

    @property
    def runtime_declaration(self) -> Runtime:
        """What this runtime composes - read a field, never test the name."""
        return RUNTIMES[self.runtime]

    @property
    def log_dir(self) -> Path:
        """Where every process's logs are: what stack/env.py tells TorQ as
        KDBLOG, and where uqs, the feeds and the checks read them (#819)."""
        return self.torqdata / "logs"

    @property
    def hdb_dir(self) -> Path:
        """The HDB root: what stack/env.py tells TorQ as KDBHDB."""
        return self.torqdata / "hdb"

    def torq_log(self, procname: str, stream: str) -> Path:
        """TorQ's current log for `procname` - `out` or `err` - the alias it
        repoints on each roll, so reading it follows the live file."""
        return self.log_dir / f"{torq_log_stem(procname, stream)}.log"

    @property
    def generated_procs(self) -> Path:
        return self.torqdata / "process.csv"

    @property
    def generated_deployment(self) -> Path:
        """The processes this stack's TorQ discovers (stack/deployment.py)."""
        return self.torqdata / "deployment.txt"

    @property
    def generated_schema(self) -> Path:
        return self.torqdata / "database.q"

    @property
    def generated_gateway_access(self) -> Path:
        return self.torqdata / "gateway_accesslist.txt"

    @property
    def generated_dqe_config(self) -> Path:
        return self.torqdata / "dqengineconfig.csv"

    @property
    def generated_reporter_config(self) -> Path:
        return self.torqdata / "reporter.csv"

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
    def crypto_fills_recorder_config_path(self) -> Path:
        """The fills recorder's config: only stp1's credential, owner-only (#1853)."""
        return self.torqdata / "crypto_fills_recorder_config.yaml"

    @property
    def crypto_fills_recorder_pid_path(self) -> Path:
        return self.orchestrator_dir / "crypto_fills_recorder.pid"

    @property
    def kafka_feed_pid_path(self) -> Path:
        """The Kafka consumer's pid file.

        Beside the crypto recorders', and for the same reason: an external
        publisher this repository starts, which torq.sh knows nothing about
        and so cannot stop.
        """
        return self.orchestrator_dir / "kafka_feed.pid"


#: Directories that together identify this repository's root and nothing else.
#: Both, because either alone is a plausible name inside some other tree.
#: Not lib/torq: a deployed release uses an external TorQ (TORQHOME) and does
#: not ship the vendored one (#773), and it is still this repository.
_ROOT_MARKERS = (ETL_DIR, PACKAGE_DIR)


def is_repo_root(candidate: Path) -> bool:
    """Does `candidate` hold this tree - both _ROOT_MARKERS? A deployed
    release does as well as a checkout, though it ships no lib/torq. The one
    test of a stack root: repo_root searches with it, and the frontend checks
    UQF_FRONTEND_STACK_ROOT with it rather than with its own marker (#802)."""
    return all((candidate / marker).is_dir() for marker in _ROOT_MARKERS)


def root_markers_text() -> str:
    """The markers, for a refusal that has to say what was looked for."""
    return " and ".join(str(m) for m in _ROOT_MARKERS)


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
        if is_repo_root(candidate):
            return candidate
    raise UqsError(
        f"cannot find the repository root above {here}: no parent holds both " + root_markers_text()
    )


#: The variables that move what a deployment keeps outside its release (#773):
#: an existing TorQ, an existing finance starter pack, and the runtime data -
#: HDB, tickerplant logs, status - which must survive every release. TorQ's
#: own names for the first two, which torq.sh and every process already read.
TORQHOME_ENV = "TORQHOME"
TORQAPPHOME_ENV = "TORQAPPHOME"
DATA_ROOT_ENV = "UQS_DATA_ROOT"


def _configured_dir(var: str, marker: str | None, what: str) -> Path | None:
    """The directory `var` names, or None when it is unset.

    Set but wrong is refused, never ignored: falling back to the bundled
    tree would start a stack on a TorQ, or a data directory, nobody chose.
    """
    raw = os.environ.get(var, "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise UqsError(f"{var}={raw!r} must be an absolute path to {what}")
    if not path.is_dir():
        raise UqsError(
            f"{var}={raw!r} is not a directory - it should be {what}; unset it to use the default"
        )
    if marker is not None and not (path / marker).is_file():
        raise UqsError(
            f"{var}={raw!r} has no {marker}, so it is not {what}; unset it to use the default"
        )
    return path


def paths_for_root(root: Path, runtime: str | None = None) -> UqsPaths:
    """Every path the stack uses, for a repository checked out at `root`.

    The one place the layout is spelled. uqf_frontend used to build its own
    copy for a configured root, which is how a move of the data directory
    would have left the frontend starting and stopping a stack in the old one.

    `runtime` defaults to UQS_RUNTIME's. Each has its own data directory,
    its declaration's `data_dir`: one runtime's HDB holds tables another lacks.

    TORQHOME and TORQAPPHOME, when set, name an existing TorQ and starter
    pack in place of the vendored lib/ trees; UQS_DATA_ROOT, when set, holds
    each runtime's data directory in place of the checkout's output/ - so a
    deployed release keeps its data outside itself (#773). Everything else
    reads these paths, so the launcher, the generated environment and the
    prerequisite checks follow them without a branch of their own.
    """
    runtime = runtime or runtime_from_env()
    if runtime not in RUNTIMES:
        raise UqsError(f"{runtime!r} is not a runtime - choose one of: {', '.join(RUNTIMES)}")
    torqhome = _configured_dir(TORQHOME_ENV, "torq.q", "a TorQ installation")
    torqapphome = _configured_dir(TORQAPPHOME_ENV, "database.q", "a TorQ finance starter pack")
    data_root = _configured_dir(DATA_ROOT_ENV, None, "the directory runtime data lives in")
    return UqsPaths(
        repo_root=root,
        torqhome=torqhome or root / "lib" / "torq",
        torqapphome=torqapphome or root / "lib" / "torq-finance-starter-pack",
        # output/, with everything else the repository generates at runtime -
        # not scripts/output/, where it used to live beside the source.
        torqdata=(data_root or root / "output") / RUNTIMES[runtime].data_dir,
        scripts_dir=root / "scripts",
        orchestrator_dir=root / PACKAGE_DIR,
        runtime=runtime,
    )


def default_paths() -> UqsPaths:
    return paths_for_root(repo_root())
