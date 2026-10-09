/ hdb_demo_markouts_backfill.q - mark out a window of our fills from the HDB (.qpipe.job.hdb_demo_markouts_backfill).
/ .
/ The bounded counterpart of the live markout job: for each window, the
/ hdb_demo_markouts source reads the HDB's fills and quotes, this worker's
/ transform scores them with the transform the live job applies
/ (src/etl/transforms/demo_markouts.q, #884), and
/ the rows land in the SAME `demo_execution_quality` table the live job
/ feeds. The target key is (sym;trade_time;horizon), so writing a window the
/ live job also scored replaces those rows rather than counting the fills
/ twice.
/ .
/ A window is on trade_time - see hdb_demo_markouts.q for why. The output's own
/ `time` is trade_time+horizon, so window_column says trade_time: `replace then
/ clears the rows the window produced, not a neighbour's late markouts (#972).

\d .qpipe.job.hdb_demo_markouts_backfill

/ Score fills against quotes with the shared transform the live job uses,
/ so a fill scored both ways scores identically - tests/q/test_twins.q
/ holds the two to each other. Kept: `time`, trade_time+horizon, which the
/ live job leaves for the plant to stamp; a backfill has no receipt time.
/ @param fill_rows rows of the HDB's trades: time sym side trade_price pip_factor
/ @param quotes rows of the HDB's quote: time sym bid ask
/ @return one row per fill per horizon, in demo_execution_quality's columns
/ @eg count .qpipe.job.hdb_demo_markouts_backfill.score[.qpipe.source.hdb_demo_markouts.raw_fills[];.qpipe.source.hdb_demo_markouts.raw_quotes[]] -> 8
score:{[fill_rows;quotes] .qpipe.transform.demo_markouts.score[fill_rows;quotes]}

/ A markout is missing only where no quote preceded the horizon - kept, as
/ the live job keeps it, so the gap shows. Anything non-finite is broken.
/ @param batch the transformed rows
/ @return no failures, or the rows whose markout is infinite
quality_check:{[batch]
    if[0=count batch; :.qetl.job.bounded.no_failures[]];
    bad:select from batch where markout_pips in -0w 0w;
    $[0=count bad;
      .qetl.job.bounded.no_failures[];
      ([] check:enlist `finite_markout;
          status:enlist `fail;
          detail:enlist string[count bad]," row(s) with an infinite markout")]}

/ What a run records about its window.
/ @param batch the transformed rows
/ @return fills scored, horizons, and rows with no reference quote
facts:{[batch]
    if[0=count batch; :(enlist `span)!enlist "empty window"];
    `scored_fills`horizons`unpriced!(count distinct `sym`trade_time#batch;
        count distinct batch`horizon; sum "j"$null batch`markout_pips)}

\d .

/ The scoring, as a transform of the source's two inputs (#617): the window's
/ fills and the quotes they are marked against, into demo_execution_quality.
/ The examples are worked by hand, not by the function they check:
/   a buy at 10:00:00 @ 1.1001 - 1s: mid 1.1005 at 10:00:01, +4;
/     10s: the quote at or before 10:00:10 is 10:00:06.5's, mid 1.1007, +6
/   a sell at 10:00:05 @ 1.1003 - 1s: still 10:00:01's mid 1.1005, -2;
/     10s: 10:00:12's mid 1.1011, -8
/ and fills of none score to nothing, however many quotes there are.
.qetl.transform.define[`hdb_demo_markouts_score;`inputs`output`fn`examples!(
    `trades`quote!(
        ([] time:`timestamp$(); sym:`symbol$(); side:`long$(); trade_price:`float$(); pip_factor:`long$());
        ([] time:`timestamp$(); sym:`symbol$(); bid:`float$(); ask:`float$()));
    .qetl.plant.shape `demo_execution_quality;
    {[fill_rows;quotes] .qpipe.job.hdb_demo_markouts_backfill.score[fill_rows;quotes]};
    (`inputs`expected!(
        `trades`quote!(
            ([] time:2026.09.17D10:00:00 2026.09.17D10:00:05; sym:`EURUSD`EURUSD; side:1 -1;
                trade_price:1.1001 1.1003; pip_factor:.qccy.pip_factor `EURUSD`EURUSD);
            ([] time:2026.09.17D09:59:59 2026.09.17D10:00:01 2026.09.17D10:00:06.5 2026.09.17D10:00:12;
                sym:4#`EURUSD; bid:1.1000 1.1004 1.1006 1.1010; ask:1.1002 1.1006 1.1008 1.1012));
        ([] time:2026.09.17D10:00:01 2026.09.17D10:00:10 2026.09.17D10:00:06 2026.09.17D10:00:15;
            sym:4#`EURUSD;
            trade_time:2026.09.17D10:00:00 2026.09.17D10:00:00 2026.09.17D10:00:05 2026.09.17D10:00:05;
            horizon:0D00:00:01 0D00:00:10 0D00:00:01 0D00:00:10;
            trade_price:1.1001 1.1001 1.1003 1.1003;
            ref_price:1.1005 1.1007 1.1005 1.1011;
            markout_pips:4 6 -2 -8f));
     `inputs`expected!(
        `trades`quote!(
            ([] time:`timestamp$(); sym:`symbol$(); side:`long$(); trade_price:`float$(); pip_factor:`long$());
            ([] time:enlist 2026.09.17D10:00:01; sym:enlist `EURUSD; bid:enlist 1.1004; ask:enlist 1.1006));
        .qetl.plant.shape `demo_execution_quality)))];

.qetl.job.bounded.define[`hdb_demo_markouts_backfill;
    `source`dataset`width`transform`check`facts`procname`note`target_key`window_column!
    (`hdb_demo_markouts;`demo_execution_quality;0D01:00:00;`hdb_demo_markouts_score;
     .qpipe.job.hdb_demo_markouts_backfill.quality_check;.qpipe.job.hdb_demo_markouts_backfill.facts;
     `hdb_demo_markouts_backfill1;
     "bounded: marks out the HDB's fills against its quotes, into demo_execution_quality";
     `sym`trade_time`horizon;`trade_time)];
