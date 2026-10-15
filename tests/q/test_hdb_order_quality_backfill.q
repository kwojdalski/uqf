/ test_hdb_order_quality_backfill.q - the bounded twin of order_quality1 (.hdb_order_qualitybftest, #1396).
/ .
/ The point of a twin is that it re-derives what the live job publishes, so the main
/ test runs BOTH over one scenario - the live job tick by tick through the real adapter,
/ the worker over an aligned hour through the source's fixture - and requires the same
/ series, row for row. The scenario has what real order flow has: orders in bursts and a
/ quiet stretch, every status, an order delivered twice and a status the adapter does
/ not know. The worker's declaration is checked, and so is where it differs on purpose.

\d .hdb_order_qualitybftest

t0:2026.10.09D10:00:00
clock:t0
sent:()
saved:()
scn:()
orig:()

spec_for:{[version;from_ts;to_ts] `source_version`range_from`range_to!(version;from_ts;to_ts)}

/ --------------------------------------------------------------- THE SCENARIO

/ 09:00 to 11:00: an order every 17 seconds, apart from a quiet stretch 10:20-10:40;
/ two books, a cycle of statuses with one the feed never emits, and every 25th order
/ delivered a second time.
scenario:{[]
    n:420;
    o:([] time:2026.10.09D09:00:00+0D00:00:17*til n; order_id:1+til n; book:n#`london`newyork`london;
        product:n#`spot`fwd;
        order_status:n#`filled`filled`cancelled`rejected`filled`rejected`pending`filled);
    o:select from o where not time within (2026.10.09D10:20:00;2026.10.09D10:40:00);
    `time xasc o,select from o where 0=order_id mod 25}

/ ------------------------------------------------------------------ THE LIVE JOB

/ One order into the live job, through the real adapter, at its own time.
feed_one:{[r]
    `.hdb_order_qualitybftest.clock set first r`time;
    .qpipe.job.order_quality.on_batch[`order_outcomes;
        update time:source_time from .qetl.job.stream.normalizer.normalize[`order_outcomes;`orders;r]]}

/ The orders after lo and up to a, fed, and then the grid point a evaluated.
tick_to:{[o;lo;a]
    rows:select from o where time>lo, time<=a;
    {[rows;j] feed_one rows enlist j}[rows] each til count rows;
    `.hdb_order_qualitybftest.clock set a;
    .qpipe.job.order_quality.evaluate a}

/ The live job over the orders, ticked on its own grid from from_ts to to_ts, the orders
/ before from_ts already in its buffer, and every row it published inside [from_ts; to_ts). The job's clock and its
/ zone are moved and put back.
live:{[o;from_ts;to_ts]
    p:.qpipe.transform.order_quality.period;
    `.hdb_order_qualitybftest.saved set (.qpipe.job.order_quality.now;.qetl.job.stream.rolling.zone_offset);
    .qetl.job.stream.reset `order_quality;
    `.qpipe.job.order_quality.now set {[] .hdb_order_qualitybftest.clock};
    `.qetl.job.stream.rolling.zone_offset set {[] 0D};
    `.hdb_order_qualitybftest.sent set ();
    .qetl.job.stream.wire[`order_quality;{[t;x] .hdb_order_qualitybftest.sent,:enlist x; count x}];
    pts:from_ts+p*til `long$(to_ts-from_ts)%p;
    {[o;pts;i] tick_to[o;$[i=0;-0Wp;pts i-1];pts i]}[o;pts] each til count pts;
    out:raze sent;
    `.qpipe.job.order_quality.now set saved 0;
    `.qetl.job.stream.rolling.zone_offset set saved 1;
    .qetl.job.stream.reset `order_quality;
    select from out where as_of>=from_ts, as_of<to_ts}

/ ------------------------------------------------------------------- THE TWIN

fixture_fn:{[] `orders`orders_before!(.hdb_order_qualitybftest.scn;.hdb_order_qualitybftest.scn)}

setUp_fresh:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    .testutil.reset_coverage_ledger[];
    .qetl.cfg.reset[];
    .qetl.cfg.set_layers[(enlist `on_conflict)!enlist "replace";()!();()!()];
    setenv[`UQF_DRY_RUN;""];
    setenv[`UQF_SOURCE_CRED_HDB_ORDERS;""];
    .qetl.job.bounded.state.release_lock `hdb_order_quality_backfill;
    .qetl.job.bounded.state.clear_checkpoint `hdb_order_quality_backfill;
    `order_quality set 0#.qetl.plant.shape `order_quality;
    `.hdb_order_qualitybftest.scn set scenario[];
    `.hdb_order_qualitybftest.orig set (.qetl.source.def `hdb_orders)`fixture;
    .testutil.set_field[`.qetl.source.sources;`hdb_orders;`fixture;.hdb_order_qualitybftest.fixture_fn];
    }

tearDown_release:{[]
    .testutil.set_field[`.qetl.source.sources;`hdb_orders;`fixture;.hdb_order_qualitybftest.orig];
    .qpipe.job.hdb_order_quality_backfill.cleanup[];
    }

/ The worker over [from_ts; to_ts) of the scenario, and what it wrote.
twin:{[from_ts;to_ts]
    .qpipe.job.hdb_order_quality_backfill.init[spec_for[`v1;from_ts;to_ts]];
    r:.qpipe.job.hdb_order_quality_backfill.run[];
    (r;select from (get `order_quality) where as_of>=from_ts, as_of<to_ts)}

