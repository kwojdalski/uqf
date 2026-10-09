/ demo_markout.q - the whole of the DEMO markout job (.qpipe.job.demo_markout).
/ .
/ Subscribes to `trades` and `quote`, buffers both, and every second scores
/ the fills old enough to score against the mid at each horizon, publishing
/ `demo_execution_quality`.
/ .
/ DEMO BY NAME (#729). Only the demo's feeds publish `trades` and `quote`,
/ and it scores the invented market's pairs (.qsynth.pairs) - so it was
/ `markout` until that read as the stack's markouts. Real fills are marked
/ out by crypto_markout.q, from crypto_trades against crypto_book.
/ .
/ WHAT IS IN THIS FILE: the schemas, the scoring transform with its examples,
/ and the declaration. The queue, the quote history, the timer and the
/ eviction are the horizon kind's (src/etl/core/horizon.q, #945): this file
/ says which tables, which transform and how long a fill waits.
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.
/ .
/ Every global a transform reads is fully qualified: these functions run
/ inside TorQ processes, where a bare name in a namespaced function is what
/ scripts/processes/torq_pipeline.q's invariant 5 warns does not resolve reliably.
/ .
/ The output schema is the published table in src/etl/plant_tables.q
/ WITHOUT `time`, which .u.upd stamps on receipt (invariant 1).
/ tests/q/test_transform.q holds the two to each other.

\d .qpipe.job.demo_markout

/ ------------------------------------------------------------- THE SHAPES

/ The horizons, and the scoring itself, live in the shared transform
/ (src/etl/transforms/demo_markouts.q, #884): hdb_demo_markouts_backfill
/ re-derives this job's rows and replaces them, so the two must compute
/ them one way. The timer reads max_horizon from there.

trades:.qetl.plant.shape `trades
/ `quotes` is the transform's NAME for its quote input, not the plant table
/ `quotes` (the vector book): its rows are plant `quote` ticks, these four columns.
quotes:.qetl.plant.columns[`quote;`time`sym`bid`ask]
demo_execution_quality:.qetl.plant.published `demo_execution_quality

/ ---------------------------------------------------------- THE TRANSFORM

/ Score each fill at every horizon with the shared transform, published
/ WITHOUT `time`: the plant stamps receipt time (invariant 1). That is the
/ only thing this job adapts - its twin writes trade_time+horizon instead.
/ @param trades fills, as the batch handler buffers them
/ @param quotes quote ticks, as the batch handler mirrors them
/ @return one row per fill per horizon, in fill order then horizon order
score_markouts:{[trades;quotes]
    scored:.qpipe.transform.demo_markouts.score[trades;quotes];
    select sym, trade_time, horizon, trade_price, ref_price, markout_pips from scored}

\d .

.qetl.transform.define[`demo_execution_quality;`inputs`output`fn`examples!(
    `trades`quotes!(.qpipe.job.demo_markout.trades;.qpipe.job.demo_markout.quotes);
    .qpipe.job.demo_markout.demo_execution_quality;
    .qpipe.job.demo_markout.score_markouts;
    / A EURUSD buy that moves 5 then 10 pips in its favour; a USDJPY sell
    / whose single later quote serves both horizons; a GBPUSD buy with no
    / quote at all, which must come out null rather than go missing.
    enlist `inputs`expected!(
        `trades`quotes!(
            ([] time:2026.09.17D10:00:00 2026.09.17D10:00:02 2026.09.17D10:00:03;
                sym:`EURUSD`USDJPY`GBPUSD;
                side:1 -1 1;
                trade_price:1.1 150 1.27;
                size:1e6 2e6 5e5;
                pip_factor:.qccy.pip_factor `EURUSD`USDJPY`GBPUSD);
            ([] time:2026.09.17D09:59:59 2026.09.17D10:00:00.5 2026.09.17D10:00:05 2026.09.17D10:00:00 2026.09.17D10:00:02.5;
                sym:`EURUSD`EURUSD`EURUSD`USDJPY`USDJPY;
                bid:1.0999 1.1004 1.1009 149.99 149.94;
                ask:1.1001 1.1006 1.1011 150.01 149.96));
        ([] sym:`EURUSD`EURUSD`USDJPY`USDJPY`GBPUSD`GBPUSD;
            trade_time:2026.09.17D10:00:00 2026.09.17D10:00:00 2026.09.17D10:00:02 2026.09.17D10:00:02 2026.09.17D10:00:03 2026.09.17D10:00:03;
            horizon:0D00:00:01 0D00:00:10 0D00:00:01 0D00:00:10 0D00:00:01 0D00:00:10;
            trade_price:1.1 1.1 150 150 1.27 1.27;
            ref_price:1.1005 1.101 149.95 149.95 0n 0n;
            markout_pips:5 10 5 5 0n 0n)))];

/ Score every second - frequent enough that demo_execution_quality stays close to
/ real-time in a demo, cheap enough not to matter at this data volume.
/ .
/ No max_age: a fill is priced at the latest quote at or before each horizon
/ however old, so the history keeps each pair's latest quote before the
/ oldest waiting fill, and drops the rest. `keep` filters both tables to the
/ demo's own pairs: `quote` also carries the starter pack's equity quotes.
.qetl.job.stream.at_horizons[`demo_markout;`procname`events`reference`transform`publishes`horizon`period`keep`start_with_all`note!(
    `demo_markout1;
    `trades;
    `quote;
    `demo_execution_quality;
    `demo_execution_quality;
    .qpipe.transform.demo_markouts.max_horizon;
    0D00:00:01.000;
    {[x] x[`sym] in .qsynth.pairs};
    1b;
    "compares its own clock against incoming data timestamps (the process_ready cutoff), and .u.upd stamps those in UTC. It reads .z.p directly for that reason, so it needs no localtime override - it used to carry localtime:0 instead, which fixed the arithmetic by starting one process on a different clock from the other twenty-two")];
