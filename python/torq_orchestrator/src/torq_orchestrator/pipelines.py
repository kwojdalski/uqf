"""The pipeline registry: one Pipeline entry per uqf process.

Adding a pipeline means adding one entry here, not picking a port number and
remembering three other places to edit - the per-process offsets, the
process.csv rows and the generated schema all derive from this tuple.

Also holds the dataflow-edge declarations and verify_pipeline_edges, which
greps each pipeline's own q script and fails if a declaration disagrees with
the code. A hand-drawn diagram goes stale silently; a derived one cannot."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from torq_orchestrator.logger import get_logger
from torq_orchestrator.schemas import (
    EXECUTION_QUALITY_TABLE_SCHEMA,
    MKT_ORDERBOOK_TABLE_SCHEMA,
    POSITION_TABLE_SCHEMA,
    QUOTES_TABLE_SCHEMA,
    TRADES_TABLE_SCHEMA,
    WIDE_BOOK_TABLE_SCHEMA,
)

log = get_logger(__name__)


DEFAULT_BASE_PORT = 6050

# fxfeed1 sits at the one offset the vendored process.csv leaves free below
# its own dqc/dqe block (+20..+23); every other uqf process is allocated
# contiguously from PIPELINE_BLOCK_START by the PIPELINES registry further
# down this file, which is also where the per-process port offset constants
# (FXFEED_PORT_OFFSET, MARKOUT_PORT_OFFSET, ...) are now derived rather than
# hand-chained. Adding a pipeline means adding one Pipeline() entry, not
# picking a number and remembering three other places to edit.
FXFEED_PINNED_OFFSET = 19
PIPELINE_BLOCK_START = 24

PIPELINE_LIB_SCRIPT = "torq_pipeline.q"

# The access list a real .sub.subscribe subscriber needs: an ETL process
# borrows an already-credentialed proctype so .servers.startup[] can open an
# access-listed handle to stp1. Feeds only publish and need no credentials.
_ETL_ACCESS_LIST = "${TORQAPPHOME}/appconfig/passwords/accesslist.txt"


@dataclass(frozen=True)
class Pipeline:
    """One uqf-authored TorQ demo process, declared once.

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
    # "feed"     publishes only
    # "etl"      subscribes, so needs credentials
    # "backfill" bounded: registers with discovery, runs a window range, exits
    kind: str
    table: str | None = None  # the table it publishes onto the tickerplant, if any
    schema: str | None = None  # that table's database.q definition
    uses_qpipe: bool = False  # load scripts/torq_pipeline.q ahead of its own script
    offset: int | None = None  # None = allocate from PIPELINE_BLOCK_START in list order
    localtime: str = "1"
    startwithall: str = "1"
    note: str = ""  # why this row deviates from the defaults, if it does

    # --- dataflow edges, for scripts/generate_diagrams.py ---------------
    # Declared here so a diagram can be DERIVED rather than drawn, and
    # verified: verify_pipeline_edges() below greps each pipeline's own .q
    # script for its `.sub.subscribe`/`.qpipe.subscribe_etl`/`.u.upd` calls
    # and fails if the declaration and the code disagree. A hand-drawn
    # diagram goes stale silently; this one cannot (see docs J-04).
    subscribes: tuple[str, ...] = ()  # tickerplant tables it subscribes to
    # Tables it publishes via `.u.upd`. Defaults to (table,) - set it
    # explicitly only when a pipeline publishes onto a table whose schema it
    # does NOT own (fxfeed1 -> the vendored `quote`), or onto more than one.
    publishes: tuple[str, ...] | None = None
    # tap1 chooses its subscription at runtime from -tables, so no fixed
    # edge exists to declare or to verify.
    subscribes_dynamic: bool = False

    @property
    def proctype(self) -> str:
        """The TorQ proctype, which is what discovery indexes processes BY.

        `backfill` is its own type rather than reusing `metrics`, because
        proctype is the lookup key: gethandlebytype on `backfill`
        has to find backfill workers and not the four metrics pipelines that
        happen to share a code path with them.
        """
        if self.kind == "feed":
            return "feed"
        if self.kind == "backfill":
            return "backfill"
        return "metrics"

    @property
    def access_list(self) -> str:
        """A backfill reads from the fleet the way an etl does, so it carries
        the same access list. Only a pure feed, which publishes and subscribes
        to nothing, needs none.
        """
        return "" if self.kind == "feed" else _ETL_ACCESS_LIST

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
        if self.uses_qpipe:
            scripts.insert(0, PIPELINE_LIB_SCRIPT)
        return " ".join(f"${{UQFSCRIPTS}}/{s}" for s in scripts)


