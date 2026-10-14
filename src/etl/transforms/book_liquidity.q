/ book_liquidity.q - source-independent liquidity analytics over normalized books (#1397).
/ .
/ Two PURE steps, each a declared transform, and nothing else:
/ .
/   book_liquidity_snapshot   books + requested sizes -> what each book can fill
/   book_liquidity_minute     those snapshots + bar_start -> one row per minute
/ .
/ They read the `liquidity_book` contract below and know nothing of where a
/ book came from: no table names, no IPC, no credentials, no cursor. How a
/ source's rows become that contract is an ADAPTER's job (book_liquidity_adapters.q);
/ which window of rows arrives is the job's (streaming bars, or a backfill).
/ .
/ THE CONTRACT. `liquidity_book` is the market_data contract plus two
/ eligibility dimensions, `settlement` and `category`. market_data itself is
/ not extended: it replaces a book per (sym, source) and posbook marks to the
/ level-0 mid across sources, so a second population under one source would
/ either overwrite the first or be mixed into one mid. Here the dimensions are
/ part of a book's identity - (sym, source, settlement, category) - and of every
/ output key, so populations are never merged. See the design notes in
/ docs/reference/book-liquidity.md for each semantic below.
/ .
/ SEMANTICS (the tests in tests/q/test_book_liquidity.q pin each):
/   ladders      best-first: bids descending, asks ascending. A side whose usable
/                levels are not in that order is unusable (null price, zero
/                quantity, not full), never priced wrongly.
/   units        base-currency units, requested sizes in the same units.
/   depth        quantity is what the sweep actually filled, <= the request. A
/                partial sweep is never full.
/   withdrawal   an empty ladder is a side with nothing: null price, zero
/                quantity, not full - and the book is still a snapshot.
/   invalid      null, non-positive or infinite price or size are skipped by
/                .qbook.level_ok (inside .qbook.sweep_price).
/   duplicates   rows with one (sym, source, settlement, category, source_time)
/                are one observation; distinct requested sizes are used once.
/   average      the minute is the mean over snapshots, each weighing the same -
/                NOT weighted by how long a book stood.

\d .qpipe.transform.book_liquidity

/ The normalized book the pure steps read. `source` is the independent
/ liquidity provider (two adapters carrying one liquidity use one value), never
/ a physical table label. Ladders are best-first float vectors per row.
book_shape:([] source_time:`timestamp$(); sym:`symbol$(); source:`symbol$(); settlement:`symbol$(); category:`symbol$(); bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:())

/ The sizes to price, in the books' quantity units.
request_shape:([] req_size:`float$())

/ One row per book per requested size: what a sweep of req_size gets on each side.
snapshot_shape:([] source_time:`timestamp$(); sym:`symbol$(); source:`symbol$(); settlement:`symbol$(); category:`symbol$(); req_size:`float$(); bid_price:`float$(); bid_qty:`float$(); bid_full:`boolean$(); ask_price:`float$(); ask_qty:`float$(); ask_full:`boolean$())

/ Snapshots with the window the kind (or the backfill) cut for them.
windowed_shape:update bar_start:`timestamp$() from snapshot_shape

/ One row per book identity, requested size and window.
minute_shape:([] sym:`symbol$(); source:`symbol$(); settlement:`symbol$(); category:`symbol$(); req_size:`float$(); bar_start:`timestamp$(); snapshots:`long$(); bid_obs:`long$(); ask_obs:`long$(); bid_price:`float$(); ask_price:`float$(); bid_qty:`float$(); ask_qty:`float$(); bid_full_ratio:`float$(); ask_full_ratio:`float$())

/ The snapshot width of the minute aggregation. The live job declares it and
/ the twin cuts by it; a backfill window should start on a multiple of it.
width:0D00:01:00

/ How long after a minute's end a snapshot may arrive and still amend its row.
/ A book's source_time can precede its arrival (quote and fx_orderbook keep the
/ original plant stamp), so a few seconds absorb ordinary jitter.
lateness:0D00:00:05

/ Private: whether the usable levels of one ladder are best-first. dir is 1 for
/ asks (prices rise with depth), -1 for bids (they fall). Equal prices are
/ fine. Fewer than two levels are trivially ordered.
/ @private
ordered:{[dir;p]
    if[2>count p; :1b];
    later:1_p;
    earlier:(neg 1)_p;
    $[dir>0; all later>=earlier; all later<=earlier]}

