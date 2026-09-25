/ crypto_market_data.q - cryptorust's recorded crypto book-and-trade rows read
/ from DuckDB over ODBC (.qpipe.source.crypto_market_data).
/ .
/ crypto_book is filled LIVE by cryptorust's kdb-market-data-recorder, and
/ crypto_mock stands in for that recorder when cryptorust is not running. This
/ is the third way to the same data and the only one that is repeatable: the
/ capture cryptorust already wrote to disk, in
/ cryptorust/data/live.duckdb, replayed a window at a time. A recorded
/ file can be backfilled, re-backfilled after a bug fix, and asserted against;
/ a live socket cannot.
/ .
/ It carries MORE than crypto_book does, which is why it has its own target
/ table rather than being poured into that one:
/ .
/   source_time   the venue's own stamp, and local_time the recorder's. Their
/                 difference is the wire lag. crypto_book has neither - it has
/                 only the plant's receipt time - so replaying into it would
/                 throw away the one thing a recorded capture is for.
/   latency_*     the recorder's own telemetry, in milliseconds: the last
/                 sample, the running minimum, and how many samples have been
/                 taken. Not market data; cryptorust's measurement of itself.
/   trade_*       the most recent trade print alongside the book, which is how
/                 the recorder writes it - one row carries both.
/ .
/ The credential, UQF_SOURCE_CRED_CRYPTO_MARKET_DATA, is an ODBC connection
/ string: "DRIVER=DuckDB;Database=/path/live.duckdb;access_mode=READ_ONLY".
/ scripts/dev/odbc_rosetta.sh sets the driver up on macOS. Without the
/ credential the worker runs on `fixture`, the demo path.
/ .
/ WHAT THE DRIVER GETS WRONG, AND THE QUERY FIXES
/ .
/   timestamp_ms        already a BIGINT of epoch milliseconds, NOT a SQL
/   local_timestamp_ms  timestamp - so unlike databento_mbp10 there is nothing
/                       for the driver to round into a q datetime. The long is
/                       fetched as it stands and `adapt` multiplies it up.
/                       Millisecond resolution is the source's, not a loss
/                       here: cryptorust writes milliseconds.
/   is_snapshot         a DuckDB BOOLEAN. What KX's ODBC client returns for one
/                       is not something this tree has measured, and a wrong
/                       guess about a flag is silent. CAST it to INTEGER, which
/                       is unambiguous, and compare to 1 in `adapt`.
/   venue, symbol,      arrive as strings; cast to symbols in `adapt`.
/   trade_side
/ .
/ The window bounds are compared against timestamp_ms directly, as plain
/ longs - no function on the column, so DuckDB can still skip row groups by
/ their min/max - and each is rendered through .qetl.io.odbc.literal, the one
/ escape function, so no caller value is spliced into SQL any other way.
/ .
/ The venue's time stays `source_time`, NOT `time`. `time` is the
/ tickerplant's: .qtorq.publish drops any `time` a publisher sends and the
/ plant stamps its receipt time, so a venue time carried in `time` would be
/ overwritten the day these rows are published.

\d .qpipe.source.crypto_market_data

source_name:`crypto_market_data

/ "1".."5" - cryptorust records five levels a side, where Databento records ten.
levels:string 1+til 5

/ Per level: bid price, bid size, ask price, ask size, named as the DuckDB
/ table names them.
level_fields:raze {`$("bid_price_";"bid_size_";"ask_price_";"ask_size_"),\:x} each levels

/ The columns this adapter READS, in the order the contract declares them.
columns:`source_time`local_time`venue`sym`is_snapshot,level_fields,`latency_ms`latency_min_ms`latency_count`trade_price`trade_size`trade_side
/ p,p,s,s,b then twenty floats then f,f,j,f,f,s
types:"ppssb",(raze 5#enlist "ffff"),"ffjffs"

/ The table in the DuckDB file, and the table its rows land in here.
/ .
/ cryptorust exposes the same 31 columns under two names, and which one a file
/ has is the only thing that differs: `market_data`, a real table in the dumped
/ data/market_data.duckdb, and `market_data_live`, a VIEW in data/live.duckdb.
/ sql_for reads THIS symbol rather than spelling the name again, so moving
/ between them is one line.
/ .
/ THREE THINGS ABOUT THE VIEW, each of which stops a run that looks correct.
/ None is this tree's to fix, and all three were measured on 2026.09.25:
/ .
/   the path is RELATIVE. The view is
/   `read_parquet('data/live*.parquet', union_by_name=true)`, so it resolves
/   only when the PROCESS's working directory is cryptorust's repository root.
/   scripts/processes/torq_backfill.q cds to uqf's root before loading, so a
/   fleet run cannot satisfy that and fails with DuckDB's "No files found that
/   match the pattern". Defining the view with an absolute path, in cryptorust,
/   is the fix; pointing this source at an absolute read_parquet glob and a
/   :memory: database is the workaround.
/ .
/   the definition lives in the WAL until the writer checkpoints. Copy
/   live.duckdb without live.duckdb.wal and the catalog is empty - not an
/   error, just a database with no such view, which reads as a typo here.
/ .
/   the recorder's own lock excludes readers. While cryptorust is writing,
/   opening the file returns "Conflicting lock is held", and access_mode=
/   READ_ONLY does NOT get around it. A backfill over live data therefore
/   wants the parquet shards, which are append-only and unlocked, rather than
/   the database file that indexes them.
table_name:`market_data_live
target:`crypto_market_data

time_column:`source_time

