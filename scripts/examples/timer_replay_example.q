// timer_replay_example.q - worked example of replaying pre-generated
// synthetic tick data into a LIVE, growing quotes table on a fixed timer
// interval, instead of building the whole synthetic dataset upfront in
// one vectorized batch the way every other scripts/*.q example does.
//
// Adapted from lib/qAutomatedTrading/histTickData/timersvc.q's pattern:
// load/generate historical data once, then a .z.time timer callback
// advances through it row-by-row on a fixed system timer ("t <ms>"),
// simulating a live feed. That script publishes each row over IPC to a
// tickerplant (hopen/neg[h]); this example appends each row directly to
// a local table instead, since uqf has no tickerplant or other
// long-running process for it to publish to (see lib/qAutomatedTrading's
// entry in README's Licensing section) - after each append, it calls a
// uqf function against however much of the table has arrived so far,
// the same "replay, then immediately react" shape timersvc.q's publish
// step demonstrates.
//
// The pre-generated historical data is deliberately in WIDE, per-level
// column form (bid_px_00.._04, bid_sz_00.._04, ask_px_00.._04,
// ask_sz_00.._04 - one scalar column per level, Databento-style naming,
// same convention reshape_wide_order_book_example.q uses) rather than
// already sitting in the vector-of-vectors book_prices/bid_sizes/
// ask_prices/ask_sizes shape uqf's pricing functions expect - a real
// feed or CSV export is far more likely to arrive column-wide than
// pre-shaped. Each tick's timer callback reshapes JUST that tick's own
// row via src/market_data/book.q's derive_level_groups/book_from_wide_levels (the
// same reshape machinery, applied per-row instead of to a whole table
// upfront) before appending the result to the live quotes table -
// demonstrating that the wide-to-vector reshape composes naturally with
// a live/incremental feed, not only a one-shot batch load.
//
// Narration/status goes through .qetl.log (src/etl/core/log.q), the one q
// logging layer in this tree - actual table contents still go through
// `show`, since a log line serializes a whole value onto one line rather
// than the readable grid `show` produces.
//
// Run from the repository root, with stdin kept open:
//   yes "" | q scripts/examples/timer_replay_example.q [n_ticks] [tick_ms]
// n_ticks (default 8) is how many historical rows to generate and
// replay; tick_ms (default 50) is the system timer interval between
// replayed rows, in milliseconds. Both are optional and positional, e.g.
// `yes "" | q scripts/examples/timer_replay_example.q 20 100` for 20 ticks 100ms
// apart. Run in an actual interactive terminal, plain
// `q scripts/examples/timer_replay_example.q` is fine too (stdin naturally stays
// open until you type something). But run non-interactively - stdin
// closed/redirected from /dev/null, as any CI job or subprocess-launched
// invocation would do - a genuine, confirmed kdb+ race exists between
// "stdin hit EOF, exit the process" and the timer's next .z.time tick,
// decided by scheduler timing: sometimes only tick 1 of n_ticks fires
// before the process exits. Piping an infinite stream of blank lines
// through stdin (evaluated as harmless no-ops) means it never reaches
// EOF, so only this script's own explicit `exit 0` inside .z.time (once
// every historical row has replayed) ends the process - confirmed
// reliable across repeated runs; without it, failed non-deterministically
// (varied 1-5 of 5 ticks firing across identical runs).
//
// The growing quotes table is persisted to output/timer_replay_quotes
// (gitignored - a run artifact, not source) after every tick, so it
// survives the process exiting rather than only living in memory - reload
// it in any q session with `quotes:get \`:output/timer_replay_quotes`.

\c 400 1000
\l src/init.q
\l src/etl/core/log.q

/ .qetl.log suppresses DBG lines by default; this example's narration uses them.
.qetl.log.debug 1b;

/ ==== Step 1: pre-generate the "historical" tick series, once ====
/ Same EURUSD single-pair spot/scale as cross_markout_example.q's
/ mk_book, scaled by .qexdef.pip_size/.qexdef.size_unit (src/examples/example_defaults.q)
/ - stands in for timersvc.q's tradeGE.N0821.csv, a fixed dataset with
/ its own recorded timestamps, generated/loaded once before replay
/ starts. Unlike mk_book elsewhere, this builds one WIDE row (a dict of
/ 20 scalar bid_px_NN/bid_sz_NN/ask_px_NN/ask_sz_NN columns, not the
/ vector-of-vectors book shape) - see the header comment for why.
mk_wide_row:{[spot]
    levels:til 5;
    lvl_suffix:{[i] $[i<10;"0","",string i;string i]} each levels;
    bid_px_names:`$"bid_px_",/:lvl_suffix;
    bid_sz_names:`$"bid_sz_",/:lvl_suffix;
    ask_px_names:`$"ask_px_",/:lvl_suffix;
    ask_sz_names:`$"ask_sz_",/:lvl_suffix;
    bid_px_vals:spot-.qexdef.pip_size*levels;
    bid_sz_vals:.qexdef.size_unit*1+levels;
    ask_px_vals:(spot+.qexdef.pip_size)+.qexdef.pip_size*levels;
    ask_sz_vals:.qexdef.size_unit*1+levels;
    (bid_px_names,bid_sz_names,ask_px_names,ask_sz_names)!(bid_px_vals,bid_sz_vals,ask_px_vals,ask_sz_vals)};

