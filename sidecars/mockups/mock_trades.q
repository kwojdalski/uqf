/ mock_trades.q - synthetic FX trades, generated per window (.qpipe.source.mock_trades).
/ .
/ A source with nothing behind it. Its transport, `mock`, opens no
/ connection: the credential is an integer SEED, and `query` makes the
/ window's rows from it. Each window's draws are seeded from the seed and
/ the window's start, so the same seed and window always give the same rows -
/ over any date range, with no data stored anywhere.
/ .
/ To backfill it, give it a seed as its credential:
/     UQF_SOURCE_CRED_MOCK_TRADES=42 uqs backfill mock_trades_backfill --version v1 \
/         --from 2026-09-01 --to 2026-09-08
/ Without one, the worker runs on `fixture` - one hour of the same rows.

\d .qpipe.source.mock_trades

source_name:`mock_trades

columns:`time`sym`side`qty`px
types:"pssff"

target:`mock_trades

time_column:`time

/ At most one trade per pair in a slot, so pair and slot identify a row.
row_key:`time`sym

/ Generated in UTC.
tz:`UTC

/ The pairs traded, and the level each starts its window at.
syms:`EURUSD`GBPUSD`USDJPY
base:1.0850 1.2650 149.50

/ One slot a minute; a trade in a slot with this probability, per pair.
step:0D00:01
rate:0.3

/ Run `f` with q's random seed set to `s`, putting the caller's seed back
/ whether or not `f` throws.
/ @param s a positive int seed
/ @param f a function of one argument, called with (::)
/ @return what `f` returns
with_seed:{[s;f]
    saved:system"S";
    system"S ",string s;
    r:@[f;::;{[saved;e] system"S ",string saved; 'e}[saved]];
    system"S ",string saved;
    r}

/ The seed a window's draws use: the source's seed and the window's start, so
/ every window of a run differs and the same window always repeats.
/ @param seed the source's seed, a long
/ @param range_from the window's start
/ @return a positive int
/ @eg .qpipe.source.mock_trades.window_seed[42;2026.09.01D00:00:00.000000000]
window_seed:{[seed;range_from] 1i+"i"$(seed+("j"$range_from) div 60000000000) mod 2147483646}

/ One pair's trades in the slots `slot`: a random walk from `level`, kept
/ in roughly `rate` of the slots. Draws from q's current seed.
/ @param slot the slot timestamps
/ @param sym the pair
/ @param level its price at the first slot
/ @return a table of the declared columns
pair_trades:{[slot;sym;level]
    n:count slot;
    t:([] time:slot; sym:n#sym; side:n?`buy`sell; qty:1e6*1+n?5; px:level*exp sums 0.0002*-0.5+n?1f);
    t where (n?1f)<rate}

/ The window's rows: every pair's trades, in time order. Each pair starts
/ at its base, moved by a slow daily cycle, so windows stay near one level.
/ @param seed the source's seed, a long
/ @param range_from the window's start, inclusive
/ @param range_to the window's end, exclusive
/ @return a table of the declared columns, every row in [range_from;range_to)
/ @eg count .qpipe.source.mock_trades.generate[42;2026.09.01D00:00:00.000000000;2026.09.01D01:00:00.000000000]
generate:{[seed;range_from;range_to]
    / In nanoseconds: PeachQ refuses a timespan `div` a timespan.
    n:0|("j"$range_to-range_from) div "j"$step;
    slot:range_from+step*til n;
    cycle:1+0.002*sin 2*acos[-1]*("j"$range_from)%86400000000000*30;
    rows:with_seed[window_seed[seed;range_from];{[slot;cycle;x] raze pair_trades[slot]'[syms;base*cycle]}[slot;cycle]];
    $[count rows; `time`sym xasc rows; 0#([] time:`timestamp$(); sym:`symbol$(); side:`symbol$(); qty:`float$(); px:`float$())]}

/ `seed` is what the `mock` transport opened: the source's credential.
/ Nothing is read or sent - the rows are generated.
query:{[seed;range_from;range_to] generate[seed;range_from;range_to]}

/ One hour of seed 42's rows: what a run with no credential reads, and what
/ the tests assert against.
fixture:{[] generate[42;2026.01.02D00:00:00.000000000;2026.01.02D01:00:00.000000000]}

\d .

.qetl.source.define[.qpipe.source.mock_trades.source_name;
    `source`table_name`target`time_column`row_key`columns`types`query`fixture`tz`transport!
    (.qpipe.source.mock_trades.source_name;`mock_trades;.qpipe.source.mock_trades.target;
     .qpipe.source.mock_trades.time_column;.qpipe.source.mock_trades.row_key;
     .qpipe.source.mock_trades.columns;.qpipe.source.mock_trades.types;
     .qpipe.source.mock_trades.query;.qpipe.source.mock_trades.fixture;
     .qpipe.source.mock_trades.tz;`mock)];
