/ fx_positions.q - the whole of the FX positions service
/ (.qsub.fx_positions).
/ .
/ Subscribes to `orders`, nets every FILLED one into a running book keyed
/ on (sym, book, product), and on a timer publishes two things: a
/ `fx_position` snapshot of the whole book, and a `fx_limit_breach` row
/ for each limit newly crossed.
/ .
/ Modelled on Data Intellect's TorQ FX positions engine, built on this
/ repository's own machinery instead - and it runs with no TorQ at all,
/ through .qtick and scripts/processes/run_stream.q.
/ .
/ WHY IT SNAPSHOTS ON A TIMER RATHER THAN PER BATCH. A position is a
/ STATE, and republishing the whole book on every order would write a
/ snapshot per tick - the same row over and over for every pair that did
/ not trade. The timer decouples how often the desk wants to see the book
/ from how fast the market happens to be going, which is what a snapshot
/ is for. Breaches go out on the same timer for the same reason, and are
/ throttled on top of it (see .qlimit.throttle).
/ .
/ WHY IT IS NOT posbook. .qsub.posbook answers "what did we make", per
/ sym, at weighted-average cost, marked to mid. This answers "what are we
/ holding", along the dimensions a desk reports on, with no marks and no
/ P&L. Same fills, different question - see src/portfolio/desk_positions.q
/ for why one module cannot honestly do both.
/ .
/ WHAT IS IN THIS FILE: the schemas, the netting transform with its
/ examples, the batch handler, the timer, the state, and the declaration
/ the runner reads.
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.
/ `time` is not published - the plant stamps it (invariant 1).

\d .qsub.fx_positions

/ ------------------------------------------------------------- THE SHAPES

orders:([] time:`timestamp$(); order_id:`long$(); sym:`symbol$(); book:`symbol$();
    product:`symbol$(); side:`long$(); size:`float$(); price:`float$();
    order_status:`symbol$())
desk_book:([] sym:`symbol$(); book:`symbol$(); product:`symbol$();
    base_qty:`float$(); quote_qty:`float$(); fill_count:`long$())
fx_position:([] sym:`symbol$(); book:`symbol$(); product:`symbol$();
    base_qty:`float$(); quote_qty:`float$(); fill_count:`long$(); break_even:`float$())
fx_limit_breach:([] sym:`symbol$(); book:`symbol$(); product:`symbol$();
    metric:`symbol$(); observed:`float$(); cap:`float$(); severity:`symbol$();
    utilisation:`float$())

/ The dimensions the book is keyed on - the blog's (currency_pair,
/ product, book), in this repository's own column names.
dimensions:`sym`book`product

/ The status that moves a position. One value, named once: a service that
/ spelled it inline in the handler would be a service where changing it
/ means finding every place it was spelled.
filled_status:`filled

/ ---------------------------------------------------------- THE TRANSFORM

/ Net a batch of orders into a desk book, keeping only the filled ones.
/ .
/ The book is an INPUT and the new book is the output, never a global this
/ mutates - which is what lets a batch against a given book have an
/ expected answer, and is the convention every job here follows.
/ .
/ THE FILTER IS PART OF THE TRANSFORM, not of the handler, because it is
/ the part most worth having examples for: an engine that nets cancels and
/ rejects into its position is wrong in a way that looks like nothing at
/ all until someone reconciles.
/ @param book the current desk book, unkeyed
/ @param batch the orders that arrived, any statuses
/ @return the new desk book, unkeyed
net_orders:{[book;batch]
    filled:select from batch where order_status=.qsub.fx_positions.filled_status;
    updated:.qdesk.apply_fills[
        .qsub.fx_positions.dimensions xkey book;
        select sym, book, product, side, size, price from filled];
    `sym`book`product xasc 0!updated}

/ --------------------------------------------------------------- THE JOB

/ Where rows go. A stub until .qstream.wire points it at a tickerplant
/ (a runner) or at a recorder (a test).
publish:.qstream.unwired `fx_positions;

/ The running book, keyed on its dimensions. Only ever changed through
/ net_orders, which has expected tables.
book:`sym`book`product xkey desk_book;

/ The desk's limits. Empty until something loads them - a service with no
/ limits reports positions and polices nothing, which is a legitimate way
/ to run and better than inventing caps nobody agreed to.
/ .
/ Scoped on the same three dimensions as the book, so a limit is written
/ per pair per book per product. A desk-wide limit is the same table at a
/ coarser scope, evaluated against .qdesk.rollup - see limits.q.
limits:([] sym:`symbol$(); book:`symbol$(); product:`symbol$();
    metric:`symbol$(); cap:`float$(); severity:`symbol$());

/ The metrics the limits are allowed to police. Named rather than "every
/ numeric column", so adding a column to the book cannot silently start
/ policing it - see .qlimit.measure.
policed:`base_qty`quote_qty

/ How long a breach stays quiet after alerting. A breach is a state that
/ persists until someone trades out of it, so without this the desk gets
/ one alert per timer tick and stops reading them.
alert_period:0D00:05;

/ Throttle state: breach identity -> when it was last alerted.
alerts:.qlimit.no_alerts[];

/ Load a limits table, refusing a malformed one at load rather than at the
/ first breach - a limits table that polices nothing looks exactly like a
/ quiet day.
/ @param t the limits table, scoped on sym, book and product
/ @return the number of limits loaded
/ @throws error naming a malformed limit, or a scope column that is not one of this book's dimensions
/ @eg .qsub.fx_positions.load_limits[([] sym:enlist `EURUSD; book:enlist `london; product:enlist `spot; metric:enlist `base_qty; cap:enlist 1e6; severity:enlist `hard)] -> 1
load_limits:{[t]
    .qlimit.require_limits t;
    / .qlimit cannot check this: it is not told which columns are meant to
    / be scope, so a stray column simply becomes one and the limit matches
    / nothing. THIS file knows the dimensions, so it can say so at load -
    / and a limit that matches nothing is exactly the failure that looks
    / like a quiet day.
    scope:.qlimit.scope_cols t;
    stray:scope where not scope in .qsub.fx_positions.dimensions;
    if[count stray;
        '"load_limits: ",(", " sv string stray)," is not a dimension of this book, so a limit carrying it would be scoped on something no position has - the dimensions are ",
         ", " sv string .qsub.fx_positions.dimensions];
    `.qsub.fx_positions.limits set t;
    count t}

/ Net one batch of orders into the book.
/ .
/ The book is replaced BEFORE anything is published, the way posbook does
/ it: orders will not be redelivered, so a failed publish must not also
/ lose them from the position.
/ @param tbl the table the batch arrived on
/ @param batch the rows, as a table
/ @return nothing
on_batch:{[tbl;batch]
    if[not tbl=`orders; :()];
    if[0=count batch; :()];
    updated:.qxf.apply[`fx_positions;`book`batch!(
        0!.qsub.fx_positions.book;
        select time, order_id, sym, book, product, side, size, price, order_status from batch)];
    `.qsub.fx_positions.book set `sym`book`product xkey updated;
    }

/ The breaches this book is in, as of now, after throttling.
/ .
/ Separated from on_timer so that the decision - which breaches are worth
/ sending - can be tested without a timer and without a publisher. The
/ throttle state is read and written here because it is state that only
/ makes sense across calls; everything it depends on is an argument.
/ @param now the time to throttle against
/ @return the breaches to publish, possibly none
fresh_breaches:{[now]
    if[0=count .qsub.fx_positions.limits; :0#.qsub.fx_positions.fx_limit_breach];
    measured:.qlimit.measure[.qsub.fx_positions.book;.qsub.fx_positions.policed];
    breaches:.qlimit.evaluate[measured;.qsub.fx_positions.limits];
    r:.qlimit.throttle[.qsub.fx_positions.alerts;breaches;now;.qsub.fx_positions.alert_period];
    `.qsub.fx_positions.alerts set r`state;
    r`alerts}