# Declared in port order. Reordering this list renumbers ports, so
# test_pipeline_offsets_are_stable pins every derived offset to its current
# value - an accidental reorder fails the suite rather than silently moving a
# running demo's ports.
PIPELINES: tuple[Pipeline, ...] = (
    Pipeline(
        procname="fxfeed1",
        script="torq_fx_feed.q",
        kind="feed",
        publishes=("quote",),
        offset=FXFEED_PINNED_OFFSET,
        note="pinned below the vendored dqc/dqe block, not part of the contiguous run",
    ),
    Pipeline(
        procname="quotesfeed1",
        script="torq_quotes_feed.q",
        kind="feed",
        table="quotes",
        schema=QUOTES_TABLE_SCHEMA,
    ),
    Pipeline(
        procname="cross1",
        script="torq_cross_etl.q",
        kind="etl",
        subscribes=("quotes",),
        note="keeps cross_quotes as private process state, publishes no table",
    ),
    Pipeline(
        procname="widefeed1",
        script="torq_wide_book_feed.q",
        kind="feed",
        table="wide_book",
        schema=WIDE_BOOK_TABLE_SCHEMA,
    ),
    Pipeline(
        procname="vectorize1",
        script="torq_vectorize_etl.q",
        kind="etl",
        subscribes=("wide_book",),
        table="mkt_orderbook",
        schema=MKT_ORDERBOOK_TABLE_SCHEMA,
    ),
    Pipeline(
        procname="tap1",
        script="torq_tap.q",
        kind="etl",
        subscribes_dynamic=True,
        startwithall="0",
        note="diagnostic subscriber - started on demand, not with the whole stack",
    ),
    Pipeline(
        procname="fxtradesfeed1",
        script="torq_fx_trades_feed.q",
        kind="feed",
        table="trades",
        schema=TRADES_TABLE_SCHEMA,
    ),
    Pipeline(
        procname="posbook1",
        script="torq_posbook_etl.q",
        kind="etl",
        subscribes=("trades", "quote"),
        table="position",
        schema=POSITION_TABLE_SCHEMA,
    ),
    Pipeline(
        procname="markout1",
        script="torq_markout_etl.q",
        kind="etl",
        subscribes=("trades", "quote"),
        table="execution_quality",
        schema=EXECUTION_QUALITY_TABLE_SCHEMA,
        uses_qpipe=True,
        localtime="0",
        note=(
            "localtime:0, unlike every other process here - markout1 is the only "
            "process in this demo that compares .proc.cp[] against incoming data "
            "timestamps (its process_ready cutoff calc); every other process just "
            "reacts to each tick immediately, so localtime never mattered for them. "
            ".u.upd stamps trades/quote with the tickerplant's own .z.p (UTC) - with "
            "localtime:1, .proc.cp[] returns local time instead, silently skewing the "
            "cutoff by the local UTC offset (confirmed live: a full hour off on a "
            "UTC+1 machine)"
        ),
    ),
    # --- bounded backfill workers ------------------------------------
    #
    # Declared so that STARTING one wires it to discovery: TorQ registers a
    # declared process at startup, so a running backfill appears in
    # .servers.SERVERS and .qwrt.connected can see it. Previously these were
    # spawned with `system "q ..."` and were invisible to the fleet.
    #
    # startwithall="0" on both: a backfill is a bounded job an operator or
    # Airflow triggers with a range, not part of the streaming stack. Starting
    # the fleet must not kick off a backfill over whatever range the
    # environment happens to carry.
    #
    # One script serves both - which worker and which window come from the
    # environment, so a third worker is an entry here and nothing else.
    Pipeline(
        procname="deals_backfill1",
        script="torq_backfill.q",
        kind="backfill",
        startwithall="0",
        note="bounded: runs a window range and exits, so it must not start with the stack",
    ),
    Pipeline(
        procname="events_backfill1",
        script="torq_backfill.q",
        kind="backfill",
        startwithall="0",
        note="bounded: see deals_backfill1",
    ),
)


