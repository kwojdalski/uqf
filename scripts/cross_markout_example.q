// cross_markout_example.q - worked example of forwards.q's markout family
// for synthetic cross pairs: cross_markout_at_horizons (post-trade price
// drift at several ms offsets, including looking backward), cross_markout_decomp
// (splitting a price move into exact per-leg contributions), and
// cross_impact_at_horizons (did a trade in one pair move a DIFFERENT,
// related pair?). All three build on cross_book_at/cross_ref_price_at,
// so - like cross_book_chain_example.q - they operate on a `quotes`
// table (ts, sym, bid_prices, bid_sizes, ask_prices, ask_sizes), not
// bare book dicts.
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
// Run from the repository root: q scripts/cross_markout_example.q

\c 400 1000
\l src/init.q
\l lib/log4q.q

/ Default log4q severity is INFO, which silently no-ops DEBUG calls -
/ lower it so this example's DEBUG lines actually print.
.log4q.sevl:`DEBUG;
key[.log4q.snk] set' .log4q.sev .log4q.sevl;

/ Illustrative, approximately realistic spot rates (not live market data) -
/ same three pairs as the other example scripts. .qf.pip_size/
/ .qf.size_unit (src/example_defaults.q) are one pip (0.0001) and one
/ depth level's notional, in clean round millions.

mk_book:{[spot]
    levels:til 10;
    bid_prices:spot-.qf.pip_size*levels;
    ask_prices:(spot+.qf.pip_size)+.qf.pip_size*levels;
    bid_sizes:.qf.size_unit*1+levels;
    ask_sizes:(.qf.size_unit-.qf.size_unit%10)+.qf.size_unit*levels;
    `bid_prices`bid_sizes`ask_prices`ask_sizes!(bid_prices;bid_sizes;ask_prices;ask_sizes)};

/ Synthetic tick timestamps, ~200ms apart with Normal(200ms,50ms) jitter -
/ see reshape_wide_order_book_multi_pair_example.q for the same helper
/ pattern (Normal gaps via this repo's own .qf.inv_ncdf) at a 1ms
/ cadence instead; this example uses a slower cadence so a handful of
/ ticks span a readable ~1 second window.
mk_timestamps:{[n;start_ts]
    mean_gap:0D00:00:00.200;
    std_gap:0D00:00:00.050;
    min_gap:0D00:00:00.050;
    p:1e-9+(1-2e-9)*n?1.0;
    DEBUG "running: .qf.inv_ncdf p";
    z:.qf.inv_ncdf p;
    gaps:min_gap|mean_gap+std_gap*z;
    start_ts+sums gaps};

/ A tick series for one pair: n prices drifting linearly by drift_per_tick,
/ one row per timestamp in ts.
mk_tick_series:{[sym;start_spot;drift_per_tick;ts]
    n:count ts;
    spots:start_spot+drift_per_tick*til n;
    ([] ts; sym:n#sym),'(mk_book each spots)};

/ 6 ticks per leg over roughly a second: AUDUSD and EURPLN drift up,
/ EURUSD drifts down - so a synthetic AUDPLN move should show up as a
/ genuine multi-leg story, not one leg dominating everything.
n_ticks:6;
start_ts:.z.p;
audusd_ts:mk_timestamps[n_ticks;start_ts];
eurusd_ts:mk_timestamps[n_ticks;start_ts];
eurpln_ts:mk_timestamps[n_ticks;start_ts];
audusd_q:mk_tick_series[`AUDUSD;0.6550;0.00005;audusd_ts];
eurusd_q:mk_tick_series[`EURUSD;1.0850;-0.00002;eurusd_ts];
eurpln_q:mk_tick_series[`EURPLN;4.2500;0.0001;eurpln_ts];
quotes:`sym`ts xasc (audusd_q,eurusd_q,eurpln_q);
INFO ("quotes - %1 rows, %2 ticks per leg over ~1s";(count quotes;n_ticks));
show quotes;

