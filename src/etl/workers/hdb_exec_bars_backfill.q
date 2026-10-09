/ hdb_exec_bars_backfill.q - rebuild the fill bars from the HDB (.qpipe.job.hdb_exec_bars_backfill).
/ .
/ The bounded counterpart of the live exec_bars job: for each window, the
/ hdb_executions source reads the HDB's fills, this worker's transform cuts
/ them into the live job's windows (.qetl.job.stream.bars.assign) and
/ aggregates them with the live job's own function
/ (src/etl/transforms/exec_bars.q), and the bars land in the SAME `exec_bar`
/ table. The target key is (sym;bar_start), so writing a window the live job
/ also bars replaces those rows.
/ .
/ A window should start and end on a multiple of the bar width: a bar is
/ never split across windows, so one that straddles a boundary is built twice,
/ each time from half its fills, and the later write wins.

\d .qpipe.job.hdb_exec_bars_backfill

/ Bars for a window of fills with the shared aggregation. Kept: `time`, the
/ bar's end, which the live job leaves for the plant to stamp; a backfill has
/ no receipt time.
/ @param fill_rows rows of the HDB's executions: source_time sym size price
/ @return one row per sym per bar, in exec_bar's columns
/ @eg count .qpipe.job.hdb_exec_bars_backfill.bars .qpipe.source.hdb_executions.fixture[]  ->  3
bars:{[fill_rows]
    windowed:.qetl.job.stream.bars.assign[.qpipe.transform.exec_bars.width;`source_time;fill_rows];
    b:.qpipe.transform.exec_bars.bars windowed;
    (cols .qetl.plant.shape `exec_bar) xcols update time:bar_start+.qpipe.transform.exec_bars.width from b}

/ A bar has a positive volume and a high no lower than its low; anything else
/ means a fill was mis-keyed.
/ @param batch the transformed bars
/ @return no failures, or the offending bars
quality_check:{[batch]
    if[0=count batch; :.qetl.job.bounded.no_failures[]];
    bad:select from batch where not (volume>0) and high>=low;
    $[0=count bad;
      .qetl.job.bounded.no_failures[];
      ([] check:enlist `bar_range; status:enlist `fail;
          detail:enlist string[count bad]," bar(s) with no volume or a high below the low")]}

\d .

/ The same bars as the live transform's example, from fills with no bar_start.
.qetl.transform.define[`hdb_exec_bars;`inputs`output`fn`examples!(
    enlist[`executions]!enlist ([] source_time:`timestamp$(); sym:`symbol$(); size:`float$(); price:`float$());
    .qetl.plant.shape `exec_bar;
    .qpipe.job.hdb_exec_bars_backfill.bars;
    enlist `inputs`expected!(
        enlist[`executions]!enlist .qpipe.source.hdb_executions.fixture[];
        ([] time:2026.10.09D10:01:00 2026.10.09D10:02:00 2026.10.09D10:01:00;
            sym:`EURUSD`EURUSD`USDJPY; bar_start:2026.10.09D10:00:00 2026.10.09D10:01:00 2026.10.09D10:00:00;
            open:1.10 1.20 150.0; high:1.12 1.20 150.0; low:1.10 1.20 150.0; close:1.12 1.20 150.0;
            volume:6e6 1e6 5e5; vwap:(6.68%6;1.20;150.0); trades:3 1 1)))];

.qetl.job.bounded.define[`hdb_exec_bars_backfill;
    `source`dataset`width`transform`check`procname`note`target_key!
    (`hdb_executions;`exec_bar;0D01:00:00;`hdb_exec_bars;
     .qpipe.job.hdb_exec_bars_backfill.quality_check;
     `hdb_exec_bars_backfill1;
     "bounded: rebuilds the fill bars from the HDB's executions with the aggregation exec_bars1 applies live";
     `sym`bar_start)];
