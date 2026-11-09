/ hdb_eq_markout_backfill.q - mark out a window of equity executions from the HDB
/ (.qpipe.job.hdb_eq_markout_backfill, #1981).
/ .
/ The bounded counterpart of eq_markout: for each window the hdb_eq_markouts source reads the
/ HDB's equity-venue executions and market_data, this worker's transform scores them with the
/ calculation the live job applies (eq_markout_score, src/etl/transforms/eq_markout_outputs.q), and
/ the rows land in the SAME `eq_markout` table the live job feeds, under one key:
/ (venue; fill_id; horizon; kind; step). A window the live job also scored is replaced, not
/ counted twice.
/ .
/ For equities this is also the job that does the work at all while no live fills feed exists:
/ eq_fills_backfill lands a drop's fills in the HDB, and this scores them against the books the
/ Databento bundle landed there.
/ .
/ The only differences are the two the live job cannot have: the inputs come from the HDB rather than
/ a queue, and `time` is the core's own - source_time plus the furthest horizon, the earliest the live
/ row could have been stamped, since all of a fill's horizons are published together.
/ .
/ on_conflict `replace, as hdb_crypto_markout_backfill declares: the live job stamps a row when it
/ RECEIVES it, the twin at source_time + the furthest horizon, so near midnight one key can sit in two
/ date partitions, and only `replace clears across that boundary. window_column is source_time, so
/ `replace clears the rows the window produced and not a neighbour's late ones.

\d .

/ A markout is missing only where no book covered the symbol, the pair crossed the close or a halt, or the
/ only books were on another clock: kept, as the live job's expiry would otherwise hide it. Anything infinite
/ is broken. A batch whose rows name a configuration nobody recorded is refused (#1999).
/ @param batch the transformed rows
/ @return no failures, or the offending rows' count by reason
/ @eg count .qpipe.job.hdb_eq_markout_backfill.quality_check[0#.qetl.plant.shape `eq_markout]  ->  0
.qpipe.job.hdb_eq_markout_backfill.quality_check:{[batch]
    if[0=count batch; :.qetl.job.bounded.no_failures[]];
    inf:sum batch[`markout_bps] in -0w 0w;
    pinned:.qpipe.transform.config_markout.check_pinned[batch];
    $[0=inf;
      pinned;
      pinned,([] check:enlist `infinite_bps; status:enlist `fail; detail:enlist string[inf]," row(s) with infinite bps")]}

/ What a run records about its window.
/ @param batch the transformed rows
/ @return executions scored, horizons, those whose markout could not be priced (no book, or a close or halt in the way), those refused for being on another clock than the books, and the configuration
/ @eg .qpipe.job.hdb_eq_markout_backfill.facts[0#.qetl.plant.shape `eq_markout]  ->  (enlist `span)!enlist "empty window"
.qpipe.job.hdb_eq_markout_backfill.facts:{[batch]
    if[0=count batch; :(enlist `span)!enlist "empty window"];
    tot:batch[`kind]=`total;
    `executions`horizons`unpriced`clock_mismatch`config_id!(count distinct `venue`fill_id#batch;count distinct batch`horizon;
        "j"$sum tot & batch[`status]=`unavailable;
        "j"$sum tot & batch[`status]=`clock_mismatch;distinct batch`config_id)}

\d .

/ The twin's scoring: the live job's calculation, with the core's `time` kept (source_time plus the
/ furthest horizon). Worked by hand in eq_markout_outputs.q: a buy of 100 AAPL at 271.50, a minute
/ after which its last row is stamped.
.qetl.transform.define[`hdb_eq_markout_long;`inputs`output`fn`examples!(
    `executions`market_data!(.qpipe.transform.eq_markout_outputs.executions_in;.qpipe.transform.eq_markout_outputs.market_data_in);
    .qetl.plant.shape `eq_markout;
    .qpipe.transform.eq_markout_outputs.rows;
    enlist `inputs`expected!(
        `executions`market_data!(.qpipe.transform.eq_markout_outputs.sample_executions[];.qpipe.transform.eq_markout_outputs.sample_books[]);
        .qpipe.transform.eq_markout_outputs.sample_rows[]))];

.qetl.job.bounded.define[`hdb_eq_markout_backfill;
    `source`outputs`width`transform`check`facts`procname`note`target_key`window_column`on_conflict`pin`twin_of!
    (`hdb_eq_markouts;enlist `eq_markout;0D01;`hdb_eq_markout_long;
     .qpipe.job.hdb_eq_markout_backfill.quality_check;.qpipe.job.hdb_eq_markout_backfill.facts;
     `hdb_eq_markout_backfill1;
     "bounded: marks out the HDB's equity executions against its market_data, into eq_markout, with the calculation eq_markout1 applies live";
     `venue`fill_id`horizon`kind`step;`source_time;`replace;.qpipe.transform.eq_markout_outputs.pin;`eq_markout)];
