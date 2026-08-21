// timer_replay_example.q - worked example of replaying pre-generated
// synthetic tick data into a LIVE, growing quotes table on a fixed timer
// interval, instead of building the whole synthetic dataset upfront in
// one vectorized batch the way every other scripts/*.q example does.
//
// Adapted from lib/qAutomatedTrading/histTickData/timersvc.q's pattern:
// load/generate historical data once, then a .z.ts timer callback
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
// Narration/status uses lib/log4q.q's INFO/DEBUG/ERROR (see README's
// Licensing section) - actual table contents still go through `show`,
// since log4q's %N message formatting serializes a whole value onto one
// line rather than the readable grid `show` produces.
//
// Requires real kdb+/KDB-X, not the local PeachQ binary: log4q relies on
// a mid-expression variable assignment/read pattern PeachQ doesn't
// evaluate correctly (see README's Licensing section).
//
// Run from the repository root, with stdin kept open:
//   yes "" | q scripts/timer_replay_example.q
// Run in an actual interactive terminal, plain `q scripts/timer_replay_example.q`
// is fine too (stdin naturally stays open until you type something). But
// run non-interactively - stdin closed/redirected from /dev/null, as any
// CI job or subprocess-launched invocation would do - a genuine, confirmed
// kdb+ race exists between "stdin hit EOF, exit the process" and "the
// system \"t 50\" timer's next .z.ts tick", decided by scheduler timing:
// sometimes only tick 1 of n_ticks fires before the process exits. Piping
// an infinite stream of blank lines through stdin (evaluated as harmless
// no-ops) means it never reaches EOF, so only this script's own explicit
// `exit 0` inside .z.ts (once every historical row has replayed) ends the
// process - confirmed reliable across repeated runs; without it, failed
// non-deterministically (varied 1-5 of 5 ticks firing across identical runs).

\c 400 1000
\l src/init.q
\l lib/log4q.q

/ Default log4q severity is INFO, which silently no-ops DEBUG calls -
/ lower it so this example's DEBUG lines actually print.
.log4q.sevl:`DEBUG;
key[.log4q.snk] set' .log4q.sev .log4q.sevl;

/ ==== Step 1: pre-generate the "historical" tick series, once ====
/ Same EURUSD single-pair shape as cross_markout_example.q's mk_book,
/ scaled by .qex.pip_size/.qex.size_unit (src/example_defaults.q) -
/ stands in for timersvc.q's tradeGE.N0821.csv, a fixed dataset with its
/ own recorded timestamps, generated/loaded once before replay starts.
mk_book:{[spot]
    levels:til 5;
    `bid_prices`bid_sizes`ask_prices`ask_sizes!(
        spot-.qex.pip_size*levels;.qex.size_unit*1+levels;
        (spot+.qex.pip_size)+.qex.pip_size*levels;.qex.size_unit*1+levels)};

n_ticks:8;
mean_gap:0D00:00:00.200; std_gap:0D00:00:00.050; min_gap:0D00:00:00.050;
p:1e-9+(1-2e-9)*n_ticks?1.0;
DEBUG "running: .qstats.inv_ncdf p";
z:.qstats.inv_ncdf p;
gaps:min_gap|mean_gap+std_gap*z;
hist_ts:.z.p+sums gaps;
hist_spots:1.0850+0.00002*til n_ticks;
hist_books:mk_book each hist_spots;
INFO ("historical - %1 pre-generated EURUSD ticks, ready to replay";n_ticks);

/ ==== Step 2: replay onto a live, growing quotes table on a timer ====
/ quotes starts empty - unlike every other scripts/*.q example, which
/ builds its whole quotes table in one shot before ever calling a uqf
/ function against it.
quotes:0#([] ts:`timestamp$(); sym:`symbol$(); bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:());
cnt:0;

/ Timer callback: append this tick's historical row (its own pre-recorded
/ hist_ts timestamp, not .z.p - the replay's real-time pacing and the
/ data's own timestamps are independent, exactly like timersvc.q) to the
/ live quotes table, then re-run .qmicro.mid_price against the table as
/ it now stands. Stops its own timer and exits once every historical row
/ has been replayed - see README's Requirements/Quick start for why every
/ scripts/*.q example ends this way rather than falling into an
/ interactive prompt.
.z.ts:{
    row:([] ts:enlist hist_ts cnt; sym:enlist `EURUSD),'enlist hist_books cnt;
    quotes,:row;
    DEBUG "running: .qmicro.mid_price[quotes`bid_prices;quotes`ask_prices]";
    mid:.qmicro.mid_price[quotes`bid_prices;quotes`ask_prices];
    INFO ("tick %1/%2 - quotes has %3 row(s) now, latest mid %4";(cnt+1;n_ticks;count quotes;last mid));
    cnt+:1;
    if[cnt>=n_ticks;
        system "t 0";
        INFO "replay complete - stopping the timer";
        show quotes;
        exit 0]};

INFO "starting replay - one historical tick appended every 50ms of wall-clock time";
system "t 50";

// exit 0
