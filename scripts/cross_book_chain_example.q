// cross_book_chain_example.q - worked example of what forwards.q
// currently supports for building a book on a pair with no direct
// interbank market: AUDPLN. There's no such thing as a live AUDUSD/PLN
// order book to load - AUDPLN has to be synthesized by chaining legs that
// do trade directly and share currencies with their neighbours:
//   AUDUSD (AUD/USD) -> EURUSD (EUR/USD, shares USD) -> EURPLN (EUR/PLN, shares EUR)
// Books come from a `quotes` table (ts, sym, bid_prices, bid_sizes,
// ask_prices, ask_sizes - one row per pair, same shape as the other
// example scripts' `out`), not bare per-leg dicts - book_for_sym below
// pulls a leg's row back out as the dict cross_book_chain_at_sizes wants.
// forwards.q's ccy_orient_chain works out which legs need inverting and
// what the resulting pair is; cross_book_chain_at_sizes then walks real
// depth on every leg to price AUDPLN at whatever sizes you ask for. There
// is currently no automatic path-finder or time-based book lookup - you
// name the leg chain yourself, and book_for_sym only ever returns the one
// row currently in `quotes` (no as-of-time semantics). (See the
// conversation that led to this script for what a `time`+`sym` ->
// automatic book convenience function would still need: currency-graph
// path discovery, plus a real as-of lookup into some quotes source -
// neither exists yet.)
//
// Narration/status uses lib/log4q.q's INFO/DEBUG/ERROR (see README's
// Licensing section) - actual table contents still go through `show`,
// since log4q's %N message formatting serializes a whole value onto one
// line rather than the readable grid `show` produces.
//
// Requires real kdb+/KDB-X, not the local PeachQ binary: log4q relies on
// a mid-expression variable assignment/read pattern PeachQ doesn't
// evaluate correctly (see README's Licensing section).
//
// Run from the repository root: q scripts/cross_book_chain_example.q

\c 400 1000
\l src/init.q
\l lib/log4q.q

/ Default log4q severity is INFO, which silently no-ops DEBUG calls -
/ lower it so this example's DEBUG lines actually print.
.log4q.sevl:`DEBUG;
key[.log4q.snk] set' .log4q.sev .log4q.sevl;

/ Illustrative, approximately realistic spot rates (not live market data) -
/ same three pairs and levels as reshape_wide_order_book_multi_pair_example.q,
/ so the two scripts' numbers agree if you compare them. pip_size is one
/ pip (0.0001); size_unit is one depth level's notional, in clean round
/ millions (1e6, 2e6, ... 1e7 across 10 levels) - typical order-of-
/ magnitude for eFX top-of-book depth on a major pair.
pip_size:0.0001;
size_unit:1e6;

/ A single 10-level top-of-book: bid/ask start pip_size apart at spot and
/ walk out one pip per level; sizes grow by size_unit per level, ask
/ offset a tenth of a level below bid so bid/ask sizes stay visually
/ distinct - see mk_level_cols in the other example scripts for the same
/ pattern applied to a whole table instead of one book dict.
mk_book:{[spot]
    levels:til 10;
    bid_prices:spot-pip_size*levels;
    ask_prices:(spot+pip_size)+pip_size*levels;
    bid_sizes:size_unit*1+levels;
    ask_sizes:(size_unit-size_unit%10)+size_unit*levels;
    `bid_prices`bid_sizes`ask_prices`ask_sizes!(bid_prices;bid_sizes;ask_prices;ask_sizes)};

/ Synthetic per-leg event timestamps, ~1ms apart - see
/ reshape_wide_order_book_multi_pair_example.q for the same helper with
/ fuller commentary (Normal(1ms,1ms) gaps via this repo's own
/ .uqf.inv_ncdf, floored so timestamps stay strictly increasing).
mk_timestamps:{[n;start_ts]
    mean_gap:0D00:00:00.001;
    std_gap:0D00:00:00.001;
    min_gap:0D00:00:00.0001;
    p:1e-9+(1-2e-9)*n?1.0;
    z:.uqf.inv_ncdf p;
    gaps:min_gap|mean_gap+std_gap*z;
    start_ts+sums gaps};

/ A quotes table - ts, sym, bid_prices, bid_sizes, ask_prices, ask_sizes,
/ one row per pair - rather than a bare dict per leg: `mk_book each spots`
/ comes back as a table already (a list of dicts sharing the same keys
/ auto-collapses to one), so ,'-joining ts/sym onto it is exactly the
/ meta_table,'level_table idiom the other example scripts use.
pairs:`AUDUSD`EURUSD`EURPLN;
approx_spot_rates:0.6550 1.0850 4.2500;
ts:mk_timestamps[count pairs;.z.p];
quotes:([] ts;sym:pairs),'(mk_book each approx_spot_rates);
INFO ("quotes - %1 rows, one 10-level book per pair, no wide-table reshape needed for this example";count quotes);
show quotes;

/ Private: one symbol's book, pulled back out of the quotes table as a
/ plain dict - the shape cross_book_chain_at_sizes expects for each leg.
book_for_sym:{[quotes;target_sym]
    matched:select bid_prices,bid_sizes,ask_prices,ask_sizes from quotes where sym=target_sym;
    if[0=count matched; '"book_for_sym: no quote for ",string target_sym];
    matched 0};

/ Work out how the three legs chain together before pricing anything -
/ cross_book_chain_at_sizes does this internally too, but resolving it up
/ front makes the leg orientation (which books get inverted) visible.
chain_syms:`AUDUSD`EURUSD`EURPLN;
orient:.uqf.ccy_orient_chain[chain_syms];
INFO ("orient - chain resolves to %1, invert flags per leg %2";(orient`cross_sym;orient`inverts));
show orient;

/ Depth-aware AUDPLN book at a few AUD notional sizes (cross_book_chain_at_sizes's
/ `size` is always denominated in leg 1's currency - AUD here, since
/ chain_syms[0] is AUDUSD).
sizes:1000000 3000000 5000000;
chain_books:book_for_sym[quotes;] each chain_syms;
audpln_book:.uqf.cross_book_chain_at_sizes[chain_syms;chain_books;sizes;`bid`ask`mid];
INFO ("audpln_book - synthetic %1 book at %2 sizes, chained through %3";(orient`cross_sym;count sizes;", " sv string chain_syms));
show audpln_book;

if[not all audpln_book`bid_fully_filled;
    ERROR "at least one requested size did not fully fill on the bid side - unexpected for this synthetic depth";
    exit 1];
if[not all audpln_book`ask_fully_filled;
    ERROR "at least one requested size did not fully fill on the ask side - unexpected for this synthetic depth";
    exit 1];

// exit 0
