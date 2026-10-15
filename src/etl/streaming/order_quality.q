/ order_quality.q - the whole of the rolling order-outcome job
/ (.qpipe.job.order_quality, #1224, #1396).
/ .
/ Subscribes to `order_outcomes` and publishes `order_quality`: per book, over the
/ last minute, five minutes and hour, how many requests there were, how many ended
/ filled and rejected, and the fill and reject ratios, every five seconds.
/ .
/ IT READS THE ORDER-OUTCOME CONTRACT, NOT AN ORDER TABLE (#1396). The outcomes
/ come from order_outcomes1 (src/etl/streaming/order_outcomes.q), which maps each
/ order source's table, columns and status codes onto one shape; this job and its
/ statistic know none of them. A second order-management system is another
/ mapping there, not an edit here. The event time is the outcome's `source_time`,
/ the source's own stamp; `time` is the plant's receipt and cuts no window.
/ .
/ WHY BOOK AND NOT LP. The question that motivates the kind is a per-liquidity-
/ provider reject rate, but the orders feed carries no venue on an order's outcome
/ (`executions` has one only for fills, and `order_routing` has no producer), and a
/ venue is never inferred. The statistic can group by venue (stats_by) and the
/ contract carries the column, null until a source supplies it; publishing that
/ grouping is a table of its own (the output here has a `book` column).
/ .
/ WHAT IS IN THIS FILE: the input shape, the transform's contract with its
/ worked example, and the declaration. The windows, the lateness rule, eviction
/ and replay are the rolling kind's (src/etl/core/rolling.q); the statistic and what
/ it counts are shared with a backfill (src/etl/transforms/order_quality.q).

\d .

/ The outcomes the statistic reads, with the columns the kind adds to them. Named
/ recent_outcomes, not order_outcomes: an input named after a plant table must be that table.
.qpipe.job.order_quality.outcome_rows:update window:`timespan$(), as_of:`timestamp$(), inside:`boolean$() from .qetl.plant.columns[`order_outcomes;`source_time`source`request_id`outcome`book]

/ The example's outcomes: london's five requests and newyork's one, with the ways a
/ real feed reports them - r3 delivered twice, r4 pending at 10:04:40 and cancelled
/ at 10:04:45 - none of which makes a request twice or an open one counted.
.qpipe.job.order_quality.example_events:([] source_time:2026.10.09D10:01:00 2026.10.09D10:04:00 2026.10.09D10:04:30 2026.10.09D10:04:30 2026.10.09D10:04:40 2026.10.09D10:04:45 2026.10.09D10:05:00 2026.10.09D10:01:30;
    source:8#`feed_a;
    request_id:`r1`r2`r3`r3`r4`r4`r5`r6;
    outcome:`rejected`filled`filled`filled`unresolved`cancelled`filled`rejected;
    book:`london`london`london`london`london`london`london`newyork)

/ The example's outcomes, once per window length as the kind stacks them, and
/ whether each is inside that window: (as_of-1m, as_of] holds the last five rows.
.qpipe.job.order_quality.example_input:raze {update inside:source_time>as_of-window from update window:x, as_of:2026.10.09D10:05:00 from .qpipe.job.order_quality.example_events} each 0D00:01 0D00:05

\d .

.qetl.transform.define[`order_quality;`inputs`output`fn`examples!(
    enlist[`recent_outcomes]!enlist .qpipe.job.order_quality.outcome_rows;
    .qetl.plant.published `order_quality;
    .qpipe.transform.order_quality.stats;
    / As of 10:05:00, windows 1m and 5m, as the kind stacks them: every outcome of
    / the last 5 minutes once per window. london: r1 rejected at 10:01:00 (5m only),
    / r2 filled at 10:04:00 - EXACTLY as_of-1m, so out of the 1m window - r3 filled at
    / 10:04:30 and again, the same row delivered twice, r4 pending at 10:04:40 and
    / cancelled at 10:04:45, r5 filled at 10:05:00 - AT as_of, so in. 1m holds three
    / requests (r3, r4, r5), two filled: 2%3 filled, no rejects. 5m holds five (r1-r5),
    / three filled and one rejected: 0.6 and 0.2. newyork has one request, r6 rejected
    / at 10:01:30: it is not in the 1m window, so 1m is the identity - 0 requests,
    / null ratios - and 5m is 0 filled, 1 rejected.
    enlist `inputs`expected!(
        enlist[`recent_outcomes]!enlist .qpipe.job.order_quality.example_input;
        ([] book:`london`london`newyork`newyork; window:0D00:01 0D00:05 0D00:01 0D00:05;
            as_of:4#2026.10.09D10:05:00; n_orders:3 5 0 1; n_filled:2 3 0 0; n_rejected:0 1 0 1;
            fill_ratio:(2%3;0.6;0n;0f); reject_ratio:(0f;0.2;0n;1f))))];

/ Every five seconds, over the last minute, five minutes and hour, on the outcome's
/ source time. An outcome received more than the lateness after it is dropped and
/ logged. The kind carries the buffer across midnight and replays it.
.qetl.job.stream.at_rolling[`order_quality;`procname`events`by`event_time`transform`publishes`windows`lateness`period`start_with_all`note!(
    `order_quality1;
    `order_outcomes;
    `book;
    `source_time;
    `order_quality;
    `order_quality;
    .qpipe.transform.order_quality.windows;
    .qpipe.transform.order_quality.lateness;
    .qpipe.transform.order_quality.period;
    0b;
    "rolling fill and reject ratios per book over the last 1m, 5m and 1h")];
