// cross_book_chain_example.q - worked example of forwards.q's
// cross_book_at: give it a quotes table, a target pair with no direct
// interbank market (AUDPLN), and a time - it works out the chain of
// available legs itself:
//   AUDUSD (AUD/USD) -> EURUSD (EUR/USD, shares USD) -> EURPLN (EUR/PLN, shares EUR)
// and prices AUDPLN at whatever sizes you ask for. You do NOT need to
// name the leg chain yourself; cross_book_at finds the shortest chain of
// currently-available pairs (ccy_shortest_path, a BFS over the currency
// graph implied by quotes' own `sym` column) and looks up each leg's most
// recent quote at or before the requested time (leg_book_as_of) on its
// own. The second half of this script shows the lower-level machinery
// cross_book_at is built on (ccy_orient_chain, cross_book_chain_at_sizes)
// for anyone who wants to supply the leg chain by hand instead.
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
    DEBUG "running: .uqf.inv_ncdf p";
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

/ ==== cross_book_at: just give it the target pair and a time ====
sizes:1000000 3000000 5000000;
as_of:.z.p+1D;

DEBUG "running: .uqf.cross_book_at[quotes;`AUDPLN;as_of;sizes;`bid`ask`mid]";
audpln_book:.uqf.cross_book_at[quotes;`AUDPLN;as_of;sizes;`bid`ask`mid];
INFO ("audpln_book - synthetic AUDPLN book at %1 sizes, chain found automatically from quotes";count sizes);
show audpln_book;

if[not all audpln_book`bid_fully_filled;
    ERROR "at least one requested size did not fully fill on the bid side - unexpected for this synthetic depth";
    exit 1];
if[not all audpln_book`ask_fully_filled;
    ERROR "at least one requested size did not fully fill on the ask side - unexpected for this synthetic depth";
    exit 1];

/ cross_book_at also handles a pair that's already directly quoted (no
/ chaining needed at all) and the inverse of a directly quoted pair
/ (USDAUD, when only AUDUSD is in `quotes`) the same way, transparently.
DEBUG "running: .uqf.cross_book_at[quotes;`AUDUSD;as_of;sizes;`mid]";
show .uqf.cross_book_at[quotes;`AUDUSD;as_of;sizes;`mid];
DEBUG "running: .uqf.cross_book_at[quotes;`USDAUD;as_of;sizes;`mid]";
show .uqf.cross_book_at[quotes;`USDAUD;as_of;sizes;`mid];

/ ==== what cross_book_at does internally, run by hand ====
/ ccy_shortest_path is the piece that replaces having to name the chain
/ yourself - it BFS-searches quotes' own distinct `sym` column for the
/ shortest route between two currencies.
DEBUG "running: .uqf.ccy_shortest_path[distinct quotes`sym;`AUD;`PLN]";
chain_syms:.uqf.ccy_shortest_path[distinct quotes`sym;`AUD;`PLN];
INFO ("chain_syms - ccy_shortest_path found %1";enlist ", " sv string chain_syms);

DEBUG "running: .uqf.ccy_orient_chain[chain_syms]";
orient:.uqf.ccy_orient_chain[chain_syms];
INFO ("orient - chain resolves to %1, invert flags per leg %2";(orient`cross_sym;orient`inverts));
show orient;

DEBUG "running: .uqf.leg_book_as_of[quotes;as_of;] each chain_syms";
chain_books:.uqf.leg_book_as_of[quotes;as_of;] each chain_syms;
DEBUG "running: .uqf.cross_book_chain_at_sizes[chain_syms;chain_books;sizes;`bid`ask`mid]";
manual_audpln_book:.uqf.cross_book_chain_at_sizes[chain_syms;chain_books;sizes;`bid`ask`mid];
INFO "manual_audpln_book - same result, built leg by leg instead of via cross_book_at:";
show manual_audpln_book;

if[not manual_audpln_book~audpln_book;
    ERROR "manual chain result disagrees with cross_book_at - the two paths should always match";
    exit 1];

// exit 0
