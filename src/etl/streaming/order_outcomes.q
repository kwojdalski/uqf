/ order_outcomes.q - the `order_outcomes` normalizer: every order source's status
/ events, published as one contract (.qpipe.job.order_outcomes, #1396).
/ .
/ order_quality1 asks "of the requests in the last minute, how many filled and how
/ many were rejected" and used to ask it of the `orders` table, by its `book`
/ and `order_status` columns and the words `filled` and `rejected`. Connecting a
/ second order-management system would have meant reshaping its data into
/ `orders` outside any contract, or editing the statistic. This normalizer is
/ the seam instead: each source is ONE declared transform from its own shape to
/ the plant table `order_outcomes`, and the statistic reads only that
/ (src/etl/transforms/order_quality.q).
/ .
/ NOT `executions`. That normalizer publishes fills and drops everything that did
/ not trade; a reject rate needs the rejects, the cancels and the requests still
/ open.
/ .
/ THE CONTRACT, column by column (the table is in src/etl/plant_tables.q, the
/ vocabulary in src/etl/transforms/order_outcome.q):
/ .
/   source_time    when the SOURCE says the event happened. NOT `time`, the
/                  plant's stamp of the normalized row: a window cut on the
/                  receipt would put a delayed order in the wrong minute.
/   source         the feed, a symbol: the SCOPE of request_id.
/   request_id     the source's id of one request ATTEMPT. A retry after a
/                  reject is a new request and must have a new id; an adapter
/                  whose source reuses one id across attempts must derive an
/                  attempt id (an order id plus a retry count) in its mapping.
/   source_status  the source's own code, verbatim.
/   outcome        filled, rejected, cancelled (the request ended), unresolved
/                  (a non-terminal update) or unmapped (a code the adapter does
/                  not know, or a row without an id).
/   book, product, venue   the grouping dimensions; null where the source has
/                  none. A venue is never defaulted.
/ .
/ ONE ROW PER STATUS EVENT, NOT PER ORDER. A mapping is stateless and row-wise, so
/ an order reported five times is five rows. Which of them is a request is the
/ statistic's decision (one request, once, at its latest terminal outcome) over
/ the rolling buffer's rows; see src/etl/transforms/order_quality.q for the
/ policy and why there is no consolidation stage here.
/ .
/ ADDING A SOURCE: declare its columns, a status_map, one transform into the
/ output below, and one entry in the normalize declaration at the bottom. Nothing
/ else changes - the statistic, the plant table and order_quality1 stay as they are.
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.

\d .qpipe.job.order_outcomes

/ Where rows go. A stub until .qetl.job.stream.wire points it at a tickerplant
/ (the runner) or at a recorder (a test).
publish:.qetl.job.stream.unwired `order_outcomes;

/ The canonical output. No `time`: the plant stamps it.
order_outcomes:.qetl.plant.published `order_outcomes

/ What the orders mapping reads - only the columns from_orders uses. Not named `orders`:
/ a table in a job's namespace named after a plant table is held to that table's whole shape.
orders_read:.qetl.plant.columns[`orders;`time`order_id`book`product`order_status]

/ The scope of an orders request id.
orders_source:`orders

/ The orders feed's statuses. It reports one row per order, at its final status,
/ so none is unresolved; any other code is `unmapped.
orders_status:.qpipe.transform.order_outcome.status_map[`filled`rejected`cancelled;`filled`rejected`cancelled]

/ An orders batch as order outcomes. The feed's rows carry the plant's stamp and no
/ venue (`order_routing` has no producer): source_time is the row's time and venue
/ is null. A row without an order_id cannot be identified, so it is `unmapped.
/ @param batch an orders batch: time, order_id, book, product, order_status
/ @return canonical order_outcomes
/ @eg .qpipe.job.order_outcomes.from_orders[([] time:enlist 2026.10.09D10:00:00; order_id:enlist 7; book:enlist `london; product:enlist `spot; order_status:enlist `filled)]`request_id  ->  enlist `7
from_orders:{[batch]
    rid:?[null batch`order_id;`;`$string batch`order_id];
    out:.qpipe.transform.order_outcome.translate[.qpipe.job.order_outcomes.orders_status;batch`order_status];
    select source_time:time, source:.qpipe.job.order_outcomes.orders_source, request_id:rid, source_status:order_status,
        outcome:?[null rid;`unmapped;out], book, product, venue:` from batch}

\d .

/ Four orders, one of every kind the mapping distinguishes: a fill, a cancel, a reject
/ and a status the feed does not define; then a row with no id. The venue is null on
/ every row - `orders` does not carry one.
.qetl.transform.define[`order_outcomes_from_orders;`inputs`output`fn`examples!(
    (enlist `orders)!enlist .qpipe.job.order_outcomes.orders_read;
    .qpipe.job.order_outcomes.order_outcomes;
    .qpipe.job.order_outcomes.from_orders;
    enlist `inputs`expected!(
        (enlist `orders)!enlist ([] time:2026.10.09D10:00:00 2026.10.09D10:00:01 2026.10.09D10:00:02 2026.10.09D10:00:03 2026.10.09D10:00:04;
            order_id:1 2 3 4 0N; book:`london`london`newyork`newyork`london; product:`spot`spot`fwd`fwd`spot;
            order_status:`filled`cancelled`rejected`pending`filled);
        ([] source_time:2026.10.09D10:00:00 2026.10.09D10:00:01 2026.10.09D10:00:02 2026.10.09D10:00:03 2026.10.09D10:00:04;
            source:5#`orders; request_id:(`1;`2;`3;`4;`);
            source_status:`filled`cancelled`rejected`pending`filled;
            outcome:`filled`cancelled`rejected`unmapped`unmapped;
            book:`london`london`newyork`newyork`london; product:`spot`spot`fwd`fwd`spot; venue:5#`)))];

.qetl.job.stream.normalize[`order_outcomes;`procname`output`input`start_with_all`note!(
    `order_outcomes1;
    .qpipe.job.order_outcomes.order_outcomes;
    (enlist `orders)!enlist `order_outcomes_from_orders;
    0b;
    "every order source's status events as one contract: orders -> order_outcomes")];
