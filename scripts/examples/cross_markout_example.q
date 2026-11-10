// cross_markout_example.q - worked example of forwards.q's markout family
// for synthetic cross pairs: cross_markout_at_horizons (post-trade price
// drift at several ms offsets, including looking backward), cross_markout_decomp
// (splitting a price move into exact per-leg contributions), and
// cross_impact_at_horizons (did a trade in one pair move a DIFFERENT,
// related pair?). All three build on cross_book_at/cross_ref_price_at,
// so - like cross_book_chain_example.q - they operate on a `quotes`
// table (time, sym, bid_prices, bid_sizes, ask_prices, ask_sizes), not
// bare book dicts.
//
// Narration/status goes through .qetl.log (src/etl/core/log.q), the one q
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
/ The synthetic-book helpers the examples share: .qf.demo.book, .qf.demo.level_cols,
/ .qf.demo.timestamps.
\l scripts/examples/lib/example_books.q

/ .qetl.log suppresses DEBUG lines by default; this example's narration uses them.
.qetl.log.debug 1b;

/ Illustrative, approximately realistic spot rates (not live market data) -
/ same three pairs as the other example scripts. .qf.demo.pip_size/
/ .qf.demo.level_size_unit (src/examples/example_defaults.q) are one pip (0.0001) and one
/ depth level's notional, in clean round millions.

