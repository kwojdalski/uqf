/ hdb_fx_markouts_backfill.q - mark out a window of executions from the HDB, long layout
/ (.qpipe.job.hdb_fx_markouts_backfill, #1395).
/ .
/ The bounded counterpart of fx_markout: for each window the hdb_fx_markouts source
/ reads the HDB's executions and market_data, this worker's transform scores them with
/ the transform the live job applies (fx_markout_score_long's calculation and layout,
/ src/etl/transforms/fx_markout_outputs.q), and the rows land in the SAME `fx_markout`
/ table the live job feeds, under one key: (venue; fill_id; horizon; kind; step). A
/ window the live job also scored is replaced, not counted twice.
/ .
/ The only differences are the two the live job cannot have: the inputs come from the
/ HDB rather than a queue, and `time` is source_time plus the furthest horizon - the
/ earliest the live row could have been stamped, since all of an execution's horizons
/ are published together once the last is due.
/ .
/ on_conflict `replace (#1281): the live job
/ stamps a row when it RECEIVES it, the twin at source_time + the furthest horizon, so
/ near midnight one key can sit in two date partitions, and only `replace clears across
/ that boundary. window_column is source_time, so `replace clears the rows the window
/ produced and not a neighbour's late ones.

\d .

/ A markout is missing only where no book covered a leg: kept, as the live job's
/ expiry would otherwise hide it. Anything infinite is broken, and a residual outside the
/ spec's tolerance means the allocation and the reference were priced differently. The
/ residual is judged on its pips, not its status: a row with no conversion rate is
/ no_rate whatever its residual is.
/ @param batch the transformed rows
/ @return no failures, or the offending rows' count by reason
/ @eg count .qpipe.job.hdb_fx_markouts_backfill.quality_check[0#.qetl.plant.shape `fx_markout]  ->  0
.qpipe.job.hdb_fx_markouts_backfill.quality_check:{[batch]
    if[0=count batch; :.qetl.job.bounded.no_failures[]];
    inf:sum batch[`pips] in -0w 0w;
    res:select pips from batch where kind=`residual, not null pips;
    unrec:sum (.qpipe.transform.fx_markout_outputs.job_spec`tolerance)<abs res`pips;
    bad:`infinite_pips`unreconciled_residual!(inf;unrec);
    names:where 0<bad;
    pinned:.qpipe.transform.config_markout.check_pinned[batch];
    $[0=count names;
      pinned;
      pinned,([] check:names; status:(count names)#`fail;
          detail:{[n;c] string[c]," row(s) ",$[n=`infinite_pips;"with infinite pips";"whose legs do not add up to the markout"]}'[names;bad names])]}

/ What a run records about its window.
/ @param batch the transformed rows
/ @return executions scored, horizons, and those whose markout could not be priced
/ @eg .qpipe.job.hdb_fx_markouts_backfill.facts[0#.qetl.plant.shape `fx_markout]  ->  (enlist `span)!enlist "empty window"
.qpipe.job.hdb_fx_markouts_backfill.facts:{[batch]
    if[0=count batch; :(enlist `span)!enlist "empty window"];
    `executions`horizons`unpriced`config_id!(count distinct `venue`fill_id#batch;count distinct batch`horizon;
        "j"$sum (batch[`kind]=`total) & batch[`status]=`unavailable;distinct batch`config_id)}

\d .

/ The twin's scoring: the live job's, with `time` stamped as the header says.
/ Worked by hand in fx_markout_outputs.q: a EURUSD buy at 1.1001, ten seconds after
/ which its last row is stamped.
.qetl.transform.define[`hdb_fx_markouts_long;`inputs`output`fn`examples!(
    `executions`market_data!(.qpipe.transform.fx_markout.executions_in;.qpipe.transform.fx_markout.market_data_in);
    .qetl.plant.shape `fx_markout;
    .qpipe.transform.fx_markout_outputs.stamped_with[.qpipe.transform.fx_markout_outputs.job_spec;`long;;];
    enlist `inputs`expected!(
        `executions`market_data!(.qpipe.transform.fx_markout_outputs.sample_executions[];.qpipe.transform.fx_markout_outputs.sample_books[]);
        (cols .qetl.plant.shape `fx_markout)#(([] time:16#2026.09.17D10:00:10),'.qpipe.transform.fx_markout_outputs.sample_long[])))];

.qetl.job.bounded.define[`hdb_fx_markouts_backfill;
    `source`outputs`width`transform`check`facts`procname`note`target_key`window_column`on_conflict`revision`pin`twin_of!
    (`hdb_fx_markouts;enlist `fx_markout;0D01;`hdb_fx_markouts_long;
     .qpipe.job.hdb_fx_markouts_backfill.quality_check;.qpipe.job.hdb_fx_markouts_backfill.facts;
     `hdb_fx_markouts_backfill1;
     "bounded: marks out the HDB's executions against its market_data, into fx_markout, with the calculation fxmarkout1 applies live";
     `venue`fill_id`horizon`kind`step;`source_time;`replace;1;.qpipe.transform.fx_markout_outputs.pin;`fx_markout)];