/ Publish a snapshot of the whole book, and any newly breached limit.
/ .
/ THE WHOLE BOOK, every tick, not just what moved. A snapshot that carried
/ only the changed rows would need its reader to keep the rest, which is
/ the reader building a second copy of this service's state and getting it
/ wrong on the first dropped message. The book is one row per (pair, book,
/ product) and a desk has hundreds, not millions.
/ @return nothing
on_timer:{[]
    snapshot:0!.qdesk.break_even .qsub.fx_positions.book;
    if[count snapshot;
        .qsub.fx_positions.publish[`fx_position;
            select sym, book, product, base_qty, quote_qty, fill_count, break_even from snapshot]];
    breaches:.qsub.fx_positions.fresh_breaches .z.p;
    if[count breaches;
        .qsub.fx_positions.publish[`fx_limit_breach;
            select sym, book, product, metric, observed, cap, severity, utilisation from breaches]];
    }

\d .

.qxf.define[`fx_positions;`inputs`output`fn`examples!(
    `book`batch!(.qsub.fx_positions.desk_book;.qsub.fx_positions.orders);
    .qsub.fx_positions.desk_book;
    .qsub.fx_positions.net_orders;
    (
    / From an empty book: a buy and a sell of the same pair on one book net
    / against each other, a cancel moves nothing at all, and a second book
    / is a row of its own rather than being folded into the first.
    `inputs`expected!(
        `book`batch!(
            .qsub.fx_positions.desk_book;
            ([] time:2026.09.17D10:00:00+0D00:00:01*til 4;
                order_id:1 2 3 4;
                sym:`EURUSD`EURUSD`EURUSD`EURUSD;
                book:`london`london`london`newyork;
                product:`spot`spot`spot`spot;
                side:1 -1 1 1;
                size:1000000 400000 5000000 250000f;
                price:1.0850 1.0860 1.0855 1.0851;
                order_status:`filled`filled`cancelled`filled));
        ([] sym:`EURUSD`EURUSD; book:`london`newyork; product:`spot`spot;
            base_qty:600000 250000f; quote_qty:-650600 -271275f; fill_count:2 1));
    / Against an existing book: a fill in a product the book has never seen
    / opens a row rather than being dropped, and the pair already there is
    / added to rather than replaced.
    `inputs`expected!(
        `book`batch!(
            ([] sym:enlist `USDJPY; book:enlist `london; product:enlist `spot;
                base_qty:enlist 1000000f; quote_qty:enlist -149500000f; fill_count:enlist 1);
            ([] time:2026.09.17D10:00:05 2026.09.17D10:00:06;
                order_id:5 6;
                sym:`USDJPY`USDJPY;
                book:`london`london;
                product:`spot`fwd;
                side:-1 1;
                size:400000 250000f;
                price:149.60 149.55;
                order_status:`filled`filled));
        ([] sym:`USDJPY`USDJPY; book:`london`london; product:`fwd`spot;
            base_qty:250000 600000f; quote_qty:-37387500 -89660000f; fill_count:1 2))
    ))];

.qstream.register[`fx_positions;`procname`subscribes`publishes`on_batch`timer_period`on_timer!(
    `fxpositions1;
    enlist `orders;
    `fx_position`fx_limit_breach;
    .qsub.fx_positions.on_batch;
    0D00:00:05.000;
    .qsub.fx_positions.on_timer)];
