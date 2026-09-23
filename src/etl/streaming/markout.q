/ markout.q - the whole of the markout job (.qsub.markout).
/ .
/ Subscribes to `trades` and `quote`, buffers both, and every second scores
/ the fills old enough to score against the mid at each horizon, publishing
/ `execution_quality`.
/ .
/ WHAT IS IN THIS FILE: the schemas, the scoring transform with its examples,
/ the batch handler, the timer body, the job's own buffers, and the
/ declaration the runner reads. Every step of the job, in the order it runs.
/ It was two files - the computation in src/etl/transforms/stream.q, the
/ subscription and timer in the old scripts/torq_markout_etl.q (deleted in
/ #204) - because the
/ computation had to be testable and the wiring had to connect. The publish
/ seam (.qstream.wire) makes both true of one file.
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.
/ .
/ Every global a transform reads is fully qualified: these functions run
/ inside TorQ processes, where a bare name in a namespaced function is what
/ scripts/processes/torq_pipeline.q's invariant 5 warns does not resolve reliably.
/ .
/ The output schema is the published table in scripts/processes/uqf_stack_tables.q
/ WITHOUT `time`, which .u.upd stamps on receipt (invariant 1).
/ tests/q/test_transform.q holds the two to each other.

\d .qsub.markout

/ ------------------------------------------------------------- THE SHAPES

/ The horizons each fill is scored at. Here rather than in the runner,
/ because they decide what the transform outputs; the timer body reads
/ max_horizon to decide when a fill is old enough to score.
horizons:0D00:00:01 0D00:00:10
max_horizon:max horizons

trades:([] time:`timestamp$(); sym:`symbol$(); side:`long$(); trade_price:`float$(); size:`float$(); pip_factor:`long$())
quotes:([] time:`timestamp$(); sym:`symbol$(); bid:`float$(); ask:`float$())
execution_quality:([] sym:`symbol$(); trade_time:`timestamp$(); horizon:`timespan$(); trade_price:`float$(); ref_price:`float$(); markout_pips:`float$())

/ ---------------------------------------------------------- THE TRANSFORM

/ Score each fill's post-trade markout at every horizon, against the mid of
/ the latest quote at or before trade_time+horizon.
/ .
/ A fill whose horizon quote never arrived gets a null ref_price and
/ markout_pips rather than being dropped, so a gap shows in
/ execution_quality instead of vanishing.
/ .
/ The empty guard is not tidiness: .qexec.markout_at_horizons throws `type`
/ on zero trades. The job never reached it because its timer returned early
/ on an empty buffer - the transform's empty-input check found it.
/ @param trades fills, as the batch handler buffers them
/ @param quotes quote ticks, as the batch handler mirrors them
/ @return one row per fill per horizon, in fill order then horizon order
score_markouts:{[trades;quotes]
    if[0=count trades; :.qsub.markout.execution_quality];
    mids:select sym, time, mid:(bid+ask)%2 from quotes;
    scored:.qexec.markout_at_horizons[trades;mids;.qsub.markout.horizons];
    select sym, trade_time, horizon, trade_price, ref_price, markout_pips from scored}

/ --------------------------------------------------------------- THE JOB

/ Where rows go. A stub until .qstream.wire points it at the tickerplant
/ (the runner) or at a recorder (a test).
publish:.qstream.unwired `markout;

/ STATE, two blocks. pending is a queue: every trade not yet old enough to
/ score, drained by on_timer as it scores them. quote_hist is a mirror:
/ every FX quote tick seen so far, with the mid derived at score time rather
/ than stored. quote_hist has no eviction and grows for as long as the job
/ runs - the same proof-of-concept tradeoff the cross job documents for its
/ own mirror.
/ .
/ Both are the transform's declared input tables, so the buffers cannot
/ drift from what the transform reads.
pending:trades;
quote_hist:quotes;

/ Buffer one incoming batch into the matching state block, filtered to the
/ demo's own FX pairs (.qsynth.pairs): `quote` also carries the vendored
/ starter pack's equity quotes, which this job has no business scoring.
/ .
/ Scoring happens on the timer rather than here: a fill cannot be scored
/ until the quotes at its horizons have arrived.
/ @param tbl the table the batch arrived on
/ @param batch the rows, as a table
/ @return nothing - this handler publishes nothing itself
on_batch:{[tbl;batch]
    $[tbl=`trades;
        `.qsub.markout.pending insert select time, sym, side, trade_price, size, pip_factor from batch where sym in .qsynth.pairs;
      tbl=`quote;
        `.qsub.markout.quote_hist insert select time, sym, bid, ask from batch where sym in .qsynth.pairs;
      ()];
    }

/ Score every trade old enough that a quote at its furthest horizon
/ (trade_time+max_horizon) should already have arrived, publish the result,
/ then evict those trades so the buffer does not grow unbounded.
/ .
/ `mask` is computed once and used for both the read and the evict:
/ recomputing the cutoff in the evict step would drop any trade that arrived
/ in between, unscored. Evict AFTER publishing, not before, so a failed
/ publish leaves the batch buffered for the next tick instead of losing it -
/ the runner's safe timer swallows the error, so a drain-first ordering
/ would lose the batch silently. That ordering is why this reads then
/ evicts rather than calling .qstream.drain.
/ .
/ `now` is an argument so the whole job can be driven in a test. The runner
/ passes the process clock.
/ @param now the instant to score as of
/ @return nothing
score_ready:{[now]
    if[0=count .qsub.markout.pending; :()];
    mask:.qsub.markout.pending[`time]<=now-.qsub.markout.max_horizon;
    ready:.qsub.markout.pending where mask;
    if[0=count ready; :()];
    out:.qxf.apply[`execution_quality;`trades`quotes!(ready;.qsub.markout.quote_hist)];
    .qsub.markout.publish[`execution_quality;out];
    .qstream.evict[`.qsub.markout.pending;mask];
    }

/ The timer body the runner installs. Reads the clock once and hands it to
/ score_ready, which is the testable half.
on_timer:{[] .qsub.markout.score_ready .qsub.markout.now[]}

/ The clock, as a function so a test can replace it.
/ .
/ .z.p, NOT .proc.cp[]. Both read "now", and only one of them agrees with the
/ data: `.u.upd` stamps every row with the tickerplant's own .z.p, so the
/ times this is compared against are UTC. `.proc.cp[]` is `.z.P` - LOCAL time -
/ whenever TorQ was started with -localtime, which the vendored process.csv
/ does for all 23 of its processes.
/ .
/ The comparison below is therefore off by the machine's UTC offset. It was
/ found live as markout scoring trades an hour before their horizon had
/ elapsed on a UTC+1 machine, and patched then by starting that ONE process
/ with localtime=0 - which fixed the arithmetic and left every other process
/ reading a different clock, including this one.
/ .
/ Reading .z.p here is what superbook.q and cross_arbitrage.q already do, and
/ it holds whatever flag the process was started with.
now:{[] .z.p}

\d .

.qxf.define[`execution_quality;`inputs`output`fn`examples!(
    `trades`quotes!(.qsub.markout.trades;.qsub.markout.quotes);
    .qsub.markout.execution_quality;
    .qsub.markout.score_markouts;
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
                pip_factor:10000 100 10000);
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

/ Score every second - frequent enough that execution_quality stays close to
/ real-time in a demo, cheap enough not to matter at this data volume.
.qstream.register[`markout;`procname`subscribes`publishes`on_batch`timer_period`on_timer`autostart`note!(
    `markout1;
    `trades`quote;
    enlist `execution_quality;
    .qsub.markout.on_batch;
    0D00:00:01.000;
    .qsub.markout.on_timer;
    1b;
    "compares its own clock against incoming data timestamps (the process_ready cutoff), and .u.upd stamps those in UTC. It reads .z.p directly for that reason, so it needs no localtime override - it used to carry localtime:0 instead, which fixed the arithmetic by starting one process on a different clock from the other twenty-two")];
