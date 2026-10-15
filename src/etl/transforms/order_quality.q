/ order_quality.q - the rolling order-outcome statistic, shared by the live job
/ and any backfill of it (#1224, #1396).
/ .
/ order_quality1 (a rolling job) and a backfill that recomputes the series from
/ the HDB both publish order_quality, so both apply this one function, and both
/ cut windows with the rolling kind (src/etl/core/rolling.q,
/ .qetl.job.stream.rolling.compute) and the one set of window lengths here.
/ .
/ IT READS THE ORDER-OUTCOME CONTRACT, NOT AN ORDER TABLE. Its input is the plant
/ table `order_outcomes` (source_time, source, request_id, outcome, book, product,
/ venue), whatever OMS the rows came from; no column, status code or table name
/ of a source appears here. A source is an adapter in src/etl/streaming/order_outcomes.q.
/ .
/ WHAT IS COUNTED. One REQUEST, once, at its latest terminal outcome - not a row
/ and not a status event. Within one (window, as_of) the statistic takes, per
/ request identity (source; request_id), the last terminal row by source_time
/ (ties: arrival order) and counts it if that row is inside the window. So:
/   - a redelivered row, or a request reported again, is one request;
/   - a lifecycle new -> partial -> partial -> filled is one request, filled at the
/     last update, because the first three are `unresolved and never counted;
/   - a corrected outcome (rejected, then filled) is one request, the later one;
/   - a reject followed by a retry is TWO requests, because a retry is a new
/     request_id (the adapter's contract), and that is what a reject rate is.
/ The denominator n_orders is the counted requests: filled, rejected and cancelled.
/ A request that has not ended (`unresolved) and a row the adapter could not read
/ (`unmapped) are in no count: until it ends a request says nothing about the
/ rate. A window with no counted request has 0 counts and null ratios.
/ .
/ The state this needs - the earlier rows of a request - is the rolling job's own
/ buffer: retention is the longest window plus the lateness, restart is the
/ kind's replay. A redelivery keeps its source time, so one older than that is in
/ no window; a correction of a request whose first outcome has been evicted counts
/ as a request of its own.

\d .qpipe.transform.order_quality

/ The window lengths the series is published at.
windows:0D00:01 0D00:05 0D01

/ How often the live job publishes, and so the grid its windows end on; a backfill of the
/ series cuts the same instants.
period:0D00:00:05

/ How long after an outcome's source time it may be received and still count. For
/ the orders feed the source time is the plant's stamp of the order, the normalizer
/ re-stamps it a few milliseconds later, and this is the slack for a stalled hop.
lateness:0D00:00:05

/ The identity of a request: a request_id is unique only within its source.
identity:`source`request_id

/ The columns that say which (window, as_of) a row was offered to.
frame:`window`as_of

/ One row per request per window length: the last terminal row by source_time.
/ @param x outcomes carrying identity, outcome, inside, source_time, window, as_of and the dimensions
/ @param dims the grouping dimensions the request keeps
/ @return the requests, one row each, with their outcome, inside and dimensions
/ @private
requests:{[x;dims]
    t:`source_time xasc select from x where outcome in .qpipe.transform.order_outcome.terminal;
    grp:.qpipe.transform.order_quality.frame,.qpipe.transform.order_quality.identity;
    cs:`outcome`inside,dims;
    0!?[t;();grp!grp;cs!{(last;x)} each cs]}

/ The statistic grouped by a chosen set of dimensions. One row per group per
/ window length: how many requests were counted and how many ended filled and
/ rejected, and their shares. Reads only the rows `inside` the window; the others
/ only keep a group that is quiet in a short window a row, whose identity is 0 for
/ each count and null for each ratio - .qf.exec.fill_ratio and .qf.exec.reject_ratio say
/ no requests is null, not zero.
/ @param dims the grouping dimensions, a non-empty subset of .qpipe.transform.order_outcome.dimensions
/ @param x outcomes carrying source, request_id, source_time, outcome, the dimensions, window, as_of and inside
/ @return one row per group per window length: the dimensions, window, as_of, n_orders, n_filled, n_rejected, fill_ratio, reject_ratio
/ @throws error naming a dimension the contract does not carry
/ @eg .qpipe.transform.order_quality.stats_by[`venue;([] source:enlist `a; request_id:enlist `r1; source_time:enlist 2026.10.09D10:00:30; outcome:enlist `filled; book:enlist `london; product:enlist `spot; venue:enlist `; window:enlist 0D00:01; as_of:enlist 2026.10.09D10:01:00; inside:enlist 1b)]`n_filled  ->  enlist 1
stats_by:{[dims;x]
    dims:(),dims;
    ok:.qpipe.transform.order_outcome.dimensions;
    if[(0=count dims) or count dims except ok;
        '"stats_by: group by a subset of ",(", " sv string ok),", not ",", " sv string dims];
    shape:update window:`timespan$(), as_of:`timestamp$(), n_orders:`long$(), n_filled:`long$(), n_rejected:`long$(),
        fill_ratio:`float$(), reject_ratio:`float$() from 0#dims#x;
    if[0=count x; :shape];
    g:dims,.qpipe.transform.order_quality.frame;
    groups:distinct g#x;
    r:update counted:`long$inside, filled:`long$inside&outcome=`filled, rejected:`long$inside&outcome=`rejected
        from requests[x;dims];
    n:?[r;();g!g;`n_orders`n_filled`n_rejected!((sum;`counted);(sum;`filled);(sum;`rejected))];
    b:groups lj g xkey n;
    b:update n_orders:0^n_orders, n_filled:0^n_filled, n_rejected:0^n_rejected from b;
    b:update fill_ratio:.qf.exec.fill_ratio[n_filled;n_orders], reject_ratio:.qf.exec.reject_ratio[n_rejected;n_orders] from b;
    (cols shape) xcols g xasc b}

/ The published statistic: per book, over the order outcomes of every source.
/ @param x outcomes carrying source, request_id, source_time, outcome, book, window, as_of and inside
/ @return one row per book per window length, in order_quality's columns without time
/ @eg count .qpipe.transform.order_quality.stats[([] source:enlist `a; request_id:enlist `r1; source_time:enlist 2026.10.09D10:00:30; outcome:enlist `filled; book:enlist `london; product:enlist `spot; venue:enlist `; window:enlist 0D00:01; as_of:enlist 2026.10.09D10:01:00; inside:enlist 1b)]  ->  1
stats:{[x] stats_by[enlist `book;x]}

\d .