/ The tick cadence for .qf.demo.timestamps: ~200ms apart with Normal(200ms,50ms)
/ jitter, floored at 50ms - slower than the other examples' 1ms, so a handful of
/ ticks span a readable ~1 second window.
tick_gaps:`mean`std`min!(0D00:00:00.200;0D00:00:00.050;0D00:00:00.050);

/ A tick series for one pair: n prices drifting linearly by drift_per_tick,
/ one row per timestamp in time.
mk_tick_series:{[sym;start_spot;drift_per_tick;time]
    n:count time;
    spots:start_spot+drift_per_tick*til n;
    ([] time; sym:n#sym),'(.qf.demo.book each spots)};

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
audusd_ts:.qf.demo.timestamps[`cross_markout;n_ticks;start_ts;tick_gaps];
eurusd_ts:.qf.demo.timestamps[`cross_markout;n_ticks;start_ts;tick_gaps];
eurpln_ts:.qf.demo.timestamps[`cross_markout;n_ticks;start_ts;tick_gaps];
audusd_q:mk_tick_series[`AUDUSD;0.6550;0.00005;audusd_ts];
eurusd_q:mk_tick_series[`EURUSD;1.0850;-0.00002;eurusd_ts];
eurpln_q:mk_tick_series[`EURPLN;4.2500;0.0001;eurpln_ts];
quotes:`sym`time xasc (audusd_q,eurusd_q,eurpln_q);
.qetl.log.ch.info[`lifecycle;"quotes - ",.Q.s1[count quotes]," rows, ",.Q.s1[n_ticks]," ticks per leg over ~1s";enlist[`example]!enlist `cross_markout];
show quotes;

/ ==== cross_markout_at_horizons: post-trade drift at several offsets ====
/ Horizons are timespans, as .qf.exec.markout_at_horizons' are.
horizons:0D00:00:00.001*-500 -300 0 100 300;
/ Simulate a trade in AUDPLN partway through the tick window, priced at
/ the prevailing mid at that instant (so the 0ms horizon's markout is
/ exactly zero by construction) - then look -500/-300/0/+100/+300ms
/ around it.
mid_idx:n_ticks div 2;
trade_time:audusd_ts mid_idx;
.qetl.log.ch.dbg[`lifecycle;"running: .qf.mkt.cross_book_at[quotes;`AUDPLN;trade_time;enlist 1;enlist `mid]";enlist[`example]!enlist `cross_markout];
trade_price:first .qf.mkt.cross_book_at[quotes;`AUDPLN;trade_time;enlist 1;enlist `mid]`mid;
.qetl.log.ch.info[`lifecycle;"trade_time/trade_price - a synthetic AUDPLN buy at ",.Q.s1[trade_time],": ",.Q.s1[trade_price];enlist[`example]!enlist `cross_markout];

.qetl.log.ch.dbg[`lifecycle;"running: .qf.exec.cross_markout_at_horizons[quotes;`AUDPLN;trade_time;1;trade_price;10000;horizons;1]";enlist[`example]!enlist `cross_markout];
horizons_r:.qf.exec.cross_markout_at_horizons[quotes;`AUDPLN;trade_time;1;trade_price;10000;horizons;1];
.qetl.log.ch.info[`lifecycle;"horizons_r - markout at each horizon (negative = before the trade):";enlist[`example]!enlist `cross_markout];
show horizons_r;
if[not (first horizons_r[`time] where horizons_r[`horizon]=0D00:00:00)~trade_time;
    .qetl.log.ch.err[`lifecycle;"the 0ms horizon should land exactly on trade_time";enlist[`example]!enlist `cross_markout];
    exit 1];
if[0.0<>first horizons_r[`markout_pips] where horizons_r[`horizon]=0D00:00:00;
    .qetl.log.ch.err[`lifecycle;"the 0ms horizon's markout should be exactly zero by construction (trade_price was set to the mid at that instant)";enlist[`example]!enlist `cross_markout];
    exit 1];

/ ==== cross_markout_decomp: exact per-leg attribution ====
/ Between the earliest time ALL 3 legs have a quote (max of each leg's
/ own first tick) and the latest time all 3 still have one (min of each
/ leg's own last tick) - each leg's tick times are independently
/ jittered by .qf.demo.timestamps, so they aren't aligned to a shared grid.
t0:max (first audusd_ts;first eurusd_ts;first eurpln_ts);
t1:min (last audusd_ts;last eurusd_ts;last eurpln_ts);
.qetl.log.ch.dbg[`lifecycle;"running: .qf.exec.cross_markout_decomp[quotes;`AUDPLN;t0;t1;10000;1]";enlist[`example]!enlist `cross_markout];
decomp:.qf.exec.cross_markout_decomp[quotes;`AUDPLN;t0;t1;10000;1];
.qetl.log.ch.info[`lifecycle;"decomp - AUDPLN's total move over the window, split by leg:";enlist[`example]!enlist `cross_markout];
show decomp;

decomp_total:sum decomp`contribution_pips;
.qetl.log.ch.dbg[`lifecycle;"running: .qf.mkt.cross_ref_price_at[quotes;`AUDPLN;t0;1]";enlist[`example]!enlist `cross_markout];
mid_t0:.qf.mkt.cross_ref_price_at[quotes;`AUDPLN;t0;1];
.qetl.log.ch.dbg[`lifecycle;"running: .qf.mkt.cross_ref_price_at[quotes;`AUDPLN;t1;1]";enlist[`example]!enlist `cross_markout];
mid_t1:.qf.mkt.cross_ref_price_at[quotes;`AUDPLN;t1;1];
actual_total:10000*mid_t1-mid_t0;
.qetl.log.ch.info[`lifecycle;"decomp_total vs actual_total - ",.Q.s1[decomp_total]," vs ",.Q.s1[actual_total],", must match exactly (this is an exact decomposition, not an approximation)";enlist[`example]!enlist `cross_markout];
if[1e-6<abs decomp_total-actual_total;
    .qetl.log.ch.err[`lifecycle;"per-leg contributions should sum exactly to the actual total move";enlist[`example]!enlist `cross_markout];
    exit 1];

/ ==== cross_impact_at_horizons: did EURPLN's move coincide with EURUSD? ====
/ EURPLN and EURUSD both quote EUR, so this is a plausible real-world
/ impact question, even though our synthetic EURUSD drift here is
/ unrelated to the EURPLN "trade" (there's no genuine causality in
/ synthetic data - this only demonstrates the mechanism).
.qetl.log.ch.dbg[`lifecycle;"running: .qf.exec.cross_impact_at_horizons[quotes;`EURPLN;`EURUSD;trade_time;1;10000;horizons;1]";enlist[`example]!enlist `cross_markout];
impact_r:.qf.exec.cross_impact_at_horizons[quotes;`EURPLN;`EURUSD;trade_time;1;10000;horizons;1];
.qetl.log.ch.info[`lifecycle;"impact_r - EURUSD's own drift around the EURPLN trade's timestamps:";enlist[`example]!enlist `cross_markout];
show impact_r;

// exit 0