/ derive_level_groups' (prefix;target_col) rules for the wide columns
/ above, and the target column order to project each reshaped row down
/ to - both computed once, reused by every tick's reshape (a real system
/ would derive its schema once from the feed's own column names too, not
/ re-derive it per incoming row).
level_prefix_targets:(
    ("bid_px_";`bid_prices);
    ("bid_sz_";`bid_sizes);
    ("ask_px_";`ask_prices);
    ("ask_sz_";`ask_sizes));
col_order:`time`sym`bid_prices`bid_sizes`ask_prices`ask_sizes;

/ Command-line params: q scripts/examples/timer_replay_example.q [n_ticks] [tick_ms]
/ - .z.x is the list of args after the script name, always strings; cast
/ and fall back to the default whenever an arg wasn't given.
default_n_ticks:8;
default_tick_ms:50;
n_ticks:$[0<count .z.x; "J"$first .z.x; default_n_ticks];
tick_ms:$[1<count .z.x; "J"$.z.x 1; default_tick_ms];

output_dir:"output";
output_path:`$":",output_dir,"/timer_replay_quotes";
system "mkdir -p ",output_dir;

mean_gap:0D00:00:00.200; std_gap:0D00:00:00.050; min_gap:0D00:00:00.050;
p:1e-9+(1-2e-9)*n_ticks?1.0;
.qetl.log.dbg[`timer_replay;"running: .qstats.inv_ncdf p";()!()];
z:.qstats.inv_ncdf p;
gaps:min_gap|mean_gap+std_gap*z;
hist_ts:.z.p+sums gaps;
hist_spots:1.0850+0.00002*til n_ticks;

/ hist_wide - the wide-form "historical feed", time/sym plus 20 scalar
/ level columns per row (bid_px_00.._04, ...). sym is deliberately a
/ STRING, not a symbol - the same mis-typed-identifier-column shape
/ book_from_wide_levels/symbolize_columns exist to fix, matching how a
/ real CSV/vendor feed would actually arrive.
hist_wide:([] time:hist_ts; sym:n_ticks#enlist "EURUSD"),'(mk_wide_row each hist_spots);
.qetl.log.info[`timer_replay;"historical - ",.Q.s1[n_ticks]," pre-generated EURUSD ticks in wide column form, ready to replay";()!()];
.qetl.log.dbg[`timer_replay;"running: .qbook.derive_level_groups[cols hist_wide;level_prefix_targets]";()!()];
level_groups:.qbook.derive_level_groups[cols hist_wide;level_prefix_targets];

/ ==== Step 2: replay onto a live, growing quotes table on a timer ====
/ quotes starts empty - unlike every other scripts/*.q example, which
/ builds its whole quotes table in one shot before ever calling a uqf
/ function against it.
quotes:0#([] time:`timestamp$(); sym:`symbol$(); bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:());
cnt:0;

/ Timer callback: reshape this tick's own wide row (its time is its own
/ pre-recorded hist_ts, not .z.p - the replay's real-time pacing and the
/ data's own timestamps are independent, exactly like timersvc.q) via
/ book_from_wide_levels - the same function reshape_wide_order_book_
/ example.q applies to a whole table upfront, here applied to one row at
/ a time as it "arrives" - then append the result to the live quotes
/ table, then re-run .qmicro.mid_price against the table as it now
/ stands. Stops its own timer and exits once every historical row has
/ been replayed - see README's Requirements/Quick start for why every
/ scripts/*.q example ends this way rather than falling into an
/ interactive prompt.
.z.time:{
    wide_row:1#cnt _ hist_wide;
    .qetl.log.dbg[`timer_replay;"running: .qbook.book_from_wide_levels[wide_row;level_groups;`sym]";()!()];
    row:col_order#.qbook.book_from_wide_levels[wide_row;level_groups;`sym];
    quotes,:row;
    .qetl.log.dbg[`timer_replay;"running: .qmicro.mid_price[quotes`bid_prices;quotes`ask_prices]";()!()];
    mid:.qmicro.mid_price[quotes`bid_prices;quotes`ask_prices];
    output_path set quotes;
    .qetl.log.info[`timer_replay;"tick ",.Q.s1[cnt+1],"/",.Q.s1[n_ticks]," - quotes has ",.Q.s1[count quotes]," row(s) now, latest mid ",.Q.s1[last mid],", persisted to ",.Q.s1[output_path];()!()];
    cnt+:1;
    if[cnt>=n_ticks;
        system "t 0";
        .qetl.log.info[`timer_replay;"replay complete - stopping the timer; final table persisted at ",.Q.s1[output_path];()!()];
        show quotes;
        exit 0]};

.qetl.log.info[`timer_replay;"starting replay - one historical tick appended every ",.Q.s1[tick_ms],"ms of wall-clock time";()!()];
system "t ",string tick_ms;

// exit 0
