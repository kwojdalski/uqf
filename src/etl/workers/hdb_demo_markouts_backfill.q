/ hdb_demo_markouts_backfill.q - mark out a window of our fills from the HDB (.qpipe.job.hdb_demo_markouts_backfill).
/ .
/ The bounded counterpart of the live markout job: for each window, the
/ hdb_demo_markouts source reads the HDB's fills and quotes and scores them with
/ the live job's own function and horizons; this writes the rows into the
/ SAME `demo_execution_quality` table the live job feeds. The target key is
/ (sym;trade_time;horizon), so writing a window the live job also scored
/ replaces those rows rather than counting the fills twice.
/ .
/ A window is on trade_time - see hdb_demo_markouts.q for why.

\d .qpipe.job.hdb_demo_markouts_backfill

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
    `scored_fills`horizons`unpriced!(count distinct flip `sym`trade_time#batch;
        count distinct batch`horizon; sum "j"$null batch`markout_pips)}

\d .

/ The source already delivers demo_execution_quality's columns; this states that
/ shape, so the contract and the target are checked against each other when
/ the worker is defined.
.qetl.transform.define[`markouts_to_execution_quality;`inputs`output`fn`examples!(
    enlist[`batch]!enlist ([] time:`timestamp$(); sym:`symbol$(); trade_time:`timestamp$();
        horizon:`timespan$(); trade_price:`float$(); ref_price:`float$(); markout_pips:`float$());
    .qetl.plant.shape `demo_execution_quality;
    {[batch] cols[.qetl.plant.shape `demo_execution_quality] xcols batch};
    enlist `inputs`expected!(
        enlist[`batch]!enlist ([] time:enlist 2026.09.17D10:00:01; sym:enlist `EURUSD;
            trade_time:enlist 2026.09.17D10:00:00; horizon:enlist 0D00:00:01;
            trade_price:enlist 1.1001; ref_price:enlist 1.1005; markout_pips:enlist 4f);
        ([] time:enlist 2026.09.17D10:00:01; sym:enlist `EURUSD; trade_time:enlist 2026.09.17D10:00:00;
            horizon:enlist 0D00:00:01; trade_price:enlist 1.1001; ref_price:enlist 1.1005;
            markout_pips:enlist 4f)))];

.qetl.job.bounded.define[`hdb_demo_markouts_backfill;
    `source`dataset`width`transform`check`facts`procname`note`target_key!
    (`hdb_demo_markouts;`demo_execution_quality;0D01:00:00;`markouts_to_execution_quality;
     .qpipe.job.hdb_demo_markouts_backfill.quality_check;.qpipe.job.hdb_demo_markouts_backfill.facts;
     `hdb_demo_markouts_backfill1;
     "bounded: marks out the HDB's fills against its quotes, into demo_execution_quality";
     `sym`trade_time`horizon)];
