/ hdb_crypto_markout_backfill.q - mark out a window of crypto executions from the HDB
/ (.qpipe.job.hdb_crypto_markout_backfill, #1936).
/ .
/ The bounded counterpart of crypto_markout: for each window the hdb_crypto_markouts source
/ reads the HDB's executions and market_data, this worker's transform scores them with the
/ calculation the live job applies (crypto_markout_score, src/etl/transforms/
/ crypto_markout_outputs.q), and the rows land in the SAME `crypto_markout` table the live
/ job feeds, under one key: (venue; fill_id; horizon; kind; step). A window the live job also
/ scored is replaced, not counted twice.
/ .
/ The only differences are the two the live job cannot have: the inputs come from the HDB
/ rather than a queue, and `time` is the core's own - source_time plus the furthest horizon,
/ the earliest the live row could have been stamped, since all of a fill's horizons are
/ published together once the last is due.
/ .
/ on_conflict `replace, as hdb_fx_markouts_backfill declares: the live job stamps a row when
/ it RECEIVES it, the twin at source_time + the furthest horizon, so near midnight one key can
/ sit in two date partitions, and only `replace clears across that boundary. window_column is
/ source_time, so `replace clears the rows the window produced and not a neighbour's late ones.

\d .

/ A markout is missing only where no book covered the pair, or the only books were on another
/ clock: kept, as the live job's expiry would otherwise hide it. Anything infinite is broken, and
/ a net figure that is not the gross one less the fee means the two were priced differently.
/ @param batch the transformed rows
/ @return no failures, or the offending rows' count by reason
/ @eg count .qpipe.job.hdb_crypto_markout_backfill.quality_check[0#.qetl.plant.shape `crypto_markout]  ->  0
.qpipe.job.hdb_crypto_markout_backfill.quality_check:{[batch]
    if[0=count batch; :.qetl.job.bounded.no_failures[]];
    inf:sum batch[`markout_bps] in -0w 0w;
    tot:select from batch where kind=`total, not null fee_bps, not null markout_bps;
    off:sum 1e-9<abs (tot`markout_net_bps)-(tot[`markout_bps]-tot`fee_bps);
    bad:`infinite_bps`net_is_not_gross_less_fee!(inf;off);
    names:where 0<bad;
    pinned:.qpipe.transform.config_markout.check_pinned[batch];
    $[0=count names;
      pinned;
      pinned,([] check:names; status:(count names)#`fail;
          detail:{[n;c] string[c]," row(s) ",$[n=`infinite_bps;"with infinite bps";"whose net bps is not the gross bps less the fee"]}'[names;bad names])]}

/ What a run records about its window.
/ @param batch the transformed rows
/ @return executions scored, horizons, those whose markout could not be priced, and those refused for being on another clock than the books
/ @eg .qpipe.job.hdb_crypto_markout_backfill.facts[0#.qetl.plant.shape `crypto_markout]  ->  (enlist `span)!enlist "empty window"
.qpipe.job.hdb_crypto_markout_backfill.facts:{[batch]
    if[0=count batch; :(enlist `span)!enlist "empty window"];
    tot:batch[`kind]=`total;
    `executions`horizons`unpriced`clock_mismatch`config_id!(count distinct `venue`fill_id#batch;count distinct batch`horizon;
        "j"$sum tot & batch[`status]=`unavailable;
        "j"$sum tot & batch[`status]=`clock_mismatch;distinct batch`config_id)}

\d .

/ The twin's scoring: the live job's calculation, with the core's `time` kept (source_time plus the
/ furthest horizon). Worked by hand in crypto_markout_outputs.q: a buy of 0.25 BTC at 62000, ten
/ seconds after which its last row is stamped.
.qetl.transform.define[`hdb_crypto_markout_long;`inputs`output`fn`examples!(
    `executions`market_data!(.qpipe.transform.crypto_markout_outputs.executions_in;.qpipe.transform.crypto_markout_outputs.market_data_in);
    .qetl.plant.shape `crypto_markout;
    .qpipe.transform.crypto_markout_outputs.rows;
    enlist `inputs`expected!(
        `executions`market_data!(.qpipe.transform.crypto_markout_outputs.sample_executions[];.qpipe.transform.crypto_markout_outputs.sample_books[]);
        .qpipe.transform.crypto_markout_outputs.sample_rows[]))];

.qetl.job.bounded.define[`hdb_crypto_markout_backfill;
    `source`outputs`width`transform`check`facts`procname`note`target_key`window_column`on_conflict`pin`twin_of!
    (`hdb_crypto_markouts;enlist `crypto_markout;0D01;`hdb_crypto_markout_long;
     .qpipe.job.hdb_crypto_markout_backfill.quality_check;.qpipe.job.hdb_crypto_markout_backfill.facts;
     `hdb_crypto_markout_backfill1;
     "bounded: marks out the HDB's crypto executions against its market_data, into crypto_markout, with the calculation crypto_markout1 applies live";
     `venue`fill_id`horizon`kind`step;`source_time;`replace;.qpipe.transform.crypto_markout_outputs.pin;`crypto_markout)];
