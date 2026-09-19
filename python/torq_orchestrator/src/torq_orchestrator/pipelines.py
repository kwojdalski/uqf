"""The pipeline registry: one Pipeline entry per uqf process.

Adding a pipeline means adding one entry here, not picking a port number and
remembering three other places to edit - the per-process offsets, the
process.csv rows and the generated schema all derive from this tuple. The
Pipeline dataclass itself is in pipeline.py.

Also holds the dataflow-edge declarations and verify_pipeline_edges, which
greps each pipeline's own q script and fails if a declaration disagrees with
the code. A hand-drawn diagram goes stale silently; a derived one cannot."""

from __future__ import annotations

from pathlib import Path

from torq_orchestrator.logger import get_logger
from torq_orchestrator.pipeline import (  # noqa: F401 - re-exported: core.py imports them from here
    PIPELINE_LIB_SCRIPT,
    STREAM_RUNNER_SCRIPT,
    Pipeline,
)
from torq_orchestrator.schemas import (
    DATABENTO_BOOK_TABLE_SCHEMA,
    EXECUTION_QUALITY_TABLE_SCHEMA,
    EXECUTIONS_TABLE_SCHEMA,
    MARKS_TABLE_SCHEMA,
    MKT_ORDERBOOK_TABLE_SCHEMA,
    ORDERS_TABLE_SCHEMA,
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

# Declared in port order. Reordering this list renumbers ports, so
# test_pipeline_offsets_are_stable pins every derived offset to its current
# value - an accidental reorder fails the suite rather than silently moving a
# running demo's ports.
PIPELINES: tuple[Pipeline, ...] = (
    Pipeline(
        procname="fxfeed1",
        loads_qpipe=True,
        script=STREAM_RUNNER_SCRIPT,
        kind="feed",
        publishes=("quote",),
        offset=FXFEED_PINNED_OFFSET,
        note="pinned below the vendored dqc/dqe block, not part of the contiguous run",
    ),
    Pipeline(
        procname="quotesfeed1",
        loads_qpipe=True,
        script=STREAM_RUNNER_SCRIPT,
        kind="feed",
        table="quotes",
        schema=QUOTES_TABLE_SCHEMA,
    ),
    Pipeline(
        procname="cross1",
        script=STREAM_RUNNER_SCRIPT,
        loads_qpipe=True,
        kind="etl",
        subscribes=("quotes",),
        startwithall="0",
        note=(
            "keeps cross_quotes as private process state, publishes no table - so "
            "it is a leaf, and nothing downstream stalls while it is stopped. "
            "startwithall:0 to stay inside PLANT_CONNECTION_BUDGET (#285); "
            "quotesfeed1 runs by default, so `uqf-stack start cross1` is enough"
        ),
    ),
    Pipeline(
        procname="widefeed1",
        loads_qpipe=True,
        script=STREAM_RUNNER_SCRIPT,
        kind="feed",
        table="wide_book",
        schema=WIDE_BOOK_TABLE_SCHEMA,
        startwithall="0",
        note=(
            "half of a closed pair with vectorize1: it is the only producer of "
            "wide_book and vectorize1 the only consumer, so the two start and stop "
            "together and no other job notices. startwithall:0 to stay inside "
            "PLANT_CONNECTION_BUDGET (#285) - `uqf-stack start widefeed1 vectorize1`"
        ),
    ),
    Pipeline(
        procname="vectorize1",
        script=STREAM_RUNNER_SCRIPT,
        loads_qpipe=True,
        kind="etl",
        subscribes=("wide_book",),
        table="mkt_orderbook",
        schema=MKT_ORDERBOOK_TABLE_SCHEMA,
        startwithall="0",
        note=(
            "the other half of the widefeed1 pair: nothing subscribes to "
            "mkt_orderbook, so this branch of the graph is self-contained. See "
            "widefeed1"
        ),
    ),
    Pipeline(
        procname="tap1",
        script="processes/torq_tap.q",
        kind="etl",
        subscribes_dynamic=True,
        startwithall="0",
        note="diagnostic subscriber - started on demand, not with the whole stack",
    ),
    Pipeline(
        procname="fxtradesfeed1",
        loads_qpipe=True,
        script=STREAM_RUNNER_SCRIPT,
        kind="feed",
        table="trades",
        schema=TRADES_TABLE_SCHEMA,
    ),
    Pipeline(
        procname="posbook1",
        script=STREAM_RUNNER_SCRIPT,
        loads_qpipe=True,
        kind="etl",
        subscribes=("executions", "marks"),
        table="position",
        schema=POSITION_TABLE_SCHEMA,
        note=(
            "reads the two normalizers' outputs, not trades and quote, so one "
            "book carries FX and crypto and a new market is a mapping, not a job"
        ),
    ),
    Pipeline(
        procname="markout1",
        script=STREAM_RUNNER_SCRIPT,
        kind="etl",
        subscribes=("trades", "quote"),
        table="execution_quality",
        schema=EXECUTION_QUALITY_TABLE_SCHEMA,
        loads_qpipe=True,
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
        script="processes/torq_backfill.q",
        kind="backfill",
        worker="demo_deals_backfill",
        startwithall="0",
        note="bounded: runs a window range and exits, so it must not start with the stack",
    ),
    Pipeline(
        procname="events_backfill1",
        script="processes/torq_backfill.q",
        kind="backfill",
        worker="demo_events_backfill",
        startwithall="0",
        note="bounded: see deals_backfill1",
    ),
    # --- appended, never inserted --------------------------------------
    #
    # Offsets are allocated in list order, so a new process goes at the END:
    # inserting above would renumber tap1 and both backfills onto other
    # processes' ports.
    Pipeline(
        procname="databento1",
        script=STREAM_RUNNER_SCRIPT,
        loads_qpipe=True,
        kind="etl",
        subscribes=("databento_mbp10",),
        table="databento_book",
        schema=DATABENTO_BOOK_TABLE_SCHEMA,
        startwithall="0",
        note=(
            "folds live Databento MBP-10 into the book shape. The raw rows are "
            "published by an EXTERNAL Python feed handler (databento_feed.py) - a "
            "q process cannot hold a Databento subscription - so databento_mbp10 "
            "has a schema row but no producer in this list. That is also why "
            "startwithall:0: on a default start nothing publishes the table it "
            "subscribes to, so it held one of the sixteen licensed plant "
            "connections (#285) to consume nothing. Start it with the feed handler"
        ),
    ),
    Pipeline(
        procname="cryptomock1",
        script=STREAM_RUNNER_SCRIPT,
        loads_qpipe=True,
        kind="feed",
        publishes=("crypto_book", "crypto_trades"),
        startwithall="0",
        note=(
            "stands in for cryptorust's two kdb recorders. startwithall:0: start it "
            "INSTEAD of them, never as well as - it publishes onto the same two "
            "tables, and an invented ladder or fill must not interleave with a real one"
        ),
    ),
    Pipeline(
        procname="executions1",
        script=STREAM_RUNNER_SCRIPT,
        loads_qpipe=True,
        kind="normalizer",
        subscribes=("trades", "crypto_trades"),
        table="executions",
        schema=EXECUTIONS_TABLE_SCHEMA,
        note="every fill table as one: trades and crypto_trades -> executions",
    ),
    Pipeline(
        procname="marks1",
        script=STREAM_RUNNER_SCRIPT,
        loads_qpipe=True,
        kind="normalizer",
        subscribes=("quote", "crypto_book"),
        table="marks",
        schema=MARKS_TABLE_SCHEMA,
        note="a mid per instrument from every book: quote and crypto_book -> marks",
    ),
    Pipeline(
        procname="fxordersfeed1",
        script=STREAM_RUNNER_SCRIPT,
        loads_qpipe=True,
        kind="feed",
        table="orders",
        schema=ORDERS_TABLE_SCHEMA,
        note="synthetic order flow, most of which never becomes a fill - fxpositions1's input",
    ),
    Pipeline(
        procname="fxpositions1",
        script=STREAM_RUNNER_SCRIPT,
        loads_qpipe=True,
        kind="etl",
        subscribes=("orders",),
        publishes=("fx_position", "fx_limit_breach"),
        note=(
            "net exposure by (sym, book, product) with limit breaches. Runs here "
            "AND standalone under processes/run_stream.q on stock kdb+ - a job is "
            "TorQ-free code and the runner decides the transport, so being "
            "runnable without TorQ is no reason not to be startable with it"
        ),
    ),
    Pipeline(
        procname="databento_backfill1",
        script="processes/torq_backfill.q",
        kind="backfill",
        worker="databento_book_backfill",
        startwithall="0",
        note=(
            "bounded: reads Databento MBP-10 over ODBC and folds it with the same "
            "transform databento1 applies live"
        ),
    ),
    Pipeline(
        procname="upstream_backfill1",
        script="processes/torq_backfill.q",
        kind="backfill",
        worker="upstream_trades_backfill",
        startwithall="0",
        note="bounded: reads an upstream q process over IPC",
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