/ Private: (avg price; filled quantity; full) for one side, through
/ .qbook.sweep_price. A side that is not a pair of equal-length lists, or whose
/ usable levels are not best-first, is an empty side - never an error, so one
/ malformed book cannot take a whole batch down, and never a guess.
/ @private
sweep_side:{[dir;prices;sizes;target]
    none:(0n;0f;0b);
    if[(0>type prices) or 0>type sizes; :none];
    if[not (count prices)=count sizes; :none];
    p:"f"$prices;
    q:"f"$sizes;
    usable:.qbook.level_ok[p;q];
    if[not ordered[dir;p where usable]; :none];
    r:.qbook.sweep_price[p;q;target];
    (r`avg_price;r`filled_size;r`fully_filled)}

/ Private: one side of every book for one requested size, as three columns.
/ @private
side_columns:{[dir;ps;zs;target]
    flip {[dir;target;p;z] sweep_side[dir;p;z;target]}[dir;target]'[ps;zs]}

/ Private: every book for one requested size.
/ @private
one_size:{[books;target]
    bid:side_columns[-1;books`bid_prices;books`bid_sizes;target];
    ask:side_columns[1;books`ask_prices;books`ask_sizes;target];
    ([] source_time:books`source_time; sym:books`sym; source:books`source;
        settlement:books`settlement; category:books`category;
        req_size:(count books)#target;
        bid_price:bid 0; bid_qty:bid 1; bid_full:bid 2;
        ask_price:ask 0; ask_qty:ask 1; ask_full:ask 2)}

/ One book per identity and instant: of rows sharing (sym, source, settlement,
/ category, source_time) the one with the greatest content wins, so the result
/ does not depend on delivery order and a redelivered book counts once.
/ @param books rows of book_shape
/ @return rows of book_shape, one per identity and source_time
/ @eg count .qpipe.transform.book_liquidity.one_observation 2#1#.qpipe.transform.book_liquidity.example_books[]  ->  1
one_observation:{[books]
    if[0=count books; :books];
    sorted:`source_time`bid_prices`bid_sizes`ask_prices`ask_sizes xasc books;
    b:0!select bid_prices:last bid_prices, bid_sizes:last bid_sizes, ask_prices:last ask_prices,
        ask_sizes:last ask_sizes by source_time, sym, source, settlement, category from sorted;
    (cols book_shape) xcols b}

/ What a sweep of each requested size gets on each side of each book.
/ .
/ price is the blended (size-weighted) price of the levels the sweep consumed -
/ present for a partial fill too, with qty saying how partial; qty is the
/ quantity actually filled, never more than req_size; full is 1b only when the
/ whole req_size filled. A side with nothing to sweep is (null; 0; 0b).
/ @param books rows of book_shape: sizes in the same units as the requests
/ @param requests rows of request_shape: the sizes to price (each positive and finite)
/ @return rows of snapshot_shape, ordered by source_time, identity and req_size
/ @throws error if a requested size is not positive and finite
/ @eg count .qpipe.transform.book_liquidity.snapshot[.qpipe.transform.book_liquidity.example_books[];([] req_size:1e6 5e6)]  ->  4
snapshot:{[books;requests]
    sizes:distinct requests`req_size;
    if[not all (sizes>0) and sizes<0w; '"snapshot: every requested size must be positive and finite"];
    b:one_observation[books];
    if[(0=count b) or 0=count sizes; :snapshot_shape];
    out:raze one_size[b;] each sizes;
    `source_time`sym`source`settlement`category`req_size xasc out}

/ One row per book identity, requested size and window: the averages over the
/ window's snapshots.
/ .
/   snapshots        how many snapshots fell in the window (withdrawn books included)
/   bid_obs/ask_obs  how many of them priced that side
/   bid_price        mean of the snapshots' prices, over the priced ones only
/                    (null when none - a null is never averaged as a zero)
/   bid_qty          mean available quantity over ALL snapshots; an empty side is
/                    a real zero
/   bid_full_ratio   share of ALL snapshots that filled req_size completely
/ Every snapshot weighs the same: this is NOT a time-weighted average. A window
/ with no snapshot has no row.
/ @param x rows of windowed_shape (snapshots plus bar_start)
/ @return rows of minute_shape
/ @eg count .qpipe.transform.book_liquidity.minute update bar_start:source_time from .qpipe.transform.book_liquidity.snapshot[.qpipe.transform.book_liquidity.example_books[];([] req_size:enlist 1e6)]  ->  2
minute:{[x]
    if[0=count x; :minute_shape];
    / by arrival-independent order: the float sums below then do not depend on it
    x:`source_time xasc x;
    b:0!select snapshots:count i, bid_obs:"j"$sum not null bid_price, ask_obs:"j"$sum not null ask_price,
        bid_price:avg bid_price, ask_price:avg ask_price, bid_qty:avg bid_qty, ask_qty:avg ask_qty,
        bid_full_ratio:avg bid_full, ask_full_ratio:avg ask_full
        by sym, source, settlement, category, req_size, bar_start from x;
    `bar_start`sym`source`settlement`category`req_size xasc (cols minute_shape) xcols b}

/ Two books of the example provider LP_A on EURUSD, spot and unspecified-category,
/ for the examples above and the tests.
/ @return rows of book_shape
/ @eg count .qpipe.transform.book_liquidity.example_books[]  ->  2
example_books:{[]
    ([] source_time:2#2026.10.09D10:00:00; sym:2#`EURUSD; source:`LP_A`LP_B;
        settlement:2#`spot; category:2#`standard;
        bid_prices:(1.0999 1.0998;1.0997 1.0996); bid_sizes:(1e6 2e6;3e6 4e6);
        ask_prices:(1.1001 1.1002;1.1003 1.1004); ask_sizes:(1e6 2e6;3e6 4e6))}

