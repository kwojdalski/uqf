/ order_quality.q - the whole of the rolling order-outcome job
/ (.qpipe.job.order_quality, #1224).
/ .
/ Subscribes to `orders` and publishes `order_quality`: per book, over the last
/ minute, five minutes and hour, how many orders there were, how many ended
/ filled and rejected, and the fill and reject ratios, every five seconds.
/ .
/ WHY BOOK AND NOT LP. The question that motivates the kind is a per-liquidity-
/ provider reject rate, but no table in this tree carries one on an order's
/ outcome: `orders` has a book and a product, `executions` has a venue but only
/ for fills (a reject is not a fill), and `order_routing` - the venue per order
/ - has no producer. Grouping by book is the dimension the data has. When the
/ orders feed carries a venue, the change is one word, `by`, here.
/ .
/ WHAT IS IN THIS FILE: the input shape, the transform's contract with its
/ worked example, and the declaration. The windows, the lateness rule, eviction
/ and replay are the rolling kind's (src/etl/core/rolling.q); the statistic is
/ shared with a backfill (src/etl/transforms/order_quality.q).

\d .qpipe.job.order_quality

/ The orders the statistic reads, with the columns the kind adds to them. Named
/ recent_orders, not orders: an input named after a plant table must be that table.
order_rows:update window:`timespan$(), as_of:`timestamp$(), inside:`boolean$() from .qetl.plant.columns[`orders;`time`book`order_status]

/ The example's orders, once per window length as the kind stacks them, and
/ whether each is inside that window: (as_of-1m, as_of] holds the last three.
example_orders:([] time:2026.10.09D10:01:00 2026.10.09D10:04:00 2026.10.09D10:04:30 2026.10.09D10:04:45 2026.10.09D10:05:00 2026.10.09D10:01:30;
    book:`london`london`london`london`london`newyork;
    order_status:`rejected`filled`filled`cancelled`filled`rejected)
example_input:(update window:0D00:01, as_of:2026.10.09D10:05:00, inside:001110b from example_orders),
    update window:0D00:05, as_of:2026.10.09D10:05:00, inside:111111b from example_orders

\d .

.qetl.transform.define[`order_quality;`inputs`output`fn`examples!(
    enlist[`recent_orders]!enlist .qpipe.job.order_quality.order_rows;
    .qetl.plant.published `order_quality;
    .qpipe.transform.order_quality.stats;
    / As of 10:05:00, windows 1m and 5m, as the kind stacks them: every order of
    / the last 5 minutes once per window. london: 10:01:00 rejected (5m only),
    / 10:04:00 filled - EXACTLY as_of-1m, so out of the 1m window - 10:04:30 filled,
    / 10:04:45 cancelled, 10:05:00 filled - AT as_of, so in. 1m holds three
    / orders (two filled): 2%3 filled, no rejects. 5m holds five (three filled,
    / one rejected): 0.6 and 0.2. newyork has one order, a reject at 10:01:30: it
    / is not in the 1m window, so 1m is the identity - 0 orders, null ratios - and
    / 5m is 0 filled, 1 rejected.
    enlist `inputs`expected!(
        enlist[`recent_orders]!enlist .qpipe.job.order_quality.example_input;
        ([] book:`london`london`newyork`newyork; window:0D00:01 0D00:05 0D00:01 0D00:05;
            as_of:4#2026.10.09D10:05:00; n_orders:3 5 0 1; n_filled:2 3 0 0; n_rejected:0 1 0 1;
            fill_ratio:(2%3;0.6;0n;0f); reject_ratio:(0f;0.2;0n;1f))))];

/ Every five seconds, over the last minute, five minutes and hour. The plant stamps an
/ order as it arrives, so none is late and the lateness is only the slack the
/ buffer keeps. The kind carries the buffer across midnight and replays it.
.qetl.job.stream.at_rolling[`order_quality;`procname`events`by`event_time`transform`publishes`windows`lateness`period`start_with_all`note!(
    `order_quality1;
    `orders;
    `book;
    `time;
    `order_quality;
    `order_quality;
    .qpipe.transform.order_quality.windows;
    .qpipe.transform.order_quality.lateness;
    0D00:00:05;
    0b;
    "rolling fill and reject ratios per book over the last 1m, 5m and 1h")];
