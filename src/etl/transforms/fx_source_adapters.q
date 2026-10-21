/ fx_source_adapters.q - two source adapters onto the canonical contracts
/ (.qpipe.transform.fx_source_adapters, #1395).
/ .
/ WHAT AN ADAPTER IS. A declared .qetl.transform from one source's table to
/ `executions` or `market_data`, written the way executions.q and market_data.q
/ write theirs, so the normalizer shell holds it to the canonical table, column
/ for column and type for type. It selects what is eligible in ITS source,
/ names and signs what that source calls things, keeps the source's own times
/ and identifiers, and refuses what it cannot represent. It owns no queue, no
/ reference-price choice and no markout arithmetic: those are the horizon
/ kind's and src/etl/transforms/fx_markout.q's, and the same calculation scores
/ whatever an adapter produces.
/ .
/ WHY TWO, AND WHY THESE. Each is a generic analogue of a kind of source a site
/ has; neither is a port of any real one (see demo_deals.q for why that
/ distinction is kept). They are shaped to differ from the tables the stack
/ already normalizes, so that "a new source needs only an adapter" is exercised
/ rather than asserted:
/ .
/   deal tape      one row per deal EVENT, not per fill: an individual fill
/                  carries its own quantity (last_qty), while an order-status
/                  row carries the order's CUMULATIVE quantity (cum_qty).
/                  Summing both would count a fill twice, so only the fills are
/                  executions. Its identifier is a 19-digit integer, kept exact.
/   book levels    one row per LEVEL, not per snapshot, tagged with a liquidity
/                  category and a settlement code. Only the full-amount spot
/                  levels are tradable reference liquidity; the rest are not
/                  this contract's. Reshaped to one snapshot per (pair, source,
/                  time), best level first.
/ .
/ ELIGIBILITY IS NOT MALFORMATION. A row the source legitimately carries but the
/ contract is not about (a status update, a forward, an equity) is DROPPED. A row
/ that claims to be what the contract is about and cannot be (a fill with an
/ unknown side, a level with a negative price) is REFUSED, by name and count: a
/ vendor changing a code is a break to see, not rows to lose.
/ .
/ Time stays the source's. deal_time and ts become source_time; the plant's
/ receipt `time` is not an input.

\d .

/ ------------------------------------------------------------- THE SOURCES

/ The deal tape as its feed delivers it. exec_type is `fill (an individual
/ fill, last_qty is its size), `status (an order update, cum_qty is the order's
/ running total) or `cancel. side is `B or `S.
.qpipe.transform.fx_source_adapters.deal_tape:([] id:`long$(); order_id:`long$(); deal_time:`timestamp$(); security_code:`symbol$();
    venue_code:`symbol$(); exec_type:`symbol$(); side:`symbol$(); price:`float$();
    last_qty:`float$(); cum_qty:`float$())

/ The order book as a feed of levels. side is `bid or `ask, lvl 0 the best, a
/ null price (with a null or zero quantity) marks a side the feed has emptied. category
/ `FULL_AMOUNT is liquidity for the whole quoted size; settle `SP and `T2 are
/ spot.
.qpipe.transform.fx_source_adapters.book_levels:([] ts:`timestamp$(); instrument:`symbol$(); feed:`symbol$(); category:`symbol$();
    settle:`symbol$(); side:`symbol$(); lvl:`long$(); px:`float$(); qty:`float$())

/ The deal tape's sides, and the one category and the settlement codes the book keeps.
.qpipe.transform.fx_source_adapters.tape_sides:`B`S
.qpipe.transform.fx_source_adapters.book_category:`FULL_AMOUNT
.qpipe.transform.fx_source_adapters.spot_codes:`SP`T2

/ The canonical outputs, from the tables that own them.
.qpipe.transform.fx_source_adapters.executions:.qetl.plant.published `executions
.qpipe.transform.fx_source_adapters.market_data:.qetl.plant.published `market_data

/ --------------------------------------------------------------- HELPERS

/ The canonical pair a source's instrument code names, or null when it names none:
/ separators and case are the source's spelling, not the pair's.
/ @param codes instrument codes, e.g. `$"EUR/USD"
/ @return canonical CURCUR symbols, null where the code is not a currency pair
/ @eg .qpipe.transform.fx_source_adapters.pairs_of[(`$"EUR/USD";`AAPL;`$"usd-jpy")]  ->  `EURUSD``USDJPY
.qpipe.transform.fx_source_adapters.pairs_of:{[codes]
    if[0=count codes; :`symbol$()];
    u:distinct codes;
    cleaned:{[c] upper (string c) except "/-_ "} each u;
    ok:.qf.px.is_ccy_pair each cleaned;
    (u!?[ok;`$cleaned;`]) codes}

/ Is each value a positive, finite number?
/ @param x floats
/ @return a boolean per value; a null is not
/ @eg .qpipe.transform.fx_source_adapters.positive[1.1 0 0n 0w -1]  ->  10000b
.qpipe.transform.fx_source_adapters.positive:{[x] (x>0f) & x<0w}