\d .

.qetl.transform.define[`book_liquidity_snapshot;`inputs`output`fn`examples!(
    `books`requests!(.qpipe.transform.book_liquidity.book_shape;.qpipe.transform.book_liquidity.request_shape);
    .qpipe.transform.book_liquidity.snapshot_shape;
    .qpipe.transform.book_liquidity.snapshot;
    / EURUSD at 10:00:00 from one provider, 1m and 4m: the 1m request fills at
    / the best level on both sides; the 4m request meets a ladder of only 3m
    / (1m at 1.1000 and 2m at 1.0999 on the bid), so it is PARTIAL: 3m filled
    / at their blended price, not full. The ask side likewise.
    enlist `inputs`expected!(
        `books`requests!(
            ([] source_time:enlist 2026.10.09D10:00:00; sym:enlist `EURUSD; source:enlist `LP_A;
                settlement:enlist `spot; category:enlist `standard;
                bid_prices:enlist 1.1000 1.0999; bid_sizes:enlist 1e6 2e6;
                ask_prices:enlist 1.1001 1.1002; ask_sizes:enlist 1e6 2e6);
            ([] req_size:1e6 4e6));
        ([] source_time:2#2026.10.09D10:00:00; sym:2#`EURUSD; source:2#`LP_A;
            settlement:2#`spot; category:2#`standard; req_size:1e6 4e6;
            bid_price:(1.1000;(1.1000+2*1.0999)%3); bid_qty:1e6 3e6; bid_full:10b;
            ask_price:(1.1001;(1.1001+2*1.1002)%3); ask_qty:1e6 3e6; ask_full:10b)))];

.qetl.transform.define[`book_liquidity_minute;`inputs`output`fn`examples!(
    enlist[`snapshots]!enlist .qpipe.transform.book_liquidity.windowed_shape;
    .qpipe.transform.book_liquidity.minute_shape;
    .qpipe.transform.book_liquidity.minute;
    / Three EURUSD snapshots of one provider at 1m in the 10:00 minute, and one
    / on the boundary that opens 10:01. In 10:00: bid prices 1.1000, null (a
    / withdrawn side), 1.0990 -> mean over the two priced = 1.0995, bid_obs 2;
    / bid_qty 1m, 0, 1m -> 2/3m (the empty side is a real zero); the full ratio
    / is 2 of 3. The ask side priced all three at 1.1010 1.1020 1.1030.
    enlist `inputs`expected!(
        enlist[`snapshots]!enlist ([] source_time:2026.10.09D10:00:10 2026.10.09D10:00:20 2026.10.09D10:00:50 2026.10.09D10:01:00;
            sym:4#`EURUSD; source:4#`LP_A; settlement:4#`spot; category:4#`standard; req_size:4#1e6;
            bid_price:(1.1000;0n;1.0990;1.2000); bid_qty:1e6 0 1e6 1e6; bid_full:1011b;
            ask_price:1.1010 1.1020 1.1030 1.2010; ask_qty:4#1e6; ask_full:4#1b;
            bar_start:(3#2026.10.09D10:00:00),2026.10.09D10:01:00);
        ([] sym:2#`EURUSD; source:2#`LP_A; settlement:2#`spot; category:2#`standard; req_size:2#1e6;
            bar_start:2026.10.09D10:00:00 2026.10.09D10:01:00; snapshots:3 1; bid_obs:2 1; ask_obs:3 1;
            bid_price:1.0995 1.2000; ask_price:1.1020 1.2010;
            bid_qty:(2e6%3;1e6); ask_qty:1e6 1e6;
            bid_full_ratio:(2%3;1f); ask_full_ratio:1 1f)))];
