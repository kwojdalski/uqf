/ fx_orders_feed.q - the whole of the synthetic order-flow feed
/ (.qpipe.job.fx_orders_feed).
/ .
/ Subscribes to nothing and publishes one `orders` row a second: a random
/ pair, book, product, side and size, at a price a few pips either side of
/ that pair's level.
/ .
/ WHY THIS IS NOT fx_trades_feed. That feed publishes FILLS - every row
/ already happened. This one publishes ORDER FLOW, most of which did not:
/ a new order, then a fill or a cancel or a reject. The positions service
/ downstream exists partly to get that filter right, and a feed that only
/ ever emitted fills would let it be written without one and pass.
/ .
/ The mix is roughly two-thirds filled, which is high for a real desk and
/ deliberate: a demo that has to run for ten minutes before a position
/ moves is a demo nobody watches.
/ .
/ WHAT IS IN THIS FILE: the books and products it invents, the row
/ builder, the draw, and the declaration the runner reads.
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.
/ `time` is not published - the plant stamps its own (invariant 1).

\d .

/ Where rows go. A stub until .qetl.job.stream.wire points it at a tickerplant
/ (the runner) or at a recorder (a test).
.qpipe.job.fx_orders_feed.publish:.qetl.job.stream.unwired `fx_orders_feed;

/ The trading books this invented desk runs. Two, because one book makes
/ every per-book figure equal to the desk total and a bug that drops the
/ dimension entirely would still look right.
.qpipe.job.fx_orders_feed.books:`london`newyork

/ The products each book trades. `spot and `fwd differ in nothing here -
/ they exist so the positions service is keyed on three dimensions rather
/ than two, which is the shape a real desk reports and the shape that
/ catches a rollup written against the wrong columns.
.qpipe.job.fx_orders_feed.products:`spot`fwd

/ The statuses an order can reach, drawn from with the weights below.
/ Only `filled moves a position, which is the point.
.qpipe.job.fx_orders_feed.statuses:`filled`filled`cancelled`rejected

/ Pips per unit, matching the trades schema's convention.
.qpipe.job.fx_orders_feed.pip_factor:.qf.px.pip_factor .qf.demo.pairs

/ The sizes an order is drawn from. Float, matching the schema.
.qpipe.job.fx_orders_feed.sizes:500000 1000000 2000000 5000000f

/ The first order id a process issues: microseconds since 2026.01.01.
/ @return the seed, a long
/ @eg .qpipe.job.fx_orders_feed.seed_id[]>0 -> 1b
.qpipe.job.fx_orders_feed.seed_id:{[] "j"$(.z.p-2026.01.01D00:00:00)%1000}

/ The order id last issued (#1265). Monotonic within a process, and SEEDED
/ from the process start rather than from 0: a feed restarted the same day
/ would otherwise reissue 1,2,3... and executions' fill_id, which is derived
/ from the order id, would repeat. The id stays a long, as orders declares it.
/ The seed is MICROSECONDS since 2026.01.01, not nanoseconds since 2000 (#1324):
/ nanoseconds put every id near 8.5e17, above 2^53, so a JSON consumer (the web,
/ Grafana) collapses neighbouring ids and the web refuses to filter on one.
/ Microseconds stay under 2^53 for about 285 years. A restart cannot collide
/ while the feed issues fewer than a million orders a second: a later start
/ begins 1e6 per second above an earlier one, which only gains 1 per second.
.qpipe.job.fx_orders_feed.last_id:.qpipe.job.fx_orders_feed.seed_id[]

/ One order's rows, for a given pair index, book, product, side, size,
/ status and slippage in pips.
/ .
/ Every column is a ONE-ELEMENT VECTOR, never a bare atom - the plant
/ takes its row count from column length, so an atom makes a one-row batch
/ read as a one-column one.
/ @param id the order id
/ @param i which pair, as an index into .qf.demo.pairs
/ @param bk the book
/ @param pr the product
/ @param side 1 for a buy, -1 for a sell
/ @param size the order size
/ @param status one of statuses
/ @param slip_pips how far from the pair's level it printed, in pips
/ @return one order's rows: order_id, sym, book, product, side, size, price, order_status
/ @eg count .qpipe.job.fx_orders_feed.order_rows[1;0;`london;`spot;1;1e6;`filled;0] -> 8
.qpipe.job.fx_orders_feed.order_rows:{[id;i;bk;pr;side;size;status;slip_pips]
    price:.qf.demo.spot[i]+slip_pips%.qpipe.job.fx_orders_feed.pip_factor[i];
    (enlist id;enlist .qf.demo.pairs i;enlist bk;enlist pr;enlist side;
        enlist size;enlist price;enlist status)}

/ Draw one order and publish it. The draws live here so that order_rows
/ stays deterministic and its shape can be asserted.
.qpipe.job.fx_orders_feed.on_timer:{[]
    `.qpipe.job.fx_orders_feed.last_id set .qpipe.job.fx_orders_feed.last_id+1;
    i:rand count .qf.demo.pairs;
    / 1 or -1, always an ATOM: indexing `1 -1` with a possibly-empty
    / vector is how a sibling feed once produced a list, and the plant
    / read it as two rows.
    side:1-2*rand 2;
    .qpipe.job.fx_orders_feed.publish[`orders;
        .qpipe.job.fx_orders_feed.order_rows[
            .qpipe.job.fx_orders_feed.last_id;
            i;
            .qpipe.job.fx_orders_feed.books rand count .qpipe.job.fx_orders_feed.books;
            .qpipe.job.fx_orders_feed.products rand count .qpipe.job.fx_orders_feed.products;
            side;
            .qpipe.job.fx_orders_feed.sizes rand count .qpipe.job.fx_orders_feed.sizes;
            .qpipe.job.fx_orders_feed.statuses rand count .qpipe.job.fx_orders_feed.statuses;
            -3+rand 7]];
    }

\d .

/ Once a second, the same cadence as the fills feed: an order is a rarer
/ event than a quote, and the positions service snapshots every five.
.qetl.job.stream.define[`fx_orders_feed;`procname`subscribe_to`publishes`period`on_timer`start_with_all`note!(
    `fxordersfeed1;
    `symbol$();
    enlist `orders;
    0D00:00:01.000;
    .qpipe.job.fx_orders_feed.on_timer;
    1b;
    "synthetic order flow, most of which never becomes a fill - fxpositions1's input")];
