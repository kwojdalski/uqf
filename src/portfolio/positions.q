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
/ @eg .qpos.apply_fill[.qpos.apply_fill[.qpos.empty_book[];`EURUSD;1000000;1.1000;1];`EURUSD;400000;1.1050;-1] -> qty 600000, avg_price 1.1 (unchanged), realized_pnl 2000 (400000 closed at a 50-pip gain)
apply_fill:{[pos;sym;qty;price;side]
    / exec sym from pos, not key pos: `key` on a single-key-column keyed
    / table does not always give a plain symbol vector, and `in` against
    / anything else fails. exec says exactly what is wanted, so it is kept
    / for clarity rather than for portability.
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

/ Fold a whole trades table through apply_fill, in time order, to build up
/ a position book from history - the many-fills counterpart to apply_fill's
/ single fill. trades only needs to have these four columns (by name, in
/ any order, alongside whatever else the caller's trades table carries -
/ e.g. execution.q's markout_at_horizons shape, or a superset of it like
/ env/schemas.q's .envschema.trades); positions.q has no dependency on
/ where the table actually comes from.
/ @param pos a position book (see empty_book) to fold trades into - not
/   mutated, the updated book is returned
/ @param trades a table with at least sym (symbol), size (fill qty, base
/   currency units, unsigned), trade_price (the fill price), side (1 buy /
/   -1 sell), and time (sorted into time order before folding - the table
/   need not already be sorted)
/ @return pos with every trade in trades applied, oldest first
/ @eg .qpos.apply_fills[.qpos.empty_book[];trades] (trades: .envschema.trades-shaped)
apply_fills:{[pos;trades]
    {[pos;row] apply_fill[pos;row`sym;row`size;row`trade_price;row`side]}/[pos;`time xasc trades]};

/ Recompute a position book from a trades table (via apply_fills) and diff
/ it, per sym, against an independent reference book - e.g. a broker/prime
/ broker statement, or any other qty/avg_price snapshot not itself derived
/ from trades. A break here means the two sources disagree about what's
/ actually open: a missed or duplicated fill, a booking error on either
/ side, or a fill this trades table simply doesn't have yet.
/ .
/ avg_price is only compared when *both* sides show a nonzero position -
/ avg_price is 0 by empty_book/apply_fill convention once flat, so
/ comparing it while one side is flat would flag a meaningless "break"
/ every time a position closes out on one side before the other.
/ @param reference_book a position book (see empty_book) from a source
/   other than trades - e.g. a broker statement reshaped into this shape
/ @param trades a table with at least sym/size/trade_price/side/time -
/   the exact shape apply_fills expects
/ @param qty_tol qty difference at or below this (in absolute base
/   currency units) doesn't count as a break - use 0 for an exact match
/ @param price_tol avg_price difference at or below this doesn't count
/   as a break (only checked when both sides are non-flat - see above)
/ @return a table sym/computed_qty/reference_qty/qty_diff/
/   computed_avg_price/reference_avg_price/avg_price_diff/qty_break/
/   avg_price_break/status (`break`match), one row per sym seen in
/   either side, sorted breaks-first
/ @eg .qpos.reconcile_trades[broker_book;trades;0f;1e-6]
reconcile_trades:{[reference_book;trades;qty_tol;price_tol]
    computed_book:apply_fills[empty_book[];trades];
    ref_syms:exec sym from reference_book;
    comp_syms:exec sym from computed_book;
    all_syms:asc distinct ref_syms,comp_syms;
    col_for:{[book;syms;all_syms;col] {[book;syms;col;sym] $[sym in syms; (book sym)col; 0f]}[book;syms;col;] each all_syms};
    ref_qty:col_for[reference_book;ref_syms;all_syms;`qty];
    ref_avg:col_for[reference_book;ref_syms;all_syms;`avg_price];
    comp_qty:col_for[computed_book;comp_syms;all_syms;`qty];
    comp_avg:col_for[computed_book;comp_syms;all_syms;`avg_price];
    result:([]
        sym:all_syms;
        computed_qty:comp_qty; reference_qty:ref_qty; qty_diff:comp_qty-ref_qty;
        computed_avg_price:comp_avg; reference_avg_price:ref_avg; avg_price_diff:comp_avg-ref_avg);
    result:update qty_break:qty_tol<abs qty_diff from result;
    result:update avg_price_break:(qty_tol<abs computed_qty) and (qty_tol<abs reference_qty) and price_tol<abs avg_price_diff from result;
    result:update status:`match`break qty_break or avg_price_break from result;
    `status xasc result};

/ Mark-to-market unrealized P&L of sym's currently open position, in quote
/ currency - built on risk.q's own pnl formula (side/notional decomposed
/ from the book's signed qty) rather than reimplementing it.
/ @param pos a position book (see empty_book)
/ @param sym the symbol to mark
/ @param mkt_price the current market price to mark against
/ @return unrealized P&L, in quote currency (0 if sym isn't in pos or is flat)
/ @eg .qpos.unrealized_pnl[.qpos.apply_fill[.qpos.empty_book[];`EURUSD;600000;1.1000;1];`EURUSD;1.1100] -> 6000f
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
/ @eg .qpos.ccy_legs[`EURAUD;1000000;1.6000] -> +`ccy`amount!(`EUR`AUD;(1000000;-1600000f))
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
/ For a book long 1mm EURAUD @ 1.60 and long 500k AUDUSD @ 0.65, AUD nets
/ across both pairs: -1,600,000 from the EURAUD leg plus +500,000 from the
/ AUDUSD leg is -1,100,000.
/ @eg .qpos.ccy_exposure[.qpos.apply_fill[.qpos.apply_fill[.qpos.empty_book[];`EURAUD;1000000;1.6000;1];`AUDUSD;500000;0.6500;1]] -> +`ccy`amount!(`s#`AUD`EUR`USD;-1100000 1000000 -325000f)
ccy_exposure:{[pos]
    legs:raze {[row] ccy_legs[row`sym;row`qty;row`avg_price]} each 0!pos;
    / 0! - a plain table, not the keyed-by-ccy table `by` naturally
    / produces: every other function in this file returns/consumes plain
    / tables, and a keyed result here silently breaks a caller doing
    / e.g. `t,:this_result` onto a plain table (join of a plain and a
    / keyed table is a type error).
    0!select amount:sum amount by ccy from legs};

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
