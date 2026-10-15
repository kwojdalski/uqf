/ hdb_fx_markout_wide_backfill.q - mark out a window of executions from the HDB, wide layout
/ (.qpipe.job.hdb_fx_markout_wide_backfill, #1395).
/ .
/ hdb_fx_markouts_backfill's twin for fx_markout_wide: the same source, the same
/ calculation, the other layout - a row per (execution, horizon) in `fx_markout_wide`,
/ under (venue; fill_id; horizon). See hdb_fx_markouts_backfill.q for what a refill
/ shares with the live job and the one thing it stamps differently (`time`).

\d .

/ Anything infinite is broken, and a wide row whose legs do not add up to its markout
/ means the allocation and the reference were priced differently.
/ @param batch the transformed rows
/ @return no failures, or the offending rows' count by reason
/ @eg count .qpipe.job.hdb_fx_markout_wide_backfill.quality_check[0#.qetl.plant.shape `fx_markout_wide]  ->  0
.qpipe.job.hdb_fx_markout_wide_backfill.quality_check:{[batch]
    if[0=count batch; :.qetl.job.bounded.no_failures[]];
    inf:sum (batch[`total_pips] in -0w 0w) | (batch[`execution_pips] in -0w 0w) | batch[`residual_pips] in -0w 0w;
    unrec:sum batch[`status]=`unreconciled;
    bad:`infinite_pips`unreconciled_residual!(inf;unrec);
    names:where 0<bad;
    $[0=count names;
      .qetl.job.bounded.no_failures[];
      ([] check:names; status:(count names)#`fail;
          detail:{[n;c] string[c]," row(s) ",$[n=`infinite_pips;"with infinite pips";"whose legs do not add up to the markout"]}'[names;bad names])]}

/ What a run records about its window.
/ @param batch the transformed rows
/ @return executions scored, horizons, and rows whose markout could not be priced
/ @eg .qpipe.job.hdb_fx_markout_wide_backfill.facts[0#.qetl.plant.shape `fx_markout_wide]  ->  (enlist `span)!enlist "empty window"
.qpipe.job.hdb_fx_markout_wide_backfill.facts:{[batch]
    if[0=count batch; :(enlist `span)!enlist "empty window"];
    `executions`horizons`unpriced!(count distinct `venue`fill_id#batch;count distinct batch`horizon;
        "j"$sum batch[`status]=`unavailable)}

\d .

/ The twin's scoring: the live wide job's, with `time` stamped as hdb_fx_markouts_backfill does.
.qetl.transform.define[`hdb_fx_markouts_wide;`inputs`output`fn`examples!(
    `executions`market_data!(.qpipe.transform.fx_markout.executions_in;.qpipe.transform.fx_markout.market_data_in);
    .qetl.plant.shape `fx_markout_wide;
    {[exe;md] .qpipe.transform.fx_markout_outputs.stamped[.qpipe.transform.fx_markout_outputs.job_spec;
        .qpipe.transform.fx_markout_outputs.score_with[.qpipe.transform.fx_markout_outputs.job_spec;`wide;exe;md]]};
    enlist `inputs`expected!(
        `executions`market_data!(.qpipe.transform.fx_markout_outputs.sample_executions[];.qpipe.transform.fx_markout_outputs.sample_books[]);
        (([] time:4#2026.09.17D10:00:10),'.qpipe.transform.fx_markout_outputs.sample_wide[])))];

.qetl.job.bounded.define[`hdb_fx_markout_wide_backfill;
    `source`dataset`width`transform`check`facts`procname`note`target_key`window_column`on_conflict!
    (`hdb_fx_markouts;`fx_markout_wide;0D01;`hdb_fx_markouts_wide;
     .qpipe.job.hdb_fx_markout_wide_backfill.quality_check;.qpipe.job.hdb_fx_markout_wide_backfill.facts;
     `hdb_fx_markout_wide_backfill1;
     "bounded: marks out the HDB's executions against its market_data, into fx_markout_wide, with the calculation fxmarkoutwide1 applies live";
     `venue`fill_id`horizon;`source_time;`replace)];