/ Refuse rows by name: how many, what is wrong, and the first offending value.
/ @param who the source
/ @param what what is wrong with the rows, e.g. "with no id"
/ @param shown the value to show for each row
/ @param bad a boolean per row, 1b where it is wrong
/ @throws error when any row is wrong
/ @private
.qpipe.transform.fx_source_adapters.refuse:{[who;what;shown;bad]
    if[any bad; '"fx_source_adapters: ",who," has ",string[sum bad]," fill/level row(s) ",what," - first: ",.Q.s1 first shown where bad];
    }

/ ----------------------------------------------------------- THE DEAL TAPE

/ Individual fills of a deal tape as canonical executions.
/ .
/ Only `fill rows are executions. `status rows carry cum_qty, a running total of
/ the order, and `cancel rows undo nothing this contract models: both are dropped,
/ so the sizes of an order's fills sum to its final cum_qty. Instruments that are
/ not a currency pair are dropped. A fill with an unknown side, a price or a
/ last_qty that is not positive and finite, or no id, is refused. The id is the
/ deal id's decimal digits, from the integer: a float would round 19 digits.
/ @param batch a deal_tape batch
/ @return canonical executions, one per eligible fill, venue the feed's own code
/ @throws error naming a fill that cannot be represented
/ @eg count .qpipe.transform.fx_source_adapters.from_deal_tape[.qpipe.transform.fx_source_adapters.deal_tape]  ->  0
.qpipe.transform.fx_source_adapters.from_deal_tape:{[batch]
    pair:.qpipe.transform.fx_source_adapters.pairs_of[batch`security_code];
    taken:select from (update pair:pair from batch) where exec_type=`fill, not null pair;
    .qpipe.transform.fx_source_adapters.refuse["deal_tape";"with a side other than B or S";taken`side;not taken[`side] in .qpipe.transform.fx_source_adapters.tape_sides];
    .qpipe.transform.fx_source_adapters.refuse["deal_tape";"with a price that is not positive and finite";taken`price;not .qpipe.transform.fx_source_adapters.positive[taken`price]];
    .qpipe.transform.fx_source_adapters.refuse["deal_tape";"with a last_qty that is not positive and finite";taken`last_qty;not .qpipe.transform.fx_source_adapters.positive[taken`last_qty]];
    .qpipe.transform.fx_source_adapters.refuse["deal_tape";"with no id";taken`order_id;null taken`id];
    if[0=count taken; :0#.qpipe.transform.fx_source_adapters.executions];
    select source_time:deal_time, sym:pair, venue:venue_code, side:?[side=`B;1;-1], size:last_qty, price,
        fee:0f, fee_ccy:`, fill_id:`$string id, book:`, product:` from taken}

/ ---------------------------------------------------------- THE BOOK LEVELS

/ One side of one snapshot: the live levels of a side, best first.
/ @private
.qpipe.transform.fx_source_adapters.ladder:{[levels;key_row;want]
    lv:select px, qty from levels where pair=key_row`sym, feed=key_row`source, ts=key_row`source_time, side=want;
    lv:$[want=`bid; `px xdesc lv; `px xasc lv];
    `px`qty!(lv`px;lv`qty)}