/ NOT a key cryptorust guarantees, and deliberately said so. The recorder
/ writes one row per update it receives, so (venue; sym; source_time) is NOT
/ unique - in the 167-row capture of 2026.09.25 it collapses to 68 rows, and
/ adding every market field still leaves 78. What made all 167 distinct was
/ latency_count, a per-venue counter of latency samples: an accident of how
/ the recorder stamps a row, not a record id. An observation about this
/ capture, then, not a promise from cryptorust - re-check it on a new one
/ before relying on it to deduplicate.
row_key:`venue`sym`source_time`latency_count

tz:`UTC
transport:`odbc

/ What UQF_SOURCE_CRED_CRYPTO_MARKET_DATA looks like. Declared rather than
/ left to the per-transport default, which is a networked-database string:
/ DuckDB is embedded, so this is a file path and a mode, with no host, user
/ or password to supply.
credential_example:"DRIVER=DuckDB;Database=/path/live.duckdb;access_mode=READ_ONLY"

/ The SQL expression selecting each field, in `columns` order. Only four of
/ them need one; the level and latency columns are read as they stand.
/ @return the comma-separated select list
/ @eg .qpipe.source.crypto_market_data.select_list[] like "timestamp_ms AS source_time, *"  ->  1b
select_list:{[]
    exprs:{[f]
        s:string f;
        $[f=`source_time; "timestamp_ms AS source_time";
          f=`local_time;  "local_timestamp_ms AS local_time";
          f=`sym;         "symbol AS sym";
          f=`is_snapshot; "CAST(is_snapshot AS INTEGER) AS is_snapshot";
          s]} each .qpipe.source.crypto_market_data.columns;
    ", " sv exprs}

/ A window bound as DuckDB epoch milliseconds, through .qetl.io.odbc.literal.
/ .
/ CEILING division, not `div`'s floor. A row stamped m milliseconds sits at
/ the instant m*1000000ns, so it belongs to [from;to) exactly when
/ m >= from%1000000 and m < to%1000000 - which in whole milliseconds is
/ ceiling on BOTH bounds. Flooring the lower bound would refetch the
/ millisecond before it on every window, and the duplicate would surface far
/ from here. Window boundaries in this tree are whole milliseconds anyway, so
/ the two agree in practice; they are not the same rule, and the one that is
/ right for any bound is the one written down.
/ @param ts a timestamp
/ @return SQL text for the first millisecond at or after it
/ @eg .qpipe.source.crypto_market_data.epoch_ms_bound[2026.09.11D21:00:00.000000000]  ->  "1789160400000"
/ @eg .qpipe.source.crypto_market_data.epoch_ms_bound[2026.09.11D21:00:00.000000001]  ->  "1789160400001"
epoch_ms_bound:{[ts] .qetl.io.odbc.literal neg[(neg "j"$ts-1970.01.01D00:00) div 1000000]}

/ The SQL for one window: half-open [range_from;range_to) on timestamp_ms,
/ ordered so a window is the same table on every fetch.
/ @param range_from inclusive lower bound
/ @param range_to exclusive upper bound
/ @return the SELECT statement text
/ @eg .qpipe.source.crypto_market_data.sql_for[2026.09.11D21:00:00.000000000;2026.09.11D22:00:00.000000000]
sql_for:{[range_from;range_to]
    "SELECT ",select_list[]," FROM ",string[table_name],
    " WHERE timestamp_ms >= ",epoch_ms_bound[range_from],
    " AND timestamp_ms < ",epoch_ms_bound[range_to],
    " ORDER BY timestamp_ms, symbol, latency_count"}

