/ upstream_trades_backfill.q - moves trades from another kdb+ process into
/ this one (.qupbf).
/ .
/ The first worker in this tree whose source is a kdb+ instance rather than
/ an analogue of a relational system, and therefore the first whose LIVE path
/ runs anywhere: tests/q/run_two_instances.q starts a plain q process on the
/ starter pack's HDB, points this worker at it through
/ UQF_SOURCE_CRED_UPSTREAM_TRADES, and moves a window of trades across.
/ Everything about the lifecycle - windows, coverage, checkpoints, retry,
/ dry-run - is .qbw's; this file is the declaration and the transform.
/ .
/ THE TRANSFORM IS NOT A PASS-THROUGH, and that is the point of the example.
/ The upstream table is shaped the way its owner shaped it: `size` is an
/ int, `side` is `buy or `side, `ex` is a char. Our tables use a long size
/ and the 1/-1 side every function in src/ takes. A source declaration
/ describes what the other side HAS; the transform is where it becomes ours,
/ and .qxf runs the transform's own example on every build so the mapping
/ is a tested claim rather than a comment.

\d .qupbf

worker_name:`upstream_trades_backfill

/ --- the contract's required globals (ETL-01, ETL-02) --------------------
source_version:`;
range_from:0Np;
range_to:0Np;
handle:0Ni;
progress:`windows_completed`windows_failed`rows_published`cursor!(0;0;0;0Np);
last_batch:();

/ --- the contract's required methods, delegated -------------------------
spec:{[] .qbw.spec worker_name}
init:{[run_spec] .qbw.init[worker_name;run_spec]}
plan:{[cursor] .qbw.plan[worker_name;cursor]}
fetch:{[from_ts;to_ts] .qbw.fetch[worker_name;from_ts;to_ts]}
publish:{[batch] .qbw.publish[worker_name;batch]}
checkpoint:{[cursor] .qbw.checkpoint[worker_name;cursor]}
run:{[] .qbw.run worker_name}
cleanup:{[] .qbw.cleanup worker_name}

/ Refuse a batch that cannot be a trade: a non-positive price or size.
/ Shaped correctly and still wrong is what a check is for.
/ @param batch the transformed rows, before publication
/ @return a table check/status/detail, one row per failing condition; empty
/   means the batch passed
/ @eg .qupbf.quality_check[([] time:enlist .z.p; sym:enlist `AAPL; venue:enlist `N; price:enlist 28.73; size:enlist 41; side:enlist 1)]  ->  an empty table
quality_check:{[batch]
    if[0=count batch; :.qbw.no_failures[]];
    bad:select from batch where (not price>0) or not size>0;
    $[0=count bad;
      .qbw.no_failures[];
      ([] check:enlist `positive_price_and_size;
          status:enlist `fail;
          detail:enlist string[count bad]," row(s) with a non-positive price or size")]}

/ What each window materialised, beyond the row count the framework records
/ itself: the span it covered and how many symbols traded. The batch that
/ reaches this hook is the TRANSFORMED one, so the zero-size rows the
/ transform dropped are already gone and cannot be counted here - that
/ number would need the pre-transform batch, which the framework does not
/ hand to facts. Stated so nobody reads "facts" as a drop count.
/ @param batch the transformed rows of one window
/ @return a dict of symbol labels to values, recorded against the window
/ @eg .qupbf.facts[0#.qsup.fixture[]]  ->  (enlist `span)!enlist "empty window"
facts:{[batch]
    if[0=count batch; :(enlist `span)!enlist "empty window"];
    `span`symbols!((string min batch`time),"/",string max batch`time; count distinct batch`sym)}

\d .

/ The upstream's shape in, ours out - and one class of row dropped.
/ .
/ `side` maps `buy -> 1 and everything else -> -1: the upstream's other
/ value is `side, whose meaning its owner never wrote down, and the mapping
/ says so rather than guessing a third state.
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
.qxf.define[`upstream_trades_to_local;`inputs`output`fn`examples!(
    enlist[`batch]!enlist ([] time:`timestamp$(); sym:`symbol$(); ex:`char$();
                              price:`float$(); size:`int$(); side:`symbol$());
    ([] time:`timestamp$(); sym:`symbol$(); venue:`symbol$();
        price:`float$(); size:`long$(); side:`long$());
    / `$string ex, NOT `$'string ex: on an EMPTY batch the each-right form
    / yields a general list rather than an empty symbol vector, and the
    / transform's own empty-input check - which .qxf runs on every build -
    / refused the output as mismatched. `string` over a char column already
    / gives one string per row, so the each-right was doing nothing on real
    / data and the wrong thing on none.
    {[batch]
        select time, sym, venue:`$string ex, price, size:`long$size,
               side:?[side=`buy;1;-1] from batch where size>0};
    enlist `inputs`expected!(
        enlist[`batch]!enlist .qsup.fixture[],
            ([] time:enlist 2015.01.07D00:00:09.000000000; sym:enlist `MSFT; ex:enlist "N";
                price:enlist 20.33; size:enlist 0i; side:enlist `buy);
        select time, sym, venue:`$string ex, price, size:`long$size,
               side:?[side=`buy;1;-1] from .qsup.fixture[]))];

/ One-hour windows: the sample data spans about eleven hours a day, so a
/ day is a dozen windows and a partial run leaves something visible to
/ resume from.
.qbw.define[`upstream_trades_backfill;
    `ns`source`dataset`width`transform`check`facts!
    (`.qupbf;`upstream_trades;`imported_trades;0D01:00:00;`upstream_trades_to_local;
     .qupbf.quality_check;.qupbf.facts)];
