/ book_liquidity_adapters.q - how a source's rows become the liquidity_book contract (#1397).
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

\d .qpipe.transform.book_liquidity

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
    t:update level:.qbook.level_ok[price;qty] from t;
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
