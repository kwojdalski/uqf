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
/ so the two scripts' numbers agree if you compare them. .qex.pip_size/
/ .qex.size_unit (src/example_defaults.q) are one pip (0.0001) and one
/ depth level's notional, in clean round millions (1e6, 2e6, ... 1e7
/ across 10 levels) - typical order-of-magnitude for eFX top-of-book
/ depth on a major pair.

/ A single 10-level top-of-book: bid/ask start .qex.pip_size apart at spot
/ and walk out one pip per level; sizes grow by .qex.size_unit per level,
/ ask offset a tenth of a level below bid so bid/ask sizes stay visually
/ distinct - see mk_level_cols in the other example scripts for the same
/ pattern applied to a whole table instead of one book dict.
mk_book:{[spot]
    levels:til 10;
    bid_prices:spot-.qex.pip_size*levels;
    ask_prices:(spot+.qex.pip_size)+.qex.pip_size*levels;
    bid_sizes:.qex.size_unit*1+levels;
    ask_sizes:(.qex.size_unit-.qex.size_unit%10)+.qex.size_unit*levels;
    `bid_prices`bid_sizes`ask_prices`ask_sizes!(bid_prices;bid_sizes;ask_prices;ask_sizes)};

/ Synthetic per-leg event timestamps, ~1ms apart - see
/ reshape_wide_order_book_multi_pair_example.q for the same helper with
/ fuller commentary (Normal(1ms,1ms) gaps via this repo's own
/ .qstats.inv_ncdf, floored so timestamps stay strictly increasing).
mk_timestamps:{[n;start_ts]
    mean_gap:0D00:00:00.001;
    std_gap:0D00:00:00.001;
    min_gap:0D00:00:00.0001;
    p:1e-9+(1-2e-9)*n?1.0;
    DEBUG "running: .qstats.inv_ncdf p";
    z:.qstats.inv_ncdf p;
    gaps:min_gap|mean_gap+std_gap*z;
    start_ts+sums gaps};

/ A quotes table - ts, sym, bid_prices, bid_sizes, ask_prices, ask_sizes,
/ one row per pair - rather than a bare dict per leg: `mk_book each spots`
/ comes back as a table already (a list of dicts sharing the same keys
/ auto-collapses to one), so ,'-joining ts/sym onto it is exactly the
/ meta_table,'level_table idiom the other example scripts use. Sorted
/ `sym`ts xasc afterward - cross_book_at requires that (it does an as-of
/ join per leg internally, which silently gives wrong answers on
/ unsorted input, so cross_book_at checks and errors instead of guessing).
pairs:`AUDUSD`EURUSD`EURPLN;
approx_spot_rates:0.6550 1.0850 4.2500;
ts:mk_timestamps[count pairs;.z.p];
unsorted_quotes:([] ts;sym:pairs),'(mk_book each approx_spot_rates);
quotes:`sym`ts xasc unsorted_quotes;
INFO ("quotes - %1 rows, one 10-level book per pair, no wide-table reshape needed for this example";count quotes);
show quotes;

/ ==== cross_book_at: just give it the target pair and a time ====
sizes:1000000 3000000 5000000;
as_of:.z.p+1D;

DEBUG "running: .qfwd.cross_book_at[quotes;`AUDPLN;as_of;sizes;`bid`ask`mid]";
audpln_book:.qfwd.cross_book_at[quotes;`AUDPLN;as_of;sizes;`bid`ask`mid];
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
DEBUG "running: .qfwd.cross_book_at[quotes;`AUDUSD;as_of;sizes;`mid]";
show .qfwd.cross_book_at[quotes;`AUDUSD;as_of;sizes;`mid];
DEBUG "running: .qfwd.cross_book_at[quotes;`USDAUD;as_of;sizes;`mid]";
show .qfwd.cross_book_at[quotes;`USDAUD;as_of;sizes;`mid];

/ ==== the "recipe" on its own: which legs does a pair need? ====
/ cross_decomp is the piece that replaces having to name the chain
/ yourself - it BFS-searches quotes' own distinct `sym` column for the
/ shortest route between a pair's two currencies. Useful on its own too,
/ e.g. to inspect what a pricing call would do before running it, or to
/ feed a different downstream step than cross_book_chain_at_sizes.
DEBUG "running: .qfwd.cross_decomp[distinct quotes`sym;`AUDPLN]";
chain_syms:.qfwd.cross_decomp[distinct quotes`sym;`AUDPLN];
INFO ("chain_syms - cross_decomp found %1";enlist ", " sv string chain_syms);

/ A different pool of available pairs decomposes differently - EURRUB
/ (no direct EUR/RUB market) only needs a single USD bridge, not the
/ 2-hop EUR->USD->PLN route AUDPLN needed above.
eurusd_usdrub:`EURUSD`USDRUB;
DEBUG "running: .qfwd.cross_decomp[eurusd_usdrub;`EURRUB]";
eurrub_chain:.qfwd.cross_decomp[eurusd_usdrub;`EURRUB];
INFO ("eurrub_chain - with only %1 available, EURRUB decomposes to %2";(", " sv string eurusd_usdrub;", " sv string eurrub_chain));
if[not eurrub_chain~`EURUSD`USDRUB;
    ERROR "expected EURRUB to decompose to exactly EURUSD, USDRUB";
    exit 1];

DEBUG "running: .qfwd.ccy_orient_chain[chain_syms]";
orient:.qfwd.ccy_orient_chain[chain_syms];
INFO ("orient - chain resolves to %1, invert flags per leg %2";(orient`cross_sym;orient`inverts));
show orient;

DEBUG "running: .qfwd.leg_book_as_of[quotes;as_of;] each chain_syms";
chain_books:.qfwd.leg_book_as_of[quotes;as_of;] each chain_syms;
DEBUG "running: .qfwd.cross_book_chain_at_sizes[chain_syms;chain_books;sizes;`bid`ask`mid]";
manual_audpln_book:.qfwd.cross_book_chain_at_sizes[chain_syms;chain_books;sizes;`bid`ask`mid];
INFO "manual_audpln_book - same result, built leg by leg instead of via cross_book_at:";
show manual_audpln_book;

if[not manual_audpln_book~audpln_book;
    ERROR "manual chain result disagrees with cross_book_at - the two paths should always match";
    exit 1];

/ ==== proof that at_time picks the price as of that specific moment ====
/ Three AUDUSD ticks 10ms apart, price drifting 0.6550 -> 0.6555 -> 0.6560.
/ Querying cross_book_at at various times should always return the most
/ recent tick AT OR BEFORE the requested time, never a later one - i.e.
/ querying 5ms after tick1 (before tick2 exists yet) must still return
/ tick1's price, not tick2's.
tick_ts:.z.p+0D 0D00:00:00.010 0D00:00:00.020;
tick_spots:0.6550 0.6555 0.6560;
unsorted_audusd_ticks:([] ts:tick_ts; sym:3#`AUDUSD),'(mk_book each tick_spots);
audusd_ticks:`sym`ts xasc unsorted_audusd_ticks;
INFO ("audusd_ticks - %1 successive AUDUSD quotes, 10ms apart, price drifting up:";count audusd_ticks);
show audusd_ticks;

