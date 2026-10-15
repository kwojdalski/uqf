/ hdb_order_quality_backfill.q - recompute the rolling order-quality series from the HDB
/ (.qpipe.job.hdb_order_quality_backfill, #1396).
/ .
/ The bounded twin of the live order_quality job. For each window the hdb_orders
/ source reads the HDB's `orders` and the orders in the longest window before it,
/ and this worker's transform runs them through
/ .  the adapter the live pair applies (order_outcomes_from_orders, through the
/    normalizer shell's own `normalize`), then
/ .  the rolling kind's own cut (.qetl.job.stream.rolling.series) with the live
/    job's one statistic (src/etl/transforms/order_quality.q),
/ at every grid point of the window, and the rows land in the SAME `order_quality`
/ table. The target key is (book; window; as_of), so writing a window the live job
/ also published replaces those rows rather than counting them twice.
/ .
/ WHAT IT SHARES, AND SO CANNOT DRIFT FROM. The adapter, the statistic, the window
/ lengths, the lateness (not needed: an HDB order has no receipt time distinct from its
/ stamp), the half-open (as_of-N, as_of] cut and the period grid. tests/q/
/ test_hdb_order_quality_backfill.q runs the live job and this worker over one hour
/ of orders and requires the same series.
/ .
/ A WINDOW IS ONE ALIGNED HOUR (align = width). The grid is derived from the rows - the
/ window's start is the hour the first order falls in - because a bounded transform is
/ not told its window; an unaligned run is refused, so the derivation holds. Grid points
/ are [start, start + width) every period, as the live job's are.
/ .
/ WHERE IT DIFFERS FROM THE LIVE JOB, on purpose.
/ .  A window with NO order in it publishes nothing (a bounded worker's rule: an empty
/    primary input runs no transform), where the live job keeps publishing identity rows for
/    a book that was active in the previous hour. The series is therefore complete only for
/    hours with at least one order.
/ .  No window length is withheld for want of coverage: the source reads the whole
/    lookback, so a 1h window at 00:05 holds the previous day's last 55 minutes,
/    which a live job restarted at midnight withholds.
/ .  `time` is as_of, where the live job's is the plant's stamp a moment later.

\d .

/ The orders as order outcomes: the live normalizer's adapter, unchanged.
/ @param rows orders, with the columns hdb_orders hands over
/ @return canonical order_outcomes rows
.qpipe.job.hdb_order_quality_backfill.outcomes_of:{[rows]
    .qetl.job.stream.normalizer.normalize[`order_outcomes;`orders;rows]}

/ The start of the window the rows belong to: the hour the first order falls in.
/ @param rows the window's orders
/ @return a timestamp
/ @eg .qpipe.job.hdb_order_quality_backfill.window_start[([] time:enlist 2026.10.09D10:20:00)]  ->  2026.10.09D10:00:00.000000000
.qpipe.job.hdb_order_quality_backfill.window_start:{[rows] `timestamp$.qpipe.job.hdb_order_quality_backfill.width xbar min rows`time}

/ The grid points a window of the live job publishes at: every period from its start.
/ @param ws the window's start
/ @return timestamps
/ @eg count .qpipe.job.hdb_order_quality_backfill.grid[2026.10.09D10:00:00]  ->  720
.qpipe.job.hdb_order_quality_backfill.grid:{[ws] ws+.qpipe.job.hdb_order_quality_backfill.period*til `long$.qpipe.job.hdb_order_quality_backfill.width%.qpipe.job.hdb_order_quality_backfill.period}

/ How long a window is: one aligned hour. The period is the live job's.
.qpipe.job.hdb_order_quality_backfill.width:0D01
.qpipe.job.hdb_order_quality_backfill.period:.qpipe.transform.order_quality.period

/ The series for one window: the live job's statistic at every grid point of it,
/ from the window's orders and the orders before it.
/ @param orders the window's orders
/ @param before the orders before the window (context; any at or after its start are ignored)
/ @return rows in order_quality's columns, `time` the grid point
.qpipe.job.hdb_order_quality_backfill.series:{[orders;before]
    ws:.qpipe.job.hdb_order_quality_backfill.window_start[orders];
    rows:(select from before where time<ws),orders;
    o:.qpipe.job.hdb_order_quality_backfill.outcomes_of[rows];
    out:.qetl.job.stream.rolling.series[`order_quality;o;.qpipe.job.hdb_order_quality_backfill.grid[ws]];
    (cols .qetl.plant.shape `order_quality) xcols update time:as_of from out}

/ The ratios are shares, the counts are consistent, and a book has a row only
/ if the live statistic could have made it.
/ @param batch the transformed rows
/ @return no failures, or the offending rows
.qpipe.job.hdb_order_quality_backfill.quality_check:{[batch]
    if[0=count batch; :.qetl.job.bounded.no_failures[]];
    ok_counts:(batch[`n_filled]+batch`n_rejected)<=batch`n_orders;
    ok_fill:null[batch`fill_ratio] | batch[`fill_ratio] within 0 1f;
    ok_rej:null[batch`reject_ratio] | batch[`reject_ratio] within 0 1f;
    bad:batch where not ok_counts&ok_fill&ok_rej;
    $[0=count bad;
      .qetl.job.bounded.no_failures[];
      ([] check:enlist `consistent_counts; status:enlist `fail;
          detail:enlist string[count bad]," row(s) whose counts or ratios cannot come from the statistic")]}

/ What a run records about its window.
/ @param batch the transformed rows
/ @return the grid points published, books, and the share of rows with no order in the window
.qpipe.job.hdb_order_quality_backfill.facts:{[batch]
    if[0=count batch; :(enlist `span)!enlist "empty window"];
    `as_ofs`books`quiet_rows!(count distinct batch`as_of;count distinct batch`book;sum batch[`n_orders]=0)}

/ The hand-worked example's orders. A window of the 10:00 hour with one order -
/ london, filled at 10:00:00 - and, before it, newyork rejected at 09:58:00.
.qpipe.job.hdb_order_quality_backfill.example_orders:([] time:enlist 2026.10.09D10:00:00; order_id:enlist 2; book:enlist `london; product:enlist `spot; order_status:enlist `filled)
.qpipe.job.hdb_order_quality_backfill.example_before:([] time:enlist 2026.10.09D09:58:00; order_id:enlist 1; book:enlist `newyork; product:enlist `spot; order_status:enlist `rejected)

/ The series at one grid point, worked from the definition and not from the function:
/ an order is in the window ending at a when its time is after a-window (and not
/ after a), and a book is published while it has an order in the last hour.
/ london's fill is in the 1m window for a<10:01, the 5m for a<10:05 and the 1h
/ always; newyork's reject (09:58) is out of every 1m window, in the 5m for a<10:03
/ and in the 1h for a<10:58, after which newyork has no row. london then newyork,
/ 1m, 5m, 1h; a ratio over no order is null.
/ @param ws the window's start, the instant the orders are placed relative to
/ @param a the grid point
/ @return the rows of that point
.qpipe.job.hdb_order_quality_backfill.example_at:{[ws;a]
    wins:0D00:01 0D00:05 0D01;
    ln:`long$ws>a-wins;
    yn:`long$(ws-0D00:02)>a-wins;
    r:([] time:6#a; book:(3#`london),3#`newyork; window:6#wins; as_of:6#a;
        n_orders:ln,yn; n_filled:ln,3#0; n_rejected:(3#0),yn);
    r:update fill_ratio:n_filled%n_orders, reject_ratio:n_rejected%n_orders from r;
    $[a<ws+0D00:58; r; 3#r]}

/ The whole hour of it: one grid point every period.
/ @return the expected series of the example
.qpipe.job.hdb_order_quality_backfill.expected_rows:{[]
    ws:2026.10.09D10:00:00;
    raze .qpipe.job.hdb_order_quality_backfill.example_at[ws;] each ws+0D00:00:05*til 720}

\d .

/ The worker: window = one aligned hour; the key replaces what the live job published.
.qetl.transform.define[`hdb_order_quality;`inputs`output`fn`examples!(
    `orders`orders_before!(2#enlist .qetl.plant.columns[`orders;.qpipe.source.hdb_orders.columns]);
    .qetl.plant.shape `order_quality;
    {[orders;before] .qpipe.job.hdb_order_quality_backfill.series[orders;before]};
    enlist `inputs`expected!(
        `orders`orders_before!(.qpipe.job.hdb_order_quality_backfill.example_orders;.qpipe.job.hdb_order_quality_backfill.example_before);
        .qpipe.job.hdb_order_quality_backfill.expected_rows[]))];

.qetl.job.bounded.define[`hdb_order_quality_backfill;
    `source`dataset`width`transform`check`facts`procname`note`target_key`window_column`align`on_conflict!
    (`hdb_orders;`order_quality;.qpipe.job.hdb_order_quality_backfill.width;`hdb_order_quality;
     .qpipe.job.hdb_order_quality_backfill.quality_check;.qpipe.job.hdb_order_quality_backfill.facts;
     `hdb_order_quality_backfill1;
     "bounded: recomputes the rolling order-quality series from the HDB's orders with the adapter and statistic order_quality1 applies live";
     `book`window`as_of;`as_of;.qpipe.job.hdb_order_quality_backfill.width;`replace)];
