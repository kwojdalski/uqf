/ hdb_orders.q - the orders the desk's feed published, from the local HDB (.qpipe.source.hdb_orders, #1396).
/ .
/ The live order_quality1 job keeps the rolling fill and reject series in a buffer
/ that dies with its process: an hour it was down for has no rows, and the series
/ it published before a change to the counting policy stays as it was. This source
/ is what hdb_order_quality_backfill reads instead - the HDB's `orders`, which end
/ of day saved - and the worker runs them through the SAME adapter
/ (order_outcomes_from_orders) and the SAME statistic
/ (src/etl/transforms/order_quality.q) the live pair applies.
/ .
/ Two inputs, because a rolling window looks back:
/ .
/   `orders         the window's orders, [range_from; range_to) on the plant's `time`
/                   - the primary input, which owns the window and is what the run counts
/   `orders_before  the orders in the longest window BEFORE it, so the first
/                   publications of the window have the same buffer the live job had
/                   - a supporting input (#617): context, never cut to the window
/ .
/ The reach is the longest window the statistic publishes
/ (.qpipe.transform.order_quality.windows). The live lateness is not a reach:
/ an order read from the HDB has no receipt time distinct from its stamp.
/ .
/ Live: export UQF_SOURCE_CRED_HDB_ORDERS=localhost:<hdb1's port> in the shell
/ `uqs job backfill` runs from. Without it, the fixture below is used.

\d .

.qpipe.source.hdb_orders.source_name:`hdb_orders

/ The primary input's columns: what the adapter reads. Not `sym`, `side`, `size`
/ or `price`: a column nothing reads is one more thing an upstream change breaks.
.qpipe.source.hdb_orders.columns:`time`order_id`book`product`order_status
.qpipe.source.hdb_orders.types:"pjsss"

.qpipe.source.hdb_orders.time_column:`time

/ The feed's ids are unique (seeded from the process start, #1265).
.qpipe.source.hdb_orders.row_key:`order_id`time

/ The supporting input: the orders before the window, read by the transform as
/ `orders_before`.
.qpipe.source.hdb_orders.supporting:enlist[`orders_before]!enlist .qetl.plant.columns[`orders;.qpipe.source.hdb_orders.columns]

/ .u.upd stamps `time` in UTC.
.qpipe.source.hdb_orders.tz:`UTC

/ How far before a window its orders are read: the longest window published.
/ A function, not a value, so it follows the statistic.
/ @return a timespan
/ @eg .qpipe.source.hdb_orders.reach[]  ->  0D01
.qpipe.source.hdb_orders.reach:{[] last .qpipe.transform.order_quality.windows}

/ The window's orders, and the longest window's worth before it. The tables are
/ date-partitioned, so the partition is constrained first, one date either side.
/ @param h an open handle to the HDB
/ @param range_from inclusive lower bound
/ @param range_to exclusive upper bound
/ @return `orders`orders_before!(the window's orders; the orders before it)
.qpipe.source.hdb_orders.query:{[h;range_from;range_to]
    f:{[from_ts;to_ts]
        `time xasc select time, order_id, book, product, order_status from `orders
            where date within (-1 1)+`date$(from_ts;to_ts), time>=from_ts, time<to_ts};
    `orders`orders_before!(.qetl.source.ipc[h;f;range_from;range_to];
        .qetl.source.ipc[h;f;range_from-.qpipe.source.hdb_orders.reach[];range_from])}

/ The fixture's orders on 2026.10.09: ten orders between 09:20 and 10:40, one id
/ delivered twice, a cancel, a reject and a status the adapter does not know.
/ The fixture is not cut for the context: the transform does that.
/ @return orders as the HDB holds them
/ @eg count .qpipe.source.hdb_orders.fixture[]`orders  ->  11
.qpipe.source.hdb_orders.fixture:{[]
    o:([] time:2026.10.09D09:20:00 2026.10.09D09:40:00 2026.10.09D09:55:00 2026.10.09D10:05:00 2026.10.09D10:05:00 2026.10.09D10:10:00 2026.10.09D10:12:30 2026.10.09D10:20:00 2026.10.09D10:31:00 2026.10.09D10:35:00 2026.10.09D10:40:00;
        order_id:1 2 3 4 4 5 6 7 8 9 10;
        book:`london`newyork`london`london`london`newyork`london`london`newyork`london`london;
        product:11#`spot;
        order_status:`filled`rejected`filled`filled`filled`cancelled`rejected`pending`filled`filled`rejected);
    `orders`orders_before!(o;o)}

.qetl.source.define[.qpipe.source.hdb_orders.source_name;
    `source`table_name`time_column`row_key`columns`types`query`fixture`tz`supporting!
    (.qpipe.source.hdb_orders.source_name;`orders;.qpipe.source.hdb_orders.time_column;.qpipe.source.hdb_orders.row_key;.qpipe.source.hdb_orders.columns;.qpipe.source.hdb_orders.types;.qpipe.source.hdb_orders.query;.qpipe.source.hdb_orders.fixture;.qpipe.source.hdb_orders.tz;.qpipe.source.hdb_orders.supporting)];

\d .
