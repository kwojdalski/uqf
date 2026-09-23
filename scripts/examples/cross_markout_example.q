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
// Narration/status goes through .qlog (src/etl/core/log.q), the one q
// logging layer in this tree - actual table contents still go through
// `show`, since a log line serializes a whole value onto one line rather
// than the readable grid `show` produces.
//
// Run from the repository root: q scripts/examples/cross_markout_example.q [n_ticks]
// n_ticks (default 6) is how many ticks to generate per leg - optional,
// positional, same convention as timer_replay_example.q's parameters.

\c 400 1000
\l src/init.q
\l src/etl/core/log.q

/ .qlog suppresses DBG lines by default; this example's narration uses them.
.qlog.debug 1b;

/ Illustrative, approximately realistic spot rates (not live market data) -
/ same three pairs as the other example scripts. .qexdef.pip_size/
/ .qexdef.size_unit (src/examples/example_defaults.q) are one pip (0.0001) and one
/ depth level's notional, in clean round millions.

mk_book:{[spot]
    levels:til 10;
    bid_prices:spot-.qexdef.pip_size*levels;
    ask_prices:(spot+.qexdef.pip_size)+.qexdef.pip_size*levels;
    bid_sizes:.qexdef.size_unit*1+levels;
    ask_sizes:(.qexdef.size_unit-.qexdef.size_unit%10)+.qexdef.size_unit*levels;
    `bid_prices`bid_sizes`ask_prices`ask_sizes!(bid_prices;bid_sizes;ask_prices;ask_sizes)};

