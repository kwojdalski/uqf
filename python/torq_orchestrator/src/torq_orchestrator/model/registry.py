"""The registry literal: one Pipeline entry per uqf process, in port order.

Split out of model/pipelines.py when it crossed the module-size threshold
test_module_split.py holds it to. This file is DATA - the entries and the
two port anchors they are allocated from. model/pipelines.py is what derives from
it: the resolved offsets, the process.csv rows, the per-process offset
constants. Data and derivation change for different reasons, and the data
grows with every process while the derivation does not.

Adding a pipeline means adding one entry here. Nothing else: the offsets,
the process.csv rows and the generated database.q all fall out of it.
"""

from __future__ import annotations

from torq_orchestrator.model.pipeline import (
    FROM_DECLARATION,
    STREAM_RUNNER_SCRIPT,
    Pipeline,
    PipelineKind,
)

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
        kind=PipelineKind.FEED,
        publishes=FROM_DECLARATION,
        offset=FXFEED_PINNED_OFFSET,
        note="pinned below the vendored dqc/dqe block, not part of the contiguous run",
    ),
    Pipeline(
        procname="quotesfeed1",
        loads_qpipe=True,
        script=STREAM_RUNNER_SCRIPT,
        kind=PipelineKind.FEED,
        table="quotes",
    ),
    Pipeline(
        procname="cross1",
        script=STREAM_RUNNER_SCRIPT,
        loads_qpipe=True,
        kind=PipelineKind.ETL,
        subscribes=FROM_DECLARATION,
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
        kind=PipelineKind.FEED,
        table="wide_book",
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
        kind=PipelineKind.ETL,
        subscribes=FROM_DECLARATION,
        table="mkt_orderbook",
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
        kind=PipelineKind.ETL,
        subscribes_dynamic=True,
        startwithall="0",
        note="diagnostic subscriber - started on demand, not with the whole stack",
    ),
    Pipeline(
        procname="fxtradesfeed1",
        loads_qpipe=True,
        script=STREAM_RUNNER_SCRIPT,
        kind=PipelineKind.FEED,
        table="trades",
    ),
    Pipeline(
        procname="posbook1",
        script=STREAM_RUNNER_SCRIPT,
        loads_qpipe=True,
        kind=PipelineKind.ETL,
        subscribes=FROM_DECLARATION,
        table="position",
        note=(
            "reads the two normalizers' outputs, not trades and quote, so one "
            "book carries FX and crypto and a new market is a mapping, not a job"
        ),
    ),
    Pipeline(
        procname="markout1",
        script=STREAM_RUNNER_SCRIPT,
        kind=PipelineKind.ETL,
        subscribes=FROM_DECLARATION,
        table="execution_quality",
        loads_qpipe=True,
        note=(
            "compares its own clock against incoming data timestamps (the "
            "process_ready cutoff), and .u.upd stamps those in UTC. It reads .z.p "
            "directly for that reason, so it needs no localtime override - it used "
            "to carry localtime:0 instead, which fixed the arithmetic by starting "
            "one process on a different clock from the other twenty-two"
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
        kind=PipelineKind.BACKFILL,
        worker="demo_deals_backfill",
        startwithall="0",
        note="bounded: runs a window range and exits, so it must not start with the stack",
    ),
    Pipeline(
        procname="events_backfill1",
        script="processes/torq_backfill.q",
        kind=PipelineKind.BACKFILL,
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
        kind=PipelineKind.ETL,
        subscribes=FROM_DECLARATION,
        table="databento_book",
        startwithall="0",
        note=(
            "folds live Databento MBP-10 into the book shape. The raw rows are "
            "published by an EXTERNAL Python feed handler (external/databento_feed.py) - a "
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
        kind=PipelineKind.FEED,
        publishes=FROM_DECLARATION,
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
        kind=PipelineKind.NORMALIZER,
        subscribes=FROM_DECLARATION,
        table="executions",
        note="every fill table as one: trades and crypto_trades -> executions",
    ),
    Pipeline(
        procname="marks1",
        script=STREAM_RUNNER_SCRIPT,
        loads_qpipe=True,
        kind=PipelineKind.NORMALIZER,
        subscribes=FROM_DECLARATION,
        table="marks",
        note="a mid per instrument from every book: quote and crypto_book -> marks",
    ),
    Pipeline(
        procname="fxordersfeed1",
        script=STREAM_RUNNER_SCRIPT,
        loads_qpipe=True,
        kind=PipelineKind.FEED,
        table="orders",
        note="synthetic order flow, most of which never becomes a fill - fxpositions1's input",
    ),
    Pipeline(
        procname="fxpositions1",
        script=STREAM_RUNNER_SCRIPT,
        loads_qpipe=True,
        kind=PipelineKind.ETL,
        subscribes=FROM_DECLARATION,
        publishes=FROM_DECLARATION,
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
        kind=PipelineKind.BACKFILL,
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
        kind=PipelineKind.BACKFILL,
        worker="upstream_trades_backfill",
        startwithall="0",
        note="bounded: reads an upstream q process over IPC",
    ),
    Pipeline(
        procname="marketdata1",
        script=STREAM_RUNNER_SCRIPT,
        loads_qpipe=True,
        kind=PipelineKind.NORMALIZER,
        subscribes=FROM_DECLARATION,
        table="market_data",
        startwithall="0",
        note=(
            "direct FX snapshots with source identity and original receipt time. "
            "Head of a closed three-process chain - market_data is read only by "
            "superbook1, superbook only by arbitrage1, and arbitrage by nothing - "
            "so the whole chain is on demand together and no default-start job "
            "notices. startwithall:0 because the licence allows a q process "
            "sixteen inbound connections and the default start is at thirteen "
            "(#285): `uqf-stack start marketdata1 superbook1 arbitrage1` spends "
            "the three spare slots, which is what they are for"
        ),
    ),
    Pipeline(
        procname="superbook1",
        script=STREAM_RUNNER_SCRIPT,
        loads_qpipe=True,
        kind=PipelineKind.ETL,
        subscribes=FROM_DECLARATION,
        table="superbook",
        publishes=FROM_DECLARATION,
        startwithall="0",
        note=(
            "latest source books merged by pair; stale liquidity expires on a "
            "timer. Middle of the marketdata1 chain - see there"
        ),
    ),
    Pipeline(
        procname="arbitrage1",
        script=STREAM_RUNNER_SCRIPT,
        loads_qpipe=True,
        kind=PipelineKind.ETL,
        subscribes=FROM_DECLARATION,
        table="arbitrage",
        startwithall="0",
        note=(
            "gross direct cross-source opportunities, including inactive clearing "
            "rows. Tail of the marketdata1 chain - see there"
        ),
    ),
    Pipeline(
        procname="crossarb1",
        script=STREAM_RUNNER_SCRIPT,
        loads_qpipe=True,
        kind=PipelineKind.ETL,
        subscribes=FROM_DECLARATION,
        table="cross_arbitrage",
        publishes=FROM_DECLARATION,
        startwithall="0",
        note=(
            "the direct book against a synthetic route through other pairs "
            "(EURJPY against EURUSD x USDJPY), where arbitrage1 compares two "
            "sources on the SAME pair. Reads superbook like arbitrage1, so it "
            "is the second consumer of the marketdata1 chain rather than a "
            "fifth link - see there. startwithall:0 for that chain's reason "
            "(#285), and note that the chain plus this one is four plant "
            "connections against three spare: stop something first"
        ),
    ),
)