/ The driver's table in the declared types and column order.
/ @param raw the table .qetl.io.odbc.run_sql returns for sql_for's statement
/ @return the same rows as the contract declares them
/ @eg .qpipe.source.crypto_market_data.adapt[update source_time:1789160662231,local_time:1789160662231,venue:enlist "coinbase_spot",sym:enlist "BTC-USD",is_snapshot:1i,trade_side:enlist "buy" from 1#.qpipe.source.crypto_market_data.fixture[]]  ->  the same row with source_time 2026.09.11D21:04:22.231000000 and the four text columns as symbols
adapt:{[raw]
    t:@[raw;`source_time`local_time;{1970.01.01D00:00+1000000*x}];
    t:@[t;`venue`sym`trade_side;{`$x}];
    t:@[t;`is_snapshot;{1=x}];
    columns xcols t}

/ One window of recorded rows from DuckDB.
/ @param h an ODBC handle from .qetl.io.odbc.open
/ @param range_from inclusive lower bound
/ @param range_to exclusive upper bound
/ @return the rows in the window, in the contract's shape
query:{[h;range_from;range_to] adapt .qetl.io.odbc.run_sql[h;sql_for[range_from;range_to]]}

/ Private: one row's five levels, flattened in level_fields order.
/ @param bid the best bid
/ @param ask the best ask
/ @param tick the price step between levels
/ @param sz the size at every level
/ @return the twenty level values, bid price/size then ask price/size per level
fixture_levels:{[bid;ask;tick;sz]
    raze {[bid;ask;tick;sz;i] (bid-tick*i;sz;ask+tick*i;sz)}[bid;ask;tick;sz] each til 5}

/ @return six recorded rows in the contract's shape
/ @eg count .qpipe.source.crypto_market_data.fixture[]  ->  6
/ .
/ Six rows taken from the capture of 2026.09.25: the first row of each of the
/ three pairs cryptorust was recording, and the same three an hour later with
/ the touch moved. An hour apart because the worker's window is 0D01, so a
/ run over both windows fetches three rows each and neither is empty.
/ .
/ DATED EARLIER THAN THE CAPTURE THEY CAME FROM, and that is the one liberty
/ taken with them. .qetl.io.hdb refuses a batch dated today or later - today
/ belongs to the tickerplant and end-of-day, not to a backfill - so a fixture
/ carrying the capture's own date would make the HDB path undemonstrable on
/ the day it was written, and only that day. Every other source's fixture is
/ past-dated for the same reason. The clock times and every value are the
/ capture's; only the date moved.
/ .
/ The capture's own quirks are kept rather than tidied away: is_snapshot is
/ true on every row (this recorder only ever wrote snapshots), and
/ latency_ms/latency_min_ms are 0 throughout (venue and recorder clocks agreed
/ to the millisecond). A fixture that invented variation there would assert
/ something the source has never produced.
fixture:{[]
    base:([] source_time:2026.09.11D21:04:21.968000000 2026.09.11D21:04:21.989000000 2026.09.11D21:04:22.231000000
                          2026.09.11D22:04:21.968000000 2026.09.11D22:04:21.989000000 2026.09.11D22:04:22.231000000;
        local_time:2026.09.11D21:04:21.968000000 2026.09.11D21:04:21.989000000 2026.09.11D21:04:22.231000000
                          2026.09.11D22:04:21.968000000 2026.09.11D22:04:21.989000000 2026.09.11D22:04:22.231000000;
        venue:6#`$"coinbase_spot";
        sym:6#`$("SOL-USD";"ETH-USD";"BTC-USD");
        is_snapshot:6#1b);
    lv:flip level_fields!flip (fixture_levels[121.5;121.51;0.01;200f];
        fixture_levels[2681.9;2681.91;0.1;1f];
        fixture_levels[83823.74;83823.75;1.68;0.09];
        fixture_levels[121.62;121.63;0.01;200f];
        fixture_levels[2682.4;2682.41;0.1;1f];
        fixture_levels[83830.02;83830.03;1.68;0.09]);
    tail:([] latency_ms:6#0f; latency_min_ms:6#0f; latency_count:2 3 3 4 5 6j;
        trade_price:121.54 2681.98 83827.89 121.66 2682.48 83834.17;
        trade_size:0.01 0.558313 0.00000009 0.01 0.558313 0.00000009;
        trade_side:`$("buy";"sell";"buy";"buy";"sell";"buy"));
    base,'lv,'tail}

.qetl.source.define[source_name;
    `source`table_name`target`time_column`row_key`columns`types`query`fixture`tz`transport`credential_example!
    (source_name;table_name;target;time_column;row_key;columns;types;query;fixture;tz;transport;credential_example)];

\d .
