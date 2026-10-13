/ order_quality.q - the rolling order-outcome statistic, shared by the live job
/ and any backfill of it (#1224).
/ .
/ order_quality1 (a rolling job) and a backfill that recomputes the series from
/ the HDB's orders both publish order_quality, so both apply this one
/ function, and both cut windows with the rolling kind (src/etl/core/rolling.q,
/ .qetl.job.stream.rolling.compute) and the one set of window lengths here.

\d .qpipe.transform.order_quality

/ The window lengths the series is published at.
windows:0D00:01 0D00:05 0D01

/ How long after an order's time it may be received and still count. The plant
/ stamps an order's `time` as it arrives, so no order is late (the kind's header)
/ and this is the bound a source with its own event time would be held to.
lateness:0D00:00:05

/ One row per book per window length: how many orders were in the window and how
/ many ended filled and rejected, and their shares. Reads only the rows `inside`
/ the window; the others only keep a book that is quiet in a short window a row.
/ Identity for a book with no order in the window: 0 for each count, null for each
/ ratio - .qexec.fill_ratio and .qexec.reject_ratio say no requests is null, not
/ zero. Count mode, as .qexec.hit_ratio_by and .qexec.reject_ratio_by with `count.
/ @param x orders carrying book, order_status, window, as_of and inside
/ @return one row per book per window length, in order_quality's columns without time
/ @eg .qpipe.transform.order_quality.stats[([] book:`london`london; order_status:`filled`rejected; window:2#0D00:01; as_of:2#2026.10.09D10:01:00; inside:11b)]`n_orders  ->  enlist 2
stats:{[x]
    shape:.qetl.plant.published `order_quality;
    if[0=count x; :shape];
    / longs, not booleans: sum of a boolean is an int, which the plant's long column refuses
    x:update counted:`long$inside, filled:`long$inside&order_status=`filled, rejected:`long$inside&order_status=`rejected from x;
    b:0!select n_orders:sum counted, n_filled:sum filled, n_rejected:sum rejected by book, window, as_of from x;
    b:update fill_ratio:.qexec.fill_ratio[n_filled;n_orders], reject_ratio:.qexec.reject_ratio[n_rejected;n_orders] from b;
    (cols shape) xcols b}

\d .
