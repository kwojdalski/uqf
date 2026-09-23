"""One process's declaration: the Pipeline dataclass and the script paths
it resolves.

Split out of model/pipelines.py when the registry grew past the module-size
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


class _FromDeclaration:
    """Sentinel: this edge is declared in the job's own q file, read it there.

    A streaming job's `.qstream.register` already names its procname, the
    tables it subscribes to and the tables it publishes. Restating them here
    made the registry a second copy of a fact the code states - and
    `verify_pipeline_edges` existed to check the two copies agreed, which is
    a check that only has a job to do because the duplication exists.

    So the registry stops restating them. `FROM_DECLARATION` says "the q file
    is the source of truth for this edge", and `resolve_edges` reads it.

    Distinct from `None` on `publishes`, which already means something else:
    "default to `(table,)`". A pipeline that owns exactly one table and
    publishes onto it still says nothing, and gets the old behaviour.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return "FROM_DECLARATION"


#: See `_FromDeclaration`. Spelled as a singleton so `is` identifies it.
FROM_DECLARATION = _FromDeclaration()


class PipelineKind(StrEnum):
    """What shape of process a `Pipeline` declares.

    This was a bare `str` with the four values written in a comment beside
    it, which made a typo in `model/registry.py` silent: an unrecognised kind falls
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
    # FROM_DECLARATION reads it from the job's own .qstream.register / .qnorm
    # .define instead, which is where a streaming job already states it.
    subscribes: tuple[str, ...] | _FromDeclaration = ()
    # Tables it publishes via `.u.upd`. Defaults to (table,) - set it
    # explicitly only when a pipeline publishes onto a table whose schema it
    # does NOT own (fxfeed1 -> the vendored `quote`), or onto more than one.
    publishes: tuple[str, ...] | None | _FromDeclaration = None
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
    def schema(self) -> str | None:
        """The owned table's q definition, read from uqf_stack_tables.q.

        DERIVED, not declared. This was a `schema=X_TABLE_SCHEMA` field sitting
        beside `table="x"`, and `X_TABLE_SCHEMA` is defined in model/schemas.py as
        `_DEFS["x"]` - the same lookup, written out by hand in two files.
        Across the registry: fourteen pipelines carry a table, fourteen carried
        a schema, and not one of them disagreed, which is what a derived value
        looks like before anyone derives it.

        A table the q file does not define RAISES rather than returning None.
        `.u.upd` onto a table the plant has never been told about discards the
        rows in silence (#288), so a pipeline naming a table nobody defined is
        a mistake to report, not a None to pass along.
        """
        if self.table is None:
            return None
        from torq_orchestrator.model.schemas import _DEFS

        if self.table not in _DEFS:
            raise KeyError(
                f"{self.procname}: declares table {self.table!r}, which "
                "scripts/processes/uqf_stack_tables.q does not define - the "
                "plant would discard its rows without an error"
            )
        return _DEFS[self.table]

    @property
    def subscribed_tables(self) -> tuple[str, ...]:
        """Tables this pipeline subscribes to, resolved.

        The resolved spelling of `subscribes`, for the same reason
        `published_tables` is the resolved spelling of `publishes`: a consumer
        that reads the raw field gets the FROM_DECLARATION sentinel instead of
        a tuple, and finds out by iterating it.
        """
        return self._resolved()[0]

    def _resolved(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        # Imported at call time: pipeline_edges imports this module, so a
        # module-scope import here is a cycle.
        from torq_orchestrator.model.pipeline_edges import resolve_edges

        return resolve_edges(self)

    @property
    def published_tables(self) -> tuple[str, ...]:
        """Tables this pipeline publishes onto the tickerplant.

        `table` is schema ownership and is the common case, so it doubles as
        the publish edge; `publishes` overrides it for the pipelines that
        publish onto a table they did not define, and FROM_DECLARATION defers
        it to the job's own q declaration.
        """
        return self._resolved()[1]

    def load_column(self) -> str:
        """The process.csv `load` value - the .qpipe library first when the
        script needs it, then the script itself. Order matters: see
        PIPELINE_LIB_SCRIPT.
        """
        scripts = [self.script]
        if self.loads_qpipe:
            scripts.insert(0, PIPELINE_LIB_SCRIPT)
        return " ".join(f"${{UQFSCRIPTS}}/{s}" for s in scripts)
