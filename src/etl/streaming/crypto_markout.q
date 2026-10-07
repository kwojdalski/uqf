/ crypto_markout.q - markouts on real crypto fills (.qpipe.job.crypto_markout).
/ .
/ Subscribes to `crypto_trades` and `crypto_book`, buffers both, and every
/ second scores the fills old enough to score: at each horizon, the fill
/ price against the BEST MID ACROSS VENUES, in basis points. Publishes
/ `crypto_execution_quality`.
/ .
/ THE REAL MARKOUT JOB (#729). demo_markout scores the demo's invented FX
/ pairs off `trades` and `quote`, which only the demo's feeds publish. This
/ scores cryptorust's real fills - crypto_trades, from get_recent_real_fills
/ - and never crypto_sim_fills: plant_tables.q keeps simulated and real
/ execution apart, and so does this.
/ .
/ THREE CHOICES, each deliberate:
/ .
/   best mid across venues   the reference is the highest bid and lowest ask
/                            any venue showed for the pair, so it is the
/                            market's price rather than the fill venue's
/                            own. A venue whose top of book is older than
/                            max_age at the horizon does not count - a venue
/                            that went quiet must not set the best price.
/   basis points             pips mean nothing for BTC-USDT. bps =
/                            side * 10000 * (ref - trade) / trade, positive
/                            when the market moved in the fill's favour, the
/                            same sign convention as .qexec.markout.
/   plant time on both       a fill's `time` and a book's `time` are both
/                            this tickerplant's receipt stamps, so they are
/                            on one clock. crypto_trades carries no venue
/                            stamp, and get_recent_real_fills is POLLED, so
/                            a fill's time is when it was received, which
/                            can trail the trade by the poll interval.
/ .
/ A fill with no live book at a horizon keeps its row with a null ref_price
/ and markout_bps, rather than being dropped, so the gap shows.
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.
/ `time` is not published - .u.upd stamps its own (invariant 1). Every global
/ a function reads is fully qualified (torq_pipeline.q's invariant 5).

\d .qpipe.job.crypto_markout

/ ------------------------------------------------------------- THE SHAPES

/ The horizons each fill is scored at, and the oldest a venue's top of book
/ may be at a horizon and still count toward the best mid - superbook's
/ expiry window for FX, five seconds.
horizons:0D00:00:01 0D00:00:10
max_horizon:max horizons
max_age:0D00:00:05

/ The transform's inputs: fills as crypto_trades carries them, and books as
/ crypto_book does, narrowed to what scoring reads.
real_fills:.qetl.plant.columns[`crypto_trades;`time`sym`venue`side`trade_price`exchange_fill_id]
books:.qetl.plant.columns[`crypto_book;`time`sym`venue`bid_prices`ask_prices]
crypto_execution_quality:.qetl.plant.published `crypto_execution_quality

/ ---------------------------------------------------------- THE TRANSFORM

/ Each venue's top of book, one row per book update.
/ .
/ A side the venue did not quote is null, not an error: its first level is
/ taken only when it has one.
/ @param books crypto_book rows
/ @return table time, sym, venue, bid, ask
/ @eg exec bid from .qpipe.job.crypto_markout.top_of_book[([] time:enlist 2026.09.17D10:00:00; sym:enlist `$"BTC-USDT"; venue:enlist `a; bid_prices:enlist 62000 61999f; ask_prices:enlist `float$())]  ->  ,62000f
top_of_book:{[books]
    top:{[px] $[count px; first px; 0n]};
    select time, sym, venue, bid:top each bid_prices, ask:top each ask_prices from books}

/ The best mid across venues at each target (sym; time).
/ .
/ Per venue, the latest top of book at or before the target (aj); dropped
/ when older than max_age. Then the highest bid and the lowest ask over the
/ venues left. Null when either side has no live venue.
/ @param tob top of book rows, from top_of_book
/ @param targets table sym, time - the instants to price
/ @param max_age the oldest a venue's book may be and still count
/ @return a float vector aligned with targets
/ @eg .qpipe.job.crypto_markout.best_mid[([] time:2026.09.17D10:00:00 2026.09.17D10:00:00; sym:2#`$"BTC-USDT"; venue:`a`b; bid:62000 62004f; ask:62010 62008f);([] sym:enlist `$"BTC-USDT"; time:enlist 2026.09.17D10:00:01);0D00:00:05]  ->  ,62006f
best_mid:{[tob;targets;max_age]
    if[0=count tob; :(count targets)#0n];
    tob:`sym`time xasc tob;
    per_venue:{[tob;targets;max_age;v]
        j:aj[`sym`time;targets;select sym, time, bid, ask, quoted:time from tob where venue=v];
        live:(not null j`quoted) & max_age>=(j`time)-j`quoted;
        (?[live;j`bid;0n];?[live;j`ask;0n])}[tob;targets;max_age] each distinct tob`venue;
    / null is the smallest float, so it never wins a max but always wins a
    / min: asks are filled high before taking the lowest, then put back
    best_bid:max per_venue[;0];
    best_ask:min 0w^per_venue[;1];
    best_ask:?[best_ask=0w;0n;best_ask];
    mid:0.5*best_bid+best_ask;
    ?[null best_bid;0n;mid]}

/ Score each fill's markout, in bps, at every horizon, against the best mid
/ across venues at trade_time+horizon.
/ @param real_fills crypto_trades rows, as the batch handler buffers them
/ @param books crypto_book rows, as the batch handler mirrors them
/ @return one row per fill per horizon, in fill order then horizon order
score_markouts:{[real_fills;books]
    if[0=count real_fills; :.qpipe.job.crypto_markout.crypto_execution_quality];
    hs:.qpipe.job.crypto_markout.horizons;
    n:count real_fills;
    m:count hs;
    f:real_fills "j"$raze m#'til n;
    h:hs (n*m)#til m;
    targets:([] sym:f`sym; time:(f`time)+h);
    ref:.qpipe.job.crypto_markout.best_mid[.qpipe.job.crypto_markout.top_of_book books;targets;.qpipe.job.crypto_markout.max_age];
    move:(ref-f`trade_price)%f`trade_price;
    bps:(f`side)*10000*move;
    ([] sym:f`sym; venue:f`venue; fill_id:f`exchange_fill_id; trade_time:f`time; horizon:h;
        side:f`side; trade_price:f`trade_price; ref_price:ref; markout_bps:bps)}

/ --------------------------------------------------------------- THE JOB

/ Where rows go. A stub until .qetl.job.stream.wire points it at the tickerplant
/ (the runner) or at a recorder (a test). Never call .u.upd from here.
publish:.qetl.job.stream.unwired `crypto_markout;

/ STATE. pending is the queue of fills not yet old enough to score;
/ book_hist the books they will be scored against, pruned as fills drain -
/ a book older than every pending fill by more than max_age can never count.
pending:real_fills;
book_hist:books;

/ Buffer each batch: real fills into the queue, books into the history.
/ @param t the table the batch arrived on
/ @param x the rows, as a table
/ @return nothing
on_batch:{[t;x]
    $[t=`crypto_trades;
        `.qpipe.job.crypto_markout.pending insert select time, sym, venue, side, trade_price, exchange_fill_id from x;
      t=`crypto_book;
        `.qpipe.job.crypto_markout.book_hist insert select time, sym, venue, bid_prices, ask_prices from x;
      ()];
    }

/ Score and publish every fill whose longest horizon has passed by `now`,
/ then drop it from the queue and drop books nothing pending can use.
/ @param now the time to score up to
/ @return nothing
score_ready:{[now]
    if[0=count .qpipe.job.crypto_markout.pending; :()];
    mask:.qpipe.job.crypto_markout.pending[`time]<=now-.qpipe.job.crypto_markout.max_horizon;
    ready:.qpipe.job.crypto_markout.pending where mask;
    if[0=count ready; :()];
    out:.qetl.transform.apply[`crypto_execution_quality;`real_fills`books!(ready;.qpipe.job.crypto_markout.book_hist)];
    .qpipe.job.crypto_markout.publish[`crypto_execution_quality;out];
    .qetl.job.stream.evict[`.qpipe.job.crypto_markout.pending;mask];
    left:.qpipe.job.crypto_markout.pending`time;
    oldest:$[count left; min left; now-.qpipe.job.crypto_markout.max_horizon];
    cutoff:oldest-.qpipe.job.crypto_markout.max_age;
    .qetl.job.stream.evict[`.qpipe.job.crypto_markout.book_hist;.qpipe.job.crypto_markout.book_hist[`time]<cutoff];
    }