query_times:`before_any_tick`at_tick1`between_tick1_and_tick2`at_tick3!(
    tick_ts[0]-0D00:00:00.001;
    tick_ts[0];
    tick_ts[0]+0D00:00:00.005;
    tick_ts[2]);

DEBUG "running: .qfwd.cross_book_at[audusd_ticks;`AUDUSD;query_times`at_tick1;enlist 1000000;`mid]";
mid_at_tick1:.qfwd.cross_book_at[audusd_ticks;`AUDUSD;query_times`at_tick1;enlist 1000000;`mid];
INFO ("mid_at_tick1 - queried exactly at tick1's timestamp: %1";enlist first mid_at_tick1`mid);

DEBUG "running: .qfwd.cross_book_at[audusd_ticks;`AUDUSD;query_times`between_tick1_and_tick2;enlist 1000000;`mid]";
mid_between:.qfwd.cross_book_at[audusd_ticks;`AUDUSD;query_times`between_tick1_and_tick2;enlist 1000000;`mid];
INFO ("mid_between - queried 5ms after tick1, before tick2 exists: %1";enlist first mid_between`mid);

DEBUG "running: .qfwd.cross_book_at[audusd_ticks;`AUDUSD;query_times`at_tick3;enlist 1000000;`mid]";
mid_at_tick3:.qfwd.cross_book_at[audusd_ticks;`AUDUSD;query_times`at_tick3;enlist 1000000;`mid];
INFO ("mid_at_tick3 - queried at tick3's timestamp: %1";enlist first mid_at_tick3`mid);

if[not mid_at_tick1[`mid]~mid_between`mid;
    ERROR "querying between tick1 and tick2 should still return tick1's price - got a different value, at_time is peeking ahead";
    exit 1];
if[mid_at_tick1[`mid]~mid_at_tick3`mid;
    ERROR "querying at tick3 should return a later, different price than tick1 - got the same value, at_time isn't picking up new ticks";
    exit 1];
INFO "confirmed: between-tick query matches the earlier tick exactly, and the tick3 query differs from it - at_time correctly reflects only information known as of that instant";

/ querying before any tick exists should error, not silently return
/ something (or, worse, the wrong tick).
wrapper_no_data:{[q] .qfwd.cross_book_at[q;`AUDUSD;query_times`before_any_tick;enlist 1000000;`mid]};
caught:@[wrapper_no_data;audusd_ticks;{[e] e}];
INFO ("querying before any tick exists correctly errors: %1";enlist caught);
if[10h<>type caught;
    ERROR "querying before any tick existed should have thrown an error, not returned a result";
    exit 1];

// exit 0
