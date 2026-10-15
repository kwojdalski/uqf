/ book_liquidity.q - source-independent liquidity analytics over normalized books (#1397).
/ .
/ Two PURE steps, each a declared transform, and two ADAPTERS that feed them:
/ .
/   book_liquidity_snapshot   books + requested sizes -> what each book can fill
/   book_liquidity_minute     those snapshots + bar_start -> one row per minute
/ .
/ The pure steps read the `liquidity_book` contract below and know nothing of
/ where a book came from: no table names, no IPC, no credentials, no cursor. How
/ a source's rows become that contract is an ADAPTER's job (the second half of
/ this file); which window of rows arrives is the job's (streaming bars, or a
/ backfill). One file, because a declaration may not depend on another file of
/ its directory at load time (src/etl/init.q): the adapters declare transforms
/ over the contract's shape.
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
/                .qf.mkt.level_ok (inside .qf.mkt.sweep_price).
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
/ .qf.mkt.sweep_price. A side that is not a pair of equal-length lists, or whose
/ usable levels are not best-first, is an empty side - never an error, so one
/ malformed book cannot take a whole batch down, and never a guess.
/ @private
sweep_side:{[dir;prices;sizes;target]
    none:(0n;0f;0b);
    if[(0>type prices) or 0>type sizes; :none];
    if[not (count prices)=count sizes; :none];
    p:"f"$prices;
    q:"f"$sizes;
    usable:.qf.mkt.level_ok[p;q];
    if[not ordered[dir;p where usable]; :none];
    r:.qf.mkt.sweep_price[p;q;target];
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

/ Two providers' spot, standard-category books of EURUSD at one instant, for the
/ examples above.
/ @return rows of book_shape
/ @eg count .qpipe.transform.book_liquidity.example_books[]  ->  2
example_books:{[]
    ([] source_time:2#2026.10.09D10:00:00; sym:2#`EURUSD; source:`LP_A`LP_B;
        settlement:2#`spot; category:2#`standard;
        bid_prices:(1.0999 1.0998;1.0997 1.0996); bid_sizes:(1e6 2e6;3e6 4e6);
        ask_prices:(1.1001 1.1002;1.1003 1.1004); ask_sizes:(1e6 2e6;3e6 4e6))}

/ ------------------------------------------------------------- ADAPTERS
/ .
/ How a source's rows become the liquidity_book contract (#1397).
/ .
/ An adapter is the ONE place that knows a source's shape. It preserves what the
/ pure steps (book_liquidity.q) need - event time, instrument, the independent
/ liquidity provider, best-first float ladders, and the eligibility dimensions
/ (settlement, category) - and it decides what is eligible at all. Transport,
/ credentials, cursors and table names stay in the source and job declarations.
/ .
/ TWO SHAPES, ONE CONTRACT:
/   liquidity_book_from_market_data   the existing feed: one row per book, ladders
/                                     in columns, provider in `source`
/   liquidity_book_from_levels        one row per LADDER LEVEL, carrying provider,
/                                     physical table label, settlement and category
/ Equivalent books through either give identical liquidity.
/ .
/ A row that is malformed is DROPPED, never repaired: a null identity or time, a
/ ladder that is not a pair of equal-length lists. An adapter is pure and cannot
/ log, so the job's batch counts are where a drop would show.

/ market_data's `market` values the liquidity analytics are about. Requested
/ sizes are in base-currency units (millions of a currency), which only an FX
/ pair's book is quoted in; a crypto book is in coins.
eligible_markets:enlist `fx

/ What market_data cannot say about a book: its settlement and category. They are
/ not guessed - a book with a single, undifferentiated population carries this.
unspecified:`unspecified

/ The settlements and categories the levels adapter keeps. EMPTY means all, kept
/ apart; a site wanting only spot, or only one book category, sets these to filter
/ in the adapter instead of collapsing populations downstream.
eligible_settlements:`symbol$()
eligible_categories:`symbol$()

/ The levels feed quotes quantity in millions of the base currency.
qty_scale:1e6

/ The levels feed's rows: one per ladder level.
/ table_label is the physical table the row was read from. It is recorded by the
/ feed and deliberately NOT part of a book's identity: provider is.
level_shape:([] source_time:`timestamp$(); sym:`symbol$(); provider:`symbol$(); table_label:`symbol$(); settlement:`symbol$(); category:`symbol$(); side:`symbol$(); price:`float$(); qty:`float$())

/ The market_data shape the first adapter reads.
market_data_shape:([] source_time:`timestamp$(); sym:`symbol$(); source:`symbol$(); market:`symbol$(); bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:())

/ Private: whether a ladder side is a pair of equal-length lists.
/ @private
aligned:{[px;sz] (0h<=type px) and (0h<=type sz) and (count px)=count sz}

/ market_data books as liquidity_book rows.
/ .
/ Only FX books (eligible_markets) pass. The event time is market_data's
/ source_time - the source's own stamp, never the plant's receipt time - and the
/ provider is `source`. settlement and category are `unspecified`: market_data
/ holds one population per (sym, source). Rows with a null time, sym or source,
/ or a ladder side that is not an aligned pair of lists, are dropped.
/ @param x rows of market_data_shape
/ @return rows of book_shape
/ @eg count .qpipe.transform.book_liquidity.from_market_data 0#.qpipe.transform.book_liquidity.market_data_shape  ->  0
from_market_data:{[x]
    if[0=count x; :0#book_shape];
    well_formed:not (null x`source_time) or (null x`sym) or null x`source;
    bids:aligned'[x`bid_prices;x`bid_sizes];
    asks:aligned'[x`ask_prices;x`ask_sizes];
    keep:(x[`market] in eligible_markets) and well_formed and bids and asks;
    t:x where keep;
    (cols book_shape) xcols ([] source_time:t`source_time; sym:t`sym; source:t`source;
        settlement:(count t)#unspecified; category:(count t)#unspecified;
        bid_prices:t`bid_prices; bid_sizes:t`bid_sizes; ask_prices:t`ask_prices; ask_sizes:t`ask_sizes)}

/ Level rows as liquidity_book rows: the ladders are rebuilt from the levels.
/ .
/ A book is (source_time, sym, provider, settlement, category); its table_label
/ is ignored, so one provider's rows read from two tables are one book. Per side:
/ levels with a null, non-positive or infinite price or quantity are not levels;
/ one level per price (a repeated price is a redelivery, the last row wins, they
/ are not summed); bids are ordered best-first, highest price first, asks lowest
/ first - the ladder's order is derived from the prices, not trusted from the feed.
/ Quantities are scaled to base-currency units once, here. A book whose rows are
/ all invalid is a WITHDRAWN book: it stays, with empty ladders, so the
/ liquidity analytics see zero liquidity instead of no observation. Rows with a
/ null identity or time, or a side other than bid/ask, are dropped. The settlement
/ and category filters (eligible_settlements, eligible_categories) apply FIRST,
/ before anything is built, so an excluded population leaves no trace.
/ @param x rows of level_shape
/ @return rows of book_shape
/ @eg count .qpipe.transform.book_liquidity.from_levels 0#.qpipe.transform.book_liquidity.level_shape  ->  0
from_levels:{[x]
    well_formed:not (null x`source_time) or (null x`sym) or (null x`provider) or (null x`settlement) or null x`category;
    sided:(x`side) in `bid`ask;
    wanted_s:$[count eligible_settlements; (x`settlement) in eligible_settlements; (count x)#1b];
    wanted_c:$[count eligible_categories; (x`category) in eligible_categories; (count x)#1b];
    t:x where well_formed and sided and wanted_s and wanted_c;
    if[0=count t; :0#book_shape];
    t:update level:.qf.mkt.level_ok[price;qty] from t;
    / one row per level price within a book side, the last delivered winning
    t:0!select level:last level, qty:last qty by source_time, sym, source:provider, settlement, category, side, price from t;
    / a local: a qSQL expression resolves a global against the session's namespace, not this one
    scale:qty_scale;
    t:`price xasc update qty:qty*scale from t;
    t:update bids:level and side=`bid, asks:level and side=`ask from t;
    b:0!select bid_prices:(reverse price where bids), bid_sizes:(reverse qty where bids),
        ask_prices:(price where asks), ask_sizes:(qty where asks)
        by source_time, sym, source, settlement, category from t;
    `source_time`sym`source`settlement`category xasc (cols book_shape) xcols b}

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


.qetl.transform.define[`liquidity_book_from_market_data;`inputs`output`fn`examples!(
    enlist[`market_data]!enlist .qpipe.transform.book_liquidity.market_data_shape;
    .qpipe.transform.book_liquidity.book_shape;
    .qpipe.transform.book_liquidity.from_market_data;
    / An FX book from LP_A passes with the source's own time and the unspecified
    / dimensions; a crypto book is not about base-currency sizes; a book whose bid
    / ladder is ragged is malformed and dropped.
    enlist `inputs`expected!(
        enlist[`market_data]!enlist ([] source_time:3#2026.10.09D10:00:00; sym:`EURUSD`BTCUSD`EURUSD;
            source:`LP_A`binance`LP_B; market:`fx`crypto`fx;
            bid_prices:(1.0999 1.0998;enlist 61999f;1.0997 1.0996); bid_sizes:(1e6 2e6;enlist 1f;enlist 3e6);
            ask_prices:(1.1001 1.1002;enlist 62001f;1.1003 1.1004); ask_sizes:(1e6 2e6;enlist 1f;3e6 4e6));
        ([] source_time:enlist 2026.10.09D10:00:00; sym:enlist `EURUSD; source:enlist `LP_A;
            settlement:enlist `unspecified; category:enlist `unspecified;
            bid_prices:enlist 1.0999 1.0998; bid_sizes:enlist 1e6 2e6;
            ask_prices:enlist 1.1001 1.1002; ask_sizes:enlist 1e6 2e6)))];

.qetl.transform.define[`liquidity_book_from_levels;`inputs`output`fn`examples!(
    enlist[`levels]!enlist .qpipe.transform.book_liquidity.level_shape;
    .qpipe.transform.book_liquidity.book_shape;
    .qpipe.transform.book_liquidity.from_levels;
    / LP_A's spot FULL_AMOUNT book at 10:00:00 as level rows (quantity in millions,
    / out of order, read from two physical tables) becomes one book with best-first
    / ladders in base units; the zero-quantity level is not a level. LP_A's FWD book
    / at the same instant is a separate book, with an empty ask side.
    enlist `inputs`expected!(
        enlist[`levels]!enlist ([] source_time:6#2026.10.09D10:00:00; sym:6#`EURUSD;
            provider:6#`LP_A; table_label:`T1`T2`T1`T2`T1`T1; settlement:`SPOT`SPOT`SPOT`SPOT`FWD`SPOT;
            category:6#`FULL_AMOUNT;
            side:`ask`bid`bid`ask`bid`bid; price:1.1002 1.0998 1.0999 1.1001 1.0990 1.0997;
            qty:2 2 1 1 5 0f);
        ([] source_time:2#2026.10.09D10:00:00; sym:2#`EURUSD; source:2#`LP_A;
            settlement:`FWD`SPOT; category:2#`FULL_AMOUNT;
            bid_prices:(enlist 1.0990;1.0999 1.0998); bid_sizes:(enlist 5e6;1e6 2e6);
            ask_prices:(`float$();1.1001 1.1002); ask_sizes:(`float$();1e6 2e6))))];