/ Full-amount spot levels of an order book feed as canonical market_data snapshots.
/ .
/ One snapshot per (pair, feed, ts), ladders best level first: bids by falling
/ price, asks by rising. A side with no level - an emptied side, or one the
/ snapshot does not list - is an EMPTY ladder, the withdrawal market_data
/ documents, never a price. Levels of another category or settlement, and
/ instruments that are not a currency pair, are dropped; a snapshot all of whose
/ levels were dropped is not published. A priced level of zero quantity is a
/ withdrawn one and is dropped, never the best level. A level with a price that is not positive
/ and finite (a null price with a null or zero quantity is the emptied-side marker),
/ a price with no quantity, a negative quantity or an unknown side is refused.
/ @param batch a book_levels batch, whole snapshots
/ @return canonical market_data, market `fx
/ @throws error naming a level that cannot be represented
/ @eg count .qpipe.transform.fx_source_adapters.from_book_levels[.qpipe.transform.fx_source_adapters.book_levels]  ->  0
.qpipe.transform.fx_source_adapters.from_book_levels:{[batch]
    pair:.qpipe.transform.fx_source_adapters.pairs_of[batch`instrument];
    wanted:.qpipe.transform.fx_source_adapters.book_category;
    spot:.qpipe.transform.fx_source_adapters.spot_codes;
    kept:select from (update pair:pair from batch) where category=wanted, settle in spot, not null pair;
    .qpipe.transform.fx_source_adapters.refuse["book_levels";"with a side other than bid or ask";kept`side;not kept[`side] in `bid`ask];
    .qpipe.transform.fx_source_adapters.refuse["book_levels";"with a negative quantity";kept`qty;(not null kept`qty) & kept[`qty]<0f];
    marker:null[kept`px] & (null[kept`qty] | 0f>=kept`qty);
    .qpipe.transform.fx_source_adapters.refuse["book_levels";"with a price but no quantity";kept`px;(not null kept`px) & null kept`qty];
    .qpipe.transform.fx_source_adapters.refuse["book_levels";"with a price that is not positive and finite";kept`px;(not marker) & not .qpipe.transform.fx_source_adapters.positive[kept`px]];
    / a zero-quantity level is a withdrawn one (book_levels): priced or not, it is no liquidity and no top of book
    levels:select from kept where not marker, not qty=0f;
    if[0=count kept; :0#.qpipe.transform.fx_source_adapters.market_data];
    snaps:0!select by sym:pair, source:feed, source_time:ts from kept;
    n:count snaps;
    rows:{[levels;snaps;want] .qpipe.transform.fx_source_adapters.ladder[levels;;want] each snaps}[levels;snaps];
    bids:rows[`bid];
    asks:rows[`ask];
    ([] sym:snaps`sym; source:snaps`source; market:n#`fx; source_time:snaps`source_time;
        bid_prices:bids@\:`px; bid_sizes:bids@\:`qty; ask_prices:asks@\:`px; ask_sizes:asks@\:`qty)}

\d .

/ ------------------------------------------------------------ THE TRANSFORMS

/ The deal tape's mapping, declared. Worked by hand: two fills of order 7 whose
/ ids differ only in the last digit (kept apart), a fill on a JPY pair, and the
/ rows that are not executions - the order's cumulative status row, a cancel and
/ an equity fill - which are dropped.
.qetl.transform.define[`executions_from_deal_tape;`inputs`output`fn`examples!(
    (enlist `deal_tape)!enlist .qpipe.transform.fx_source_adapters.deal_tape;
    .qpipe.transform.fx_source_adapters.executions;
    .qpipe.transform.fx_source_adapters.from_deal_tape;
    enlist `inputs`expected!(
        (enlist `deal_tape)!enlist ([] id:1234567890123456789 1234567890123456790 1005 3 4 2j;
            order_id:7 7 7 8 9 10j;
            deal_time:2026.09.17D10:00:00 2026.09.17D10:00:01 2026.09.17D10:00:01 2026.09.17D10:00:02 2026.09.17D10:00:02 2026.09.17D10:00:03;
            security_code:(`$"EUR/USD";`$"eur-usd";`$"EUR/USD";`$"EUR/USD";`AAPL;`$"USD/JPY");
            venue_code:6#`fx; exec_type:`fill`fill`status`cancel`fill`fill; side:`B`B`B`S`B`S;
            price:1.1001 1.1002 1.1002 1.1003 190.0 150.01;
            last_qty:6e5 4e5 0n 1e5 10.0 1e5; cum_qty:6e5 1e6 1e6 1e5 10.0 1e5);
        ([] source_time:2026.09.17D10:00:00 2026.09.17D10:00:01 2026.09.17D10:00:03;
            sym:`EURUSD`EURUSD`USDJPY; venue:3#`fx; side:1 1 -1; size:6e5 4e5 1e5;
            price:1.1001 1.1002 150.01; fee:3#0f; fee_ccy:3#`; fill_id:`1234567890123456789`1234567890123456790`2;
            book:3#`; product:3#`)))];

/ The book levels' mapping, declared. Worked by hand: LPA's snapshot arrives with
/ its levels out of order and comes out best first; its partial-amount bid and
/ forward ask are dropped; LPB emptied its bid (a marker row) and keeps an ask;
/ an equity level is dropped.
.qetl.transform.define[`market_data_from_book_levels;`inputs`output`fn`examples!(
    (enlist `book_levels)!enlist .qpipe.transform.fx_source_adapters.book_levels;
    .qpipe.transform.fx_source_adapters.market_data;
    .qpipe.transform.fx_source_adapters.from_book_levels;
    enlist `inputs`expected!(
        (enlist `book_levels)!enlist ([] ts:9#2026.09.17D10:00:00;
            instrument:(8#`$"EUR/USD"),`AAPL; feed:`LPA`LPA`LPA`LPA`LPA`LPA`LPB`LPB`LPA;
            category:`FULL_AMOUNT`FULL_AMOUNT`FULL_AMOUNT`FULL_AMOUNT`PARTIAL`FULL_AMOUNT`FULL_AMOUNT`FULL_AMOUNT`FULL_AMOUNT;
            settle:`SP`SP`SP`SP`SP`1W`T2`T2`SP;
            side:`bid`ask`bid`ask`bid`ask`bid`ask`bid; lvl:1 1 0 0 0 0 0 0 0;
            px:1.0998 1.1002 1.0999 1.1001 1.05 1.09 0n 1.1005 190.0;
            qty:2e6 2e6 1e6 1e6 9e6 9e6 0.0 1e6 1.0);
        ([] sym:2#`EURUSD; source:`LPA`LPB; market:2#`fx; source_time:2#2026.09.17D10:00:00;
            bid_prices:(1.0999 1.0998;`float$()); bid_sizes:(1e6 2e6;`float$());
            ask_prices:(1.1001 1.1002;enlist 1.1005); ask_sizes:(1e6 2e6;enlist 1e6))))];