/ The series, comparable: the same columns in the same order, without the receipt `time`.
norm:{[t] `as_of`book`window xasc select as_of, book, window, n_orders, n_filled, n_rejected, fill_ratio, reject_ratio from t}

/ ---------------------------------------------------------------- DECLARATION

test_the_twin_is_declared_as_the_live_jobs_table_on_an_aligned_hour:{[t]
    d:.qetl.job.bounded.def `hdb_order_quality_backfill;
    .qunit.assertEquals[(d`dataset;d`width;d`align;d`window_column);(`order_quality;0D01;0D01;`as_of);"the live table, one aligned hour, cleared on as_of"];
    .qunit.assertEquals[d`target_key;`book`window`as_of;"the live key"];
    .qunit.assertEquals[first .qetl.uptime.twins `order_quality;`hdb_order_quality_backfill;"uqs job gaps names it"];
    .qunit.assertEquals[.qpipe.job.hdb_order_quality_backfill.period;.qpipe.transform.order_quality.period;"the live job's grid"]};

test_an_unaligned_range_is_refused_because_the_window_is_derived_from_the_hour:{[t]
    .qunit.assertThrows[{.qpipe.job.hdb_order_quality_backfill.init x};spec_for[`v1;t0+0D00:00:30;t0+0D01];
        "*multiples of*";"a run not on the hour is refused"]};

test_the_source_reaches_back_the_longest_window:{[t]
    .qunit.assertEquals[.qpipe.source.hdb_orders.reach[];last .qpipe.transform.order_quality.windows;"the buffer the first grid point needs"]};

/ ------------------------------------------------------------------ EQUIVALENCE

test_the_twin_gives_the_live_series_over_an_hour_row_for_row:{[t]
    from_ts:t0;
    to_ts:t0+0D01;
    lv:norm live[scn;from_ts;to_ts];
    tw:twin[from_ts;to_ts];
    .qunit.assertEquals[(tw 0)`state;`completed;"the worker ran"];
    .qunit.assertTrue[(count lv)>2000;"a real series: an hour of grid points, both books, three windows"];
    .qunit.assertEquals[count norm tw 1;count lv;"as many rows"];
    .qunit.assertEquals[norm tw 1;lv;"the same series, row for row, to the last ratio"];
    / the first grid point: the 10:00 hour window holds the 09:00-10:00 orders, from the source's lookback
    f:{select from x where as_of=2026.10.09D10:00:00, window=0D01};
    .qunit.assertTrue[0<first (f lv)`n_orders;"the first publication has a buffer behind it"];
    .qunit.assertEquals[f norm tw 1;f lv;"and the twin read the same one"]};

test_rerunning_a_window_replaces_its_rows_rather_than_duplicating_them:{[t]
    a:twin[t0;t0+0D01];
    n:count get `order_quality;
    b:twin[t0;t0+0D01];
    .qunit.assertEquals[count get `order_quality;n;"same count after a second run"];
    .qunit.assertEquals[count distinct `book`window`as_of#get `order_quality;n;"and the key is unique"]};

/ --------------------------------------------------------- WHERE IT DIFFERS

test_an_hour_with_no_orders_publishes_nothing_by_a_bounded_workers_rule:{[t]
    r:twin[t0+0D05;t0+0D06];
    .qunit.assertEquals[count r 1;0;"no orders in the window, no run of the transform"]};

test_the_quality_check_refuses_counts_the_statistic_cannot_make:{[t]
    bad:([] n_orders:enlist 1; n_filled:enlist 2; n_rejected:enlist 0; fill_ratio:enlist 2f; reject_ratio:enlist 0f);
    .qunit.assertEquals[first (.qpipe.job.hdb_order_quality_backfill.quality_check bad)`check;`consistent_counts;"filled more than ordered"];
    ok:([] n_orders:enlist 0; n_filled:enlist 0; n_rejected:enlist 0; fill_ratio:enlist 0n; reject_ratio:enlist 0n);
    .qunit.assertEquals[count .qpipe.job.hdb_order_quality_backfill.quality_check ok;0;"a quiet group with null ratios is fine"]};

/ ------------------------------------------------ THE WORKED EXAMPLE'S DEFINITION

test_the_examples_series_is_worked_from_the_definition_at_its_edges:{[t]
    ws:2026.10.09D10:00:00;
    a:.qpipe.job.hdb_order_quality_backfill.example_at[ws;ws+0D00:00:30];
    .qunit.assertEquals[a`n_orders;1 1 1 0 1 1;"london's fill is in all three; newyork's reject (09:58) is in the 5m and 1h only"];
    .qunit.assertEquals[a`n_rejected;0 0 0 0 1 1;"and is the only reject"];
    b:.qpipe.job.hdb_order_quality_backfill.example_at[ws;ws+0D00:01];
    .qunit.assertEquals[(b`n_orders) 0 1 2;0 1 1;"london's fill is exactly 1m old at 10:01:00: out of the 1m window"] ;
    c:.qpipe.job.hdb_order_quality_backfill.example_at[ws;ws+0D00:58];
    .qunit.assertEquals[distinct c`book;enlist `london;"newyork has no order in the last hour from 10:58:00: no rows"];
    e:.qpipe.job.hdb_order_quality_backfill.expected_rows[];
    .qunit.assertEquals[count distinct e`as_of;720;"one grid point every 5s of the hour"];
    .qunit.assertTrue[null first exec fill_ratio from e where book=`newyork, window=0D00:01, as_of=ws;"a ratio over no order is null"]};

\d .