/ ------------------------------------------------------- THE ONE-MINUTE JOB
/ .
/ What the streaming job book_liquidity_1m and its backfill twin
/ hdb_book_liquidity_1m_backfill both apply: market_data rows in, one-minute
/ liquidity rows out, through the steps above and nothing else - the market_data
/ adapter, the snapshot, the window cut, the minute average. They write
/ book_liquidity_1m under one key, (sym; source; settlement; category; req_size;
/ bar_start), and a backfill REPLACES the live job's rows, so they share this one
/ function and this one `width`.

\d .qpipe.transform.book_liquidity_1m

/ The window width. The live job declares it and the twin cuts by it; a backfill
/ window should start on a multiple of it, or its first and last rows are partial.
width:.qpipe.transform.book_liquidity.width

/ How long after a minute's end a book may arrive and still amend its row. Later
/ ones are dropped and logged by the bars kind, not folded in; a backfill reads
/ the whole window and so includes them.
lateness:.qpipe.transform.book_liquidity.lateness

/ The sizes priced, in market_data's units (base currency) - FX books only,
/ which the adapter enforces. One row per size per provider per minute.
sizes:1e6 5e6 10e6

/ One row per provider, size and minute from a batch of market_data rows: the
/ adapter keeps the eligible, well-formed FX books, the snapshot prices each at
/ every size, the minute averages them. bar_start on the input is not read: the
/ window is cut here from the snapshot's source_time, by the same function the
/ bars kind uses, so a live and a backfilled book land in one window.
/ @param x market_data rows: source_time, sym, source, market and the four ladders (any bar_start is ignored)
/ @return rows of .qpipe.transform.book_liquidity.minute_shape
/ @eg count .qpipe.transform.book_liquidity_1m.bars 0#.qpipe.transform.book_liquidity.market_data_shape  ->  0
bars:{[x]
    books:.qpipe.transform.book_liquidity.from_market_data[x];
    snaps:.qpipe.transform.book_liquidity.snapshot[books;([] req_size:sizes)];
    .qpipe.transform.book_liquidity.minute[.qetl.job.stream.bars.assign[width;`source_time;snaps]]}

\d .
