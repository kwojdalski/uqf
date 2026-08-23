/ positions.q - a weighted-average-cost position tracker for an FX book,
/ plus per-currency exposure decomposition. Requires risk.q (pnl), ccy.q
/ (ccy_pair_legs/ccy_pair_symbol) and forwards.q (cross_book_at, for
/ ccy_exposure_in's cross-currency revaluation) to be loaded first.
/ .
/ Like every other module here, this is pure: the position book is an
/ explicit keyed table threaded in and out of each function, never a
/ hidden global - apply_fill returns the *updated* table rather than
/ mutating one in place. A live caller (e.g. a TorQ process subscribing to
/ a `trade` table) owns the actual mutable state itself, the same way
/ torq_cross_etl.q/torq_vectorize_etl.q wrap other uqf pure functions with
/ their own local state and glue.

\d .qpos

/ An empty position book: one row per sym once opened, running signed
/ qty, volume-weighted average entry price of the *currently open*
/ position, and cumulative realized P&L (quote currency). Unrealized P&L
/ is never stored - see unrealized_pnl, computed on demand against a
/ current market price.
/ @return an empty keyed table, ready for apply_fill
/ @eg .qpos.empty_book[]
empty_book:{[] ([sym:`symbol$()] qty:`float$(); avg_price:`float$(); realized_pnl:`float$())};

/ Apply one fill to a position book, weighted-average-cost style:
/ - opening from flat, or adding to a position in the same direction:
/   avg_price becomes the size-weighted average of the old position and
/   this fill; no P&L realized.
/ - reducing a position without flipping it: the closed slice realizes
/   P&L (via risk.q's pnl, at the *old* avg_price) using notional slice = fill_qty; avg_price is unchanged
/   for what remains open.
/ - closing a position exactly flat, or flipping it past flat: the whole
/   old position is realized at this fill's price; if the fill was large
/   enough to flip, the excess opens a new position *at this same fill
/   price* (the standard convention - a reversing trade closes the old
/   side and opens the new one at one execution price).
/ @param pos a position book (see empty_book) - not mutated, the updated
/   book is returned
/ @param sym the symbol filled
/ @param qty fill size, base currency units (unsigned - see side)
/ @param price the fill price
/ @param side 1 for a buy, -1 for a sell
/ @return pos with sym's row updated (or added, if new)
/ @eg .qpos.apply_fill[.qpos.empty_book[];`EURUSD;1000000;1.1000;1] -> qty 1000000, avg_price 1.1, realized_pnl 0
/ @eg .qpos.apply_fill[b;`EURUSD;400000;1.1050;-1] (b = book from the previous example) -> qty 600000, avg_price 1.1 (unchanged), realized_pnl 2000 (400000 closed at a 50-pip gain)
apply_fill:{[pos;sym;qty;price;side]
    / exec sym from pos, not key pos: PeachQ returns a 1-col table (not a
    / plain vector) from `key` on a single-key-column keyed table, which
    / breaks `in` - exec gives a portable plain symbol vector on both
    / PeachQ and real kdb+/KDB-X.
    old:$[sym in exec sym from pos; pos sym; `qty`avg_price`realized_pnl!0 0 0f];
    old_qty:old`qty;
    old_avg:old`avg_price;
    fill_qty:side*qty;
    new_qty:old_qty+fill_qty;
    same_dir:(old_qty=0f) or (signum[old_qty]=signum[new_qty]);
    increasing:same_dir and (abs new_qty)>=abs old_qty;
    result:$[
        increasing;
            / opening or adding: weighted-average price, nothing realized yet
            `qty`avg_price`realized_pnl!(
                new_qty;
                $[old_qty=0f; price; ((old_qty*old_avg)+(fill_qty*price))%new_qty];
                old`realized_pnl);
        same_dir;
            / partial reduce, same direction: realize P&L on the closed
            / slice at the *old* avg_price; avg_price stays put for the rest
            `qty`avg_price`realized_pnl!(
                new_qty;
                old_avg;
                old[`realized_pnl]+.qrisk.pnl[abs fill_qty;old_avg;price;signum old_qty]);
        / else: exact close (new_qty=0) or a flip past flat - either way
        / the whole old position closes here, at this fill's price
        `qty`avg_price`realized_pnl!(
            new_qty;
            $[new_qty=0f; 0f; price];
            old[`realized_pnl]+.qrisk.pnl[abs old_qty;old_avg;price;signum old_qty])
    ];
    pos upsert enlist `sym`qty`avg_price`realized_pnl!(sym;result`qty;result`avg_price;result`realized_pnl)};

/ Mark-to-market unrealized P&L of sym's currently open position, in quote
/ currency - built on risk.q's own pnl formula (side/notional decomposed
/ from the book's signed qty) rather than reimplementing it.
/ @param pos a position book (see empty_book)
/ @param sym the symbol to mark
/ @param mkt_price the current market price to mark against
/ @return unrealized P&L, in quote currency (0 if sym isn't in pos or is flat)
/ @eg .qpos.unrealized_pnl[b;`EURUSD;1.1100] (b: 600000 EURUSD @ 1.1000) -> 6000
unrealized_pnl:{[pos;sym;mkt_price]
    if[not sym in exec sym from pos; :0f];
    row:pos sym;
    .qrisk.pnl[abs row`qty;row`avg_price;mkt_price;signum row`qty]};

/ Total P&L (realized + mark-to-market unrealized) of sym's position.
/ @param pos a position book (see empty_book)
/ @param sym the symbol to mark
/ @param mkt_price the current market price to mark against
/ @return total P&L, in quote currency
/ @eg .qpos.total_pnl[b;`EURUSD;1.1100]
total_pnl:{[pos;sym;mkt_price]
    realized:$[sym in exec sym from pos; pos[sym]`realized_pnl; 0f];
    realized+unrealized_pnl[pos;sym;mkt_price]};

/ Decompose one pair position into its two currency legs, in *fixed*,
/ dealt-rate notional terms - not marked to current market. Spot FX legs
/ settle at the rate they were actually dealt at, so avg_price (the
/ book's own cost basis), not some current price, is what's economically
/ real about the quote-currency amount - that's what makes this
/ decomposition well-defined without needing a market price at all.
/ @param sym the pair traded (any format ccy.q's normalize_ccy_pair accepts)
/ @param qty signed position size, in the pair's base currency units
/ @param avg_price the position's average entry/cost-basis rate
/ @return a 2-row table `ccy`amount - the base currency's leg is +qty,
/   the quote currency's leg is -(qty*avg_price)
/ @eg .qpos.ccy_legs[`EURAUD;1000000;1.6000] -> (`EUR;1000000f), (`AUD;-1600000f)
ccy_legs:{[sym;qty;avg_price]
    legs:.qccy.ccy_pair_legs sym;
    ([] ccy:(legs`base;legs`quote); amount:(qty;neg qty*avg_price))};

/ Net exposure per currency across an entire position book - decomposes
/ every row via ccy_legs (fixed, dealt-rate terms) and sums by currency,
/ so a currency touched by several different pairs (majors and crosses
/ alike) nets correctly into one number. Raw currency-unit amounts, not
/ comparable to each other across currencies - see ccy_exposure_in to
/ revalue everything into one reporting currency.
/ @param pos a position book (see empty_book)
/ @return a table `ccy`amount, one row per currency touched by the book
/ @eg .qpos.ccy_exposure[b] (b: long 1mm EURAUD @ 1.60, long 500k AUDUSD @ 0.65) -> AUD: -1,600,000+500,000 = -1,100,000; EUR: 1,000,000; USD: -325,000
ccy_exposure:{[pos]
    legs:raze {[row] ccy_legs[row`sym;row`qty;row`avg_price]} each 0!pos;
    select amount:sum amount by ccy from legs};

/ Net exposure per currency (see ccy_exposure), revalued into one
/ reporting currency via forwards.q's own cross-currency chaining
/ (cross_book_at) - so a currency with no *direct* quote against the
/ reporting currency (e.g. PLN needing PLN->EUR->USD, exactly the
/ AUDPLN-needs-three-legs case cross_book_at itself already handles for
/ pricing a single pair) still resolves, using whatever pairs are
/ actually available in quotes. Uses mid, not bid/ask, since this is an
/ exposure report, not an executable price.
/ @param pos a position book (see empty_book)
/ @param quotes a depth-aware quotes table - forwards.q's own
/   `ts`sym`bid_prices`bid_sizes`ask_prices`ask_sizes shape (require_quotes_cols),
/   sorted `sym`ts xasc
/ @param reporting_ccy the currency to convert every leg into, e.g. `USD
/ @param at_time only consider quotes at or before this time
/ @return a table `ccy`amount`reporting_amount - amount is the raw
/   currency-unit exposure (see ccy_exposure), reporting_amount is that
/   same exposure converted into reporting_ccy at the chain's mid price
/   (reporting_ccy's own row converts at 1, trivially)
/ @throws error (from cross_book_at) if some currency has no chain of
/   available pairs connecting it to reporting_ccy in quotes
/ @eg .qpos.ccy_exposure_in[b;quotes;`USD;.z.p]
ccy_exposure_in:{[pos;quotes;reporting_ccy;at_time]
    exposure:ccy_exposure pos;
    convert_one:{[quotes;reporting_ccy;at_time;ccy;amount]
        if[ccy=reporting_ccy; :amount];
        pair:.qccy.ccy_pair_symbol[ccy;reporting_ccy];
        mid:first exec mid from .qfwd.cross_book_at[quotes;pair;at_time;enlist abs amount;enlist `mid];
        amount*mid};
    update reporting_amount:convert_one[quotes;reporting_ccy;at_time]'[ccy;amount] from exposure};

\d .
