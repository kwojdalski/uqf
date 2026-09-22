"""One process's declaration: the Pipeline dataclass and the script paths
it resolves.

Split out of pipelines.py when the registry grew past the module-size
threshold test_module_split.py holds it to. The registry - the PIPELINES
tuple, its derived offsets and the edge verification - stays there; this is
the shape of one entry, which changes for different reasons (a new field, a
new derived property) and at a different rate."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

#: Paths under $UQFSCRIPTS, which is scripts/. The subdirectory is part of
#: the name because that is what lands in process.csv's load column - see
#: load_expression below. scripts/ was foldered by role in #241.
PIPELINE_LIB_SCRIPT = "processes/torq_pipeline.q"

#: The one process script every streaming job runs under - the feeds that
#: publish on a timer as well as the jobs that subscribe. Which job a process
#: runs is decided in q, by its procname: each job under src/etl/streaming/
#: declares the process that runs it, and the runner looks itself up. There
#: were EIGHT near-identical scripts here before, one per job.
STREAM_RUNNER_SCRIPT = "processes/torq_stream.q"

# The access list a real .sub.subscribe subscriber needs: an ETL process
# borrows an already-credentialed proctype so .servers.startup[] can open an
# access-listed handle to stp1. Feeds only publish and need no credentials.
_ETL_ACCESS_LIST = "${TORQAPPHOME}/appconfig/passwords/accesslist.txt"


class PipelineKind(StrEnum):
    """What shape of process a `Pipeline` declares.

    This was a bare `str` with the four values written in a comment beside
    it, which made a typo in `registry.py` silent: an unrecognised kind falls
    through `proctype`'s two checks to "metrics" and through `access_list` to
    the subscriber list, so a mistyped feed would be declared, started, and
    given credentials it does not need, with nothing raised anywhere.

    A StrEnum rather than a plain Enum because these values are written into
    `process.csv` and compared against strings read back out of it. A bare
    Enum would serialise as `PipelineKind.FEED`.
    """

    #: Publishes only, subscribes to nothing, so it needs no credentials.
    FEED = "feed"
    #: Subscribes, so it needs the access list.
    ETL = "etl"
    #: An etl of one shape: N source tables in, one canonical table out, one
    #: declared transform per source (`.qnorm`).
    NORMALIZER = "normalizer"
    #: Bounded: registers with discovery, runs a window range, exits.
    BACKFILL = "backfill"


@dataclass(frozen=True)
class Pipeline:
    """One uqf-authored uqf stack process, declared once.

    Everything a pipeline needs in three generated places - its
    `{KDBBASEPORT}+N` port offset, its process.csv row, and its `database.q`
    table definition - is derived from this single entry, so adding a
    pipeline is one list entry rather than a port constant plus a schema
    string plus a _base_process_rows() block (and remembering to chain the
    offset comment to the previous one).

    `kind` picks the two fields that always move together: a "feed" gets
    proctype "feed" and no access list, an "etl" gets proctype "metrics" and
    the subscriber access list.
    """

    procname: str
    script: str
    kind: PipelineKind
    table: str | None = None  # the table it publishes onto the tickerplant, if any
    schema: str | None = None  # that table's database.q definition
    # Only whether to LOAD the library. There was once a second flag to skip
    # publish-edge verification for a pipeline publishing through
    # .qpipe.publish; the check now reads the table at that call site, so
    # every pipeline's declared publishes are verified the same way.
    loads_qpipe: bool = False  # load scripts/processes/torq_pipeline.q ahead of its own script
    offset: int | None = None  # None = allocate from PIPELINE_BLOCK_START in list order
    localtime: str = "1"
    startwithall: str = "1"
    note: str = ""  # why this row deviates from the defaults, if it does

    # --- dataflow edges, for scripts/generate/generate_operational_docs.py ---
    # Declared here so a diagram can be DERIVED rather than drawn, and
    # verified: verify_pipeline_edges() below greps each pipeline's own .q
    # script for its `.sub.subscribe`/`.qpipe.subscribe_etl`/`.u.upd` calls
    # and fails if the declaration and the code disagree. A hand-drawn
    # diagram goes stale silently; this one cannot.
    subscribes: tuple[str, ...] = ()  # tickerplant tables it subscribes to
    # Tables it publishes via `.u.upd`. Defaults to (table,) - set it
    # explicitly only when a pipeline publishes onto a table whose schema it
    # does NOT own (fxfeed1 -> the vendored `quote`), or onto more than one.
    publishes: tuple[str, ...] | None = None
    # tap1 chooses its subscription at runtime from -tables, so no fixed
    # edge exists to declare or to verify.
    subscribes_dynamic: bool = False
    # For a backfill: the .qbw worker this process runs. One script serves
    # every worker and UQF_BACKFILL_WORKER names which at runtime, so
    # without this the link between a process and its worker exists only
    # in an operator's head - which is how two declared workers ended up
    # with no process at all and nothing noticed (#283).
    worker: str | None = None

    @property
    def proctype(self) -> str:
        """The TorQ proctype, which is what discovery indexes processes BY.

        `backfill` is its own type rather than reusing `metrics`, because
        proctype is the lookup key: gethandlebytype on `backfill`
        has to find backfill workers and not the four metrics pipelines that
        happen to share a code path with them.
        """
        if self.kind is PipelineKind.FEED:
            return "feed"
        if self.kind is PipelineKind.BACKFILL:
            return "backfill"
        # etl and normalizer alike: a normalizer subscribes and republishes,
        # which is what makes it a metrics process to discovery.
        return "metrics"

    @property
    def access_list(self) -> str:
        """A backfill reads from the fleet the way an etl does, so it carries
        the same access list. Only a pure feed, which publishes and subscribes
        to nothing, needs none.
        """
        return "" if self.kind is PipelineKind.FEED else _ETL_ACCESS_LIST

    @property
    def published_tables(self) -> tuple[str, ...]:
        """Tables this pipeline publishes onto the tickerplant.

        `table` is schema ownership and is the common case, so it doubles as
        the publish edge; `publishes` overrides it for the pipelines that
        publish onto a table they did not define.
        """
        if self.publishes is not None:
            return self.publishes
        return (self.table,) if self.table else ()

    def load_column(self) -> str:
        """The process.csv `load` value - the .qpipe library first when the
        script needs it, then the script itself. Order matters: see
        PIPELINE_LIB_SCRIPT.
        """
        scripts = [self.script]
        if self.loads_qpipe:
            scripts.insert(0, PIPELINE_LIB_SCRIPT)
        return " ".join(f"${{UQFSCRIPTS}}/{s}" for s in scripts)