/ The timer body: score whatever is ready as of now.
/ @return nothing
on_timer:{[] .qpipe.job.crypto_markout.score_ready .qpipe.job.crypto_markout.now[]}

/ The clock score_ready is run against - a function rather than .z.p inline,
/ so a test can stand a fixed time in for it. .z.p is UTC, as .u.upd stamps.
/ @return the current timestamp
now:{[] .z.p}

\d .

/ Two venues quote BTC-USDT; one goes quiet. The 1s horizon sees both (best
/ bid 62004 from b, best ask 62008 from b: mid 62006), the 10s horizon only
/ a, b's book being 10s old by then (mid 62015).
.qetl.transform.define[`crypto_execution_quality;`inputs`output`fn`examples!(
    `real_fills`books!(.qpipe.job.crypto_markout.real_fills;.qpipe.job.crypto_markout.books);
    .qpipe.job.crypto_markout.crypto_execution_quality;
    .qpipe.job.crypto_markout.score_markouts;
    enlist `inputs`expected!(
        `real_fills`books!(
            ([] time:enlist 2026.09.17D10:00:00; sym:enlist `$"BTC-USDT"; venue:enlist `a;
                side:enlist 1; trade_price:enlist 62000f; exchange_fill_id:enlist `f1);
            ([] time:2026.09.17D10:00:00 2026.09.17D10:00:00 2026.09.17D10:00:09;
                sym:3#`$"BTC-USDT"; venue:`a`b`a;
                bid_prices:(62000 61999f;enlist 62004f;enlist 62010f);
                ask_prices:(62010 62011f;enlist 62008f;enlist 62020f)));
        ([] sym:2#`$"BTC-USDT"; venue:`a`a; fill_id:`f1`f1;
            trade_time:2#2026.09.17D10:00:00; horizon:0D00:00:01 0D00:00:10;
            side:1 1; trade_price:62000 62000f; ref_price:62006 62015f;
            markout_bps:(10000*6%62000;10000*15%62000))))];

/ The process registry is read from this declaration: `procname` is the
/ process that runs it, and `start_with_all` whether `uqs start all` starts it
/ (absent: on demand, until the connection budget has room).
.qetl.job.stream.define[`crypto_markout;`procname`subscribe_to`publishes`on_batch`period`on_timer`note!(
    `crypto_markout1;
    `crypto_trades`crypto_book;
    enlist `crypto_execution_quality;
    .qpipe.job.crypto_markout.on_batch;
    0D00:00:01;
    .qpipe.job.crypto_markout.on_timer;
    "markouts on real crypto fills, in bps against the best mid across venues. Not started with the stack: its inputs come from cryptorust's recorders, or from cryptomock1 in their place, neither of which starts by default - `uqs start --profile crypto` brings it up with the mock")];