/ Synthetic tick timestamps, ~200ms apart with Normal(200ms,50ms) jitter -
/ see reshape_wide_order_book_multi_pair_example.q for the same helper
/ pattern (Normal gaps via this repo's own .qstats.inv_ncdf) at a 1ms
/ cadence instead; this example uses a slower cadence so a handful of
/ ticks span a readable ~1 second window.
mk_timestamps:{[n;start_ts]
    mean_gap:0D00:00:00.200;
    std_gap:0D00:00:00.050;
    min_gap:0D00:00:00.050;
    p:1e-9+(1-2e-9)*n?1.0;
    .qlog.dbg[`cross_markout;"running: .qstats.inv_ncdf p";()!()];
    z:.qstats.inv_ncdf p;
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
/ genuine multi-leg story, not one leg dominating everything. Overridable
/ via the command line (q scripts/examples/cross_markout_example.q [n_ticks]) -
/ .z.x is the list of args after the script name, always strings; cast
/ and fall back to the default whenever an arg wasn't given, same
/ convention as timer_replay_example.q's parameters.
default_n_ticks:6;
n_ticks:$[0<count .z.x; "J"$first .z.x; default_n_ticks];
start_ts:.z.p;
audusd_ts:mk_timestamps[n_ticks;start_ts];
eurusd_ts:mk_timestamps[n_ticks;start_ts];
eurpln_ts:mk_timestamps[n_ticks;start_ts];
audusd_q:mk_tick_series[`AUDUSD;0.6550;0.00005;audusd_ts];
eurusd_q:mk_tick_series[`EURUSD;1.0850;-0.00002;eurusd_ts];
eurpln_q:mk_tick_series[`EURPLN;4.2500;0.0001;eurpln_ts];
quotes:`sym`ts xasc (audusd_q,eurusd_q,eurpln_q);
.qlog.info[`cross_markout;"quotes - ",.Q.s1[count quotes]," rows, ",.Q.s1[n_ticks]," ticks per leg over ~1s";()!()];
show quotes;

/ ==== cross_markout_at_horizons: post-trade drift at several offsets ====
/ Simulate a trade in AUDPLN partway through the tick window, priced at
/ the prevailing mid at that instant (so the 0ms horizon's markout is
/ exactly zero by construction) - then look -500/-300/0/+100/+300ms
/ around it.
mid_idx:n_ticks div 2;
trade_time:audusd_ts mid_idx;
.qlog.dbg[`cross_markout;"running: .qfwd.cross_book_at[quotes;`AUDPLN;trade_time;enlist 1;enlist `mid]";()!()];
trade_price:first .qfwd.cross_book_at[quotes;`AUDPLN;trade_time;enlist 1;enlist `mid]`mid;
.qlog.info[`cross_markout;"trade_time/trade_price - a synthetic AUDPLN buy at ",.Q.s1[trade_time],": ",.Q.s1[trade_price];()!()];

.qlog.dbg[`cross_markout;"running: .qfwd.cross_markout_at_horizons[quotes;`AUDPLN;trade_time;1;trade_price;10000;-500 -300 0 100 300;1]";()!()];
horizons_r:.qfwd.cross_markout_at_horizons[quotes;`AUDPLN;trade_time;1;trade_price;10000;-500 -300 0 100 300;1];
.qlog.info[`cross_markout;"horizons_r - markout at each horizon (negative = before the trade):";()!()];
show horizons_r;
if[not (first horizons_r[`ts] where horizons_r[`horizon_ms]=0)~trade_time;
    .qlog.err[`cross_markout;"the 0ms horizon should land exactly on trade_time";()!()];
    exit 1];
if[0.0<>first horizons_r[`markout_pips] where horizons_r[`horizon_ms]=0;
    .qlog.err[`cross_markout;"the 0ms horizon's markout should be exactly zero by construction (trade_price was set to the mid at that instant)";()!()];
    exit 1];

/ ==== cross_markout_decomp: exact per-leg attribution ====
/ Between the earliest time ALL 3 legs have a quote (max of each leg's
/ own first tick) and the latest time all 3 still have one (min of each
/ leg's own last tick) - each leg's tick times are independently
/ jittered by mk_timestamps, so they aren't aligned to a shared grid.
t0:max (first audusd_ts;first eurusd_ts;first eurpln_ts);
t1:min (last audusd_ts;last eurusd_ts;last eurpln_ts);
.qlog.dbg[`cross_markout;"running: .qfwd.cross_markout_decomp[quotes;`AUDPLN;t0;t1;10000;1]";()!()];
decomp:.qfwd.cross_markout_decomp[quotes;`AUDPLN;t0;t1;10000;1];
.qlog.info[`cross_markout;"decomp - AUDPLN's total move over the window, split by leg:";()!()];
show decomp;

decomp_total:sum decomp`contribution_pips;
.qlog.dbg[`cross_markout;"running: .qfwd.cross_ref_price_at[quotes;`AUDPLN;t0;1]";()!()];
mid_t0:.qfwd.cross_ref_price_at[quotes;`AUDPLN;t0;1];
.qlog.dbg[`cross_markout;"running: .qfwd.cross_ref_price_at[quotes;`AUDPLN;t1;1]";()!()];
mid_t1:.qfwd.cross_ref_price_at[quotes;`AUDPLN;t1;1];
actual_total:10000*mid_t1-mid_t0;
.qlog.info[`cross_markout;"decomp_total vs actual_total - ",.Q.s1[decomp_total]," vs ",.Q.s1[actual_total],", must match exactly (this is an exact decomposition, not an approximation)";()!()];
if[1e-6<abs decomp_total-actual_total;
    .qlog.err[`cross_markout;"per-leg contributions should sum exactly to the actual total move";()!()];
    exit 1];

/ ==== cross_impact_at_horizons: did EURPLN's move coincide with EURUSD? ====
/ EURPLN and EURUSD both quote EUR, so this is a plausible real-world
/ impact question, even though our synthetic EURUSD drift here is
/ unrelated to the EURPLN "trade" (there's no genuine causality in
/ synthetic data - this only demonstrates the mechanism).
.qlog.dbg[`cross_markout;"running: .qfwd.cross_impact_at_horizons[quotes;`EURPLN;`EURUSD;trade_time;1;10000;-500 -300 0 100 300;1]";()!()];
impact_r:.qfwd.cross_impact_at_horizons[quotes;`EURPLN;`EURUSD;trade_time;1;10000;-500 -300 0 100 300;1];
.qlog.info[`cross_markout;"impact_r - EURUSD's own drift around the EURPLN trade's timestamps:";()!()];
show impact_r;

// exit 0
