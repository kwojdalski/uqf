/ fx_orders_feed.q - the whole of the synthetic order-flow feed
/ (.qsub.fx_orders_feed).
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

\d .qsub.fx_orders_feed

/ Where rows go. A stub until .qstream.wire points it at a tickerplant
/ (the runner) or at a recorder (a test).
publish:.qstream.unwired `fx_orders_feed;

/ The trading books this invented desk runs. Two, because one book makes
/ every per-book figure equal to the desk total and a bug that drops the
/ dimension entirely would still look right.
books:`london`newyork

/ The products each book trades. `spot and `fwd differ in nothing here -
/ they exist so the positions service is keyed on three dimensions rather
/ than two, which is the shape a real desk reports and the shape that
/ catches a rollup written against the wrong columns.
products:`spot`fwd

/ The statuses an order can reach, drawn from with the weights below.
/ Only `filled moves a position, which is the point.
statuses:`filled`filled`cancelled`rejected

/ Pips per unit, matching the trades schema's convention.
pip_factor:"j"$1%.qsynth.pip

/ The sizes an order is drawn from. Float, matching the schema.
sizes:500000 1000000 2000000 5000000f

/ The order id last issued. Monotonic within a process, so a replayed log
/ and a live session cannot collide within one run.
last_id:0

/ One order's rows, for a given pair index, book, product, side, size,
/ status and slippage in pips.
/ .
/ Every column is a ONE-ELEMENT VECTOR, never a bare atom - the plant
/ takes its row count from column length, so an atom makes a one-row batch
/ read as a one-column one.
/ @param id the order id
/ @param i which pair, as an index into .qsynth.pairs
/ @param bk the book
/ @param pr the product
/ @param side 1 for a buy, -1 for a sell
/ @param size the order size
/ @param status one of statuses
/ @param slip_pips how far from the pair's level it printed, in pips
/ @return one order's rows: order_id, sym, book, product, side, size, price, order_status
/ @eg count .qsub.fx_orders_feed.order_rows[1;0;`london;`spot;1;1e6;`filled;0] -> 8
order_rows:{[id;i;bk;pr;side;size;status;slip_pips]
    price:.qsynth.spot[i]+slip_pips%.qsub.fx_orders_feed.pip_factor[i];
    (enlist id;enlist .qsynth.pairs i;enlist bk;enlist pr;enlist side;
        enlist size;enlist price;enlist status)}

/ Draw one order and publish it. The draws live here so that order_rows
/ stays deterministic and its shape can be asserted.
on_timer:{[]
    `.qsub.fx_orders_feed.last_id set .qsub.fx_orders_feed.last_id+1;
    i:rand count .qsynth.pairs;
    / 1 or -1, always an ATOM: indexing `1 -1` with a possibly-empty
    / vector is how a sibling feed once produced a list, and the plant
    / read it as two rows.
    side:1-2*rand 2;
    .qsub.fx_orders_feed.publish[`orders;
        .qsub.fx_orders_feed.order_rows[
            .qsub.fx_orders_feed.last_id;
            i;
            .qsub.fx_orders_feed.books rand count .qsub.fx_orders_feed.books;
            .qsub.fx_orders_feed.products rand count .qsub.fx_orders_feed.products;
            side;
            .qsub.fx_orders_feed.sizes rand count .qsub.fx_orders_feed.sizes;
            .qsub.fx_orders_feed.statuses rand count .qsub.fx_orders_feed.statuses;
            -3+rand 7]];
    }

\d .

/ Once a second, the same cadence as the fills feed: an order is a rarer
/ event than a quote, and the positions service snapshots every five.
.qstream.define[`fx_orders_feed;`procname`subscribe_to`publishes`period`on_timer`start_with_all`note!(
    `fxordersfeed1;
    `symbol$();
    enlist `orders;
    0D00:00:01.000;
    .qsub.fx_orders_feed.on_timer;
    1b;
    "synthetic order flow, most of which never becomes a fill - fxpositions1's input")];