/ ==== cross_markout_at_horizons: post-trade drift at several offsets ====
/ Simulate a trade in AUDPLN partway through the tick window, priced at
/ the prevailing mid at that instant (so the 0ms horizon's markout is
/ exactly zero by construction) - then look -500/-300/0/+100/+300ms
/ around it.
mid_idx:n_ticks div 2;
trade_time:audusd_ts mid_idx;
DEBUG "running: .qf.cross_book_at[quotes;`AUDPLN;trade_time;enlist 1;enlist `mid]";
trade_price:first .qf.cross_book_at[quotes;`AUDPLN;trade_time;enlist 1;enlist `mid]`mid;
INFO ("trade_time/trade_price - a synthetic AUDPLN buy at %1: %2";(trade_time;trade_price));

DEBUG "running: .qf.cross_markout_at_horizons[quotes;`AUDPLN;trade_time;1;trade_price;10000;-500 -300 0 100 300;1]";
horizons_r:.qf.cross_markout_at_horizons[quotes;`AUDPLN;trade_time;1;trade_price;10000;-500 -300 0 100 300;1];
INFO "horizons_r - markout at each horizon (negative = before the trade):";
show horizons_r;
if[not (first horizons_r[`ts] where horizons_r[`horizon_ms]=0)~trade_time;
    ERROR "the 0ms horizon should land exactly on trade_time";
    exit 1];
if[0.0<>first horizons_r[`markout_pips] where horizons_r[`horizon_ms]=0;
    ERROR "the 0ms horizon's markout should be exactly zero by construction (trade_price was set to the mid at that instant)";
    exit 1];

/ ==== cross_markout_decomp: exact per-leg attribution ====
/ Between the earliest time ALL 3 legs have a quote (max of each leg's
/ own first tick) and the latest time all 3 still have one (min of each
/ leg's own last tick) - each leg's tick times are independently
/ jittered by mk_timestamps, so they aren't aligned to a shared grid.
t0:max (first audusd_ts;first eurusd_ts;first eurpln_ts);
t1:min (last audusd_ts;last eurusd_ts;last eurpln_ts);
DEBUG "running: .qf.cross_markout_decomp[quotes;`AUDPLN;t0;t1;10000;1]";
decomp:.qf.cross_markout_decomp[quotes;`AUDPLN;t0;t1;10000;1];
INFO "decomp - AUDPLN's total move over the window, split by leg:";
show decomp;

decomp_total:sum decomp`contribution_pips;
DEBUG "running: .qf.cross_ref_price_at[quotes;`AUDPLN;1;t0]";
mid_t0:.qf.cross_ref_price_at[quotes;`AUDPLN;1;t0];
DEBUG "running: .qf.cross_ref_price_at[quotes;`AUDPLN;1;t1]";
mid_t1:.qf.cross_ref_price_at[quotes;`AUDPLN;1;t1];
actual_total:10000*mid_t1-mid_t0;
INFO ("decomp_total vs actual_total - %1 vs %2, must match exactly (this is an exact decomposition, not an approximation)";(decomp_total;actual_total));
if[1e-6<abs decomp_total-actual_total;
    ERROR "per-leg contributions should sum exactly to the actual total move";
    exit 1];

/ ==== cross_impact_at_horizons: did EURPLN's move coincide with EURUSD? ====
/ EURPLN and EURUSD both quote EUR, so this is a plausible real-world
/ impact question, even though our synthetic EURUSD drift here is
/ unrelated to the EURPLN "trade" (there's no genuine causality in
/ synthetic data - this only demonstrates the mechanism).
DEBUG "running: .qf.cross_impact_at_horizons[quotes;`EURPLN;`EURUSD;trade_time;1;10000;-500 -300 0 100 300;1]";
impact_r:.qf.cross_impact_at_horizons[quotes;`EURPLN;`EURUSD;trade_time;1;10000;-500 -300 0 100 300;1];
INFO "impact_r - EURUSD's own drift around the EURPLN trade's timestamps:";
show impact_r;

// exit 0