def _resolved_offsets() -> dict[str, int]:
    """Each pipeline's `{KDBBASEPORT}+N` offset: explicit where pinned,
    otherwise allocated contiguously from PIPELINE_BLOCK_START in list order.
    """
    offsets: dict[str, int] = {}
    nxt = PIPELINE_BLOCK_START
    for pipeline in PIPELINES:
        if pipeline.offset is not None:
            offsets[pipeline.procname] = pipeline.offset
            continue
        offsets[pipeline.procname] = nxt
        nxt += 1
    return offsets


PIPELINE_OFFSETS = _resolved_offsets()
PIPELINE_BY_NAME = {pipeline.procname: pipeline for pipeline in PIPELINES}


def _pipeline_rows() -> list[dict[str, str]]:
    """One process.csv row per PIPELINES entry, in port order."""
    return [
        {
            "host": "localhost",
            "port": f"{{KDBBASEPORT}}+{PIPELINE_OFFSETS[pipeline.procname]}",
            "proctype": pipeline.proctype,
            "procname": pipeline.procname,
            "U": pipeline.access_list,
            "localtime": pipeline.localtime,
            "g": "0",
            "T": "",
            "w": "",
            "load": pipeline.load_column(),
            "startwithall": pipeline.startwithall,
            "extras": "",
            "qcmd": "q",
        }
        for pipeline in PIPELINES
    ]


# Per-process offset constants, kept as a stable public surface (tests and
# docs reference them by name) but derived from PIPELINES rather than
# hand-maintained.
FXFEED_PORT_OFFSET = PIPELINE_OFFSETS["fxfeed1"]
QUOTES_FEED_PORT_OFFSET = PIPELINE_OFFSETS["quotesfeed1"]
CROSS_ETL_PORT_OFFSET = PIPELINE_OFFSETS["cross1"]
WIDE_BOOK_FEED_PORT_OFFSET = PIPELINE_OFFSETS["widefeed1"]
VECTORIZE_ETL_PORT_OFFSET = PIPELINE_OFFSETS["vectorize1"]
TAP_PORT_OFFSET = PIPELINE_OFFSETS["tap1"]
FX_TRADES_FEED_PORT_OFFSET = PIPELINE_OFFSETS["fxtradesfeed1"]
POSBOOK_PORT_OFFSET = PIPELINE_OFFSETS["posbook1"]
MARKOUT_PORT_OFFSET = PIPELINE_OFFSETS["markout1"]


# Edge verification lives in pipeline_edges.py - see its header for why the
# split is there. Re-exported so `from pipelines import verify_pipeline_edges`
# keeps working for every existing caller.
from torq_orchestrator.pipeline_edges import (  # noqa: E402
    verify_pipeline_edges as _verify_edges,
)


def verify_pipeline_edges(scripts_dir: Path) -> list[str]:
    """Check every pipeline's declared edges against its own q script.

    A thin wrapper that supplies THIS module's registry to the checker in
    `pipeline_edges`, keeping the one-argument signature every existing
    caller uses while the two modules import in only one direction.
    """
    return _verify_edges(scripts_dir, PIPELINES)


PROCESS_CSV_FIELDS = (
    "host",
    "port",
    "proctype",
    "procname",
    "U",
    "localtime",
    "g",
    "T",
    "w",
    "load",
    "startwithall",
    "extras",
    "qcmd",
)
