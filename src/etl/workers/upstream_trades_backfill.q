/ upstream_trades_backfill.q - moves trades from another kdb+ process into
/ this one (.qpipe.job.upstream_trades_backfill).
/ .
/ The first worker in this tree whose source is a kdb+ instance rather than
/ an analogue of a relational system, and therefore the first whose LIVE path
/ runs anywhere: tests/q/run_two_instances.q starts a plain q process on the
/ starter pack's HDB, points this worker at it through
/ UQF_SOURCE_CRED_UPSTREAM_TRADES, and moves a window of trades across.
/ Everything about the lifecycle - windows, coverage, checkpoints, retry,
/ dry-run - is .qetl.job.bounded's; this file is the declaration and the transform.
/ .
/ THE TRANSFORM IS NOT A PASS-THROUGH, and that is the point of the example.
/ The upstream table is shaped the way its owner shaped it: `size` is an
/ int, `side` is `buy or `side, `ex` is a char. Our tables use a long size
/ and the 1/-1 side every function in src/ takes. A source declaration
/ describes what the other side HAS; the transform is where it becomes ours,
/ and .qetl.transform runs the transform's own example on every build so the mapping
/ is a tested claim rather than a comment.

\d .

/ The upstream's side vocabulary, as the 1/-1 every function in src/ takes.
/ `side is the upstream's own second value (see the transform's note); a
/ side outside this map maps to a null, which quality_check refuses rather
/ than letting it import as a sell (#2573).
.qpipe.job.upstream_trades_backfill.side_sign:`buy`sell`side!1 -1 -1

/ Refuse a batch that cannot be a trade: a non-positive price or size, or a
/ side the transform could not map. Shaped correctly and still wrong is what
/ a check is for.
/ @param batch the transformed rows, before publication
/ @return a table check/status/detail, one row per failing condition; empty
/   means the batch passed
/ @eg .qpipe.job.upstream_trades_backfill.quality_check[([] time:enlist .z.p; sym:enlist `AAPL; venue:enlist `N; price:enlist 28.73; size:enlist 41; side:enlist 1)]  ->  an empty table
.qpipe.job.upstream_trades_backfill.quality_check:{[batch]
    if[0=count batch; :.qetl.job.bounded.no_failures[]];
    n_ps:sum (not batch[`price]>0) or not batch[`size]>0;
    n_side:sum null batch`side;
    out:.qetl.job.bounded.no_failures[];
    if[n_ps; out,:([] check:enlist `positive_price_and_size; status:enlist `fail;
        detail:enlist string[n_ps]," row(s) with a non-positive price or size")];
    if[n_side; out,:([] check:enlist `known_side; status:enlist `fail;
        detail:enlist string[n_side]," row(s) with a side that is not buy, sell or side")];
    out}

/ What each window materialised, beyond the row count the framework records
/ itself: the span it covered and how many symbols traded. The batch that
/ reaches this hook is the TRANSFORMED one, so the zero-size rows the
/ transform dropped are already gone and cannot be counted here - that
/ number would need the pre-transform batch, which the framework does not
/ hand to facts. Stated so nobody reads "facts" as a drop count.
/ @param batch the transformed rows of one window
/ @return a dict of symbol labels to values, recorded against the window
/ @eg .qpipe.job.upstream_trades_backfill.facts[0#.qpipe.source.upstream_trades.fixture[]]  ->  (enlist `span)!enlist "empty window"
.qpipe.job.upstream_trades_backfill.facts:{[batch]
    if[0=count batch; :(enlist `span)!enlist "empty window"];
    `span`symbols!((string min batch`time),"/",string max batch`time; count distinct batch`sym)}

\d .

/ The upstream's shape in, ours out - and one class of row dropped.
/ .
/ `side` maps through side_sign: `buy -> 1, `sell and the upstream's other
/ value `side -> -1 (`side's meaning its owner never wrote down, and the
/ mapping says so rather than guessing a third state). Anything else - a
/ null, `SELL - maps to a null side that quality_check refuses, rather than
/ importing as a sell (#2573).
/ .
/ ZERO-SIZE ROWS ARE DROPPED HERE, deliberately and visibly. About 1% of
/ the starter pack's trades carry size=0 - it is synthetic data drawing
/ sizes uniformly from 0 to 98, and zero is simply in range. A trade of
/ nothing is not a trade, and the quality check below refuses a batch that
/ contains one; found the first time this worker ran live, when every window
/ failed that check. The choice was between loosening the check and dropping
/ the rows, and the check stays strict: it exists to catch a source that
/ starts emitting nonsense, and a rule that tolerated zero would not. So the
/ transform - which is where the upstream's data becomes ours - is where
/ they go, and the example below carries a zero-size row so that the drop
/ is a tested claim.
.qetl.transform.define[`upstream_trades_to_local;`inputs`output`fn`examples!(
    enlist[`batch]!enlist ([] time:`timestamp$(); sym:`symbol$(); ex:`char$();
                              price:`float$(); size:`int$(); side:`symbol$());
    ([] time:`timestamp$(); sym:`symbol$(); venue:`symbol$();
        price:`float$(); size:`long$(); side:`long$());
    / `$string ex, NOT `$'string ex: on an EMPTY batch the each-right form
    / yields a general list rather than an empty symbol vector, and the
    / transform's own empty-input check - which .qetl.transform runs on every build -
    / refused the output as mismatched. `string` over a char column already
    / gives one string per row, so the each-right was doing nothing on real
    / data and the wrong thing on none.
    / sg a local, not the global by name: inside qSQL KDB-X resolves a bare
    / global at the ROOT, where no side_sign exists.
    {[batch]
        sg:.qpipe.job.upstream_trades_backfill.side_sign;
        select time, sym, venue:`$string ex, price, size:`long$size,
               side:sg side from batch where size>0};
    enlist `inputs`expected!(
        enlist[`batch]!enlist .qpipe.source.upstream_trades.fixture[],
            ([] time:enlist 2015.01.07D00:00:09.000000000; sym:enlist `MSFT; ex:enlist "N";
                price:enlist 20.33; size:enlist 0i; side:enlist `buy);
        select time, sym, venue:`$string ex, price, size:`long$size,
               side:(`buy`side!1 -1) side from .qpipe.source.upstream_trades.fixture[]))];

/ One-hour windows: the sample data spans about eleven hours a day, so a
/ day is a dozen windows and a partial run leaves something visible to
/ resume from.
.qetl.job.bounded.define[`upstream_trades_backfill;
    `source`outputs`width`transform`check`facts`procname`note`target_key!
    (`upstream_trades;enlist `imported_trades;0D01:00:00;`upstream_trades_to_local;
     .qpipe.job.upstream_trades_backfill.quality_check;.qpipe.job.upstream_trades_backfill.facts;
     `upstream_backfill1;
     "bounded: reads an upstream q process over IPC";
     / The source's row_key, with ex under the name the transform gives it.
     `time`sym`venue`price`size`side)];
