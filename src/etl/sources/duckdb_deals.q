/ duckdb_deals.q - mock FX deals read from DuckDB over ODBC
/ (.qpipe.source.duckdb_deals).
/ .
/ The demo_deals dataset, reached the other way. demo_deals reads its deals
/ from a q process over IPC; this reads the same shape - deal_id, deal_time,
/ sym, side, notional, rate - from a DuckDB file over ODBC, the transport a
/ real relational deal store would need. The file is written by
/ scripts/dev/make_fx_deals_duckdb.py: demo_deals' five fixture rows followed
/ by generated deals, deterministic for given arguments. MOCK DATA - nothing
/ about a real deal system is claimed by it.
/ .
/ The credential, UQF_SOURCE_CRED_DUCKDB_DEALS, is an ODBC connection string:
/ "DRIVER=DuckDB;Database=/path/fx_deals.duckdb;access_mode=READ_ONLY".
/ Without it the worker runs on `fixture`, the demo path.
/ .
/ WHAT THE DRIVER GETS WRONG, AND THE QUERY FIXES - as for databento_mbp10:
/ .
/   deal_time   KX's ODBC client returns a SQL timestamp as a q DATETIME,
/               milliseconds as a float, which the contract refuses as a
/               window column. So the SQL selects epoch_ns(deal_time), a
/               BIGINT, and `adapt` turns it back into a timestamp exactly.
/   sym, side   arrive as strings; cast to symbols in `adapt`.
/ .
/ The window bounds are compared as make_timestamp_ns(<epoch ns>), not by
/ applying epoch_ns() to the column: a function on the column stops DuckDB
/ skipping row groups by their min/max. Each bound is a long rendered
/ through .qetl.io.odbc.literal, the one escape function, so no caller value is
/ spliced into SQL any other way - and a long, not a timestamp, because
/ .qetl.io.odbc.literal's timestamp form drops the sub-second part.
/ .
/ The deal's own time stays `deal_time`, NOT `time`. `time` is the
/ tickerplant's: .qtorq.publish drops any `time` a publisher sends and the
/ plant stamps its receipt time, so a deal time carried in `time` would be
/ overwritten the day these rows are published. uqs_tables.q defines the
/ plant table as time plus deal_time, as databento_book keeps ts_event.

\d .qpipe.source.duckdb_deals

source_name:`duckdb_deals

/ The columns this adapter READS, in the order the target table holds them.
columns:`deal_time`deal_id`sym`side`notional`rate
/ p=timestamp, j=long, s=symbol, s=symbol, f=float, f=float
types:"pjssff"

/ The table in the DuckDB file, and the table its rows land in here.
table_name:`deals
target:`duckdb_deals

time_column:`deal_time

/ deal_id is the DuckDB table's PRIMARY KEY, so here the source does
/ guarantee uniqueness - the thing a natural key needs and demo_deals can
/ only assume.
row_key:`deal_id

tz:`UTC
transport:`odbc

/ What UQF_SOURCE_CRED_DUCKDB_DEALS looks like. Declared rather than
/ left to the per-transport default, which is a networked-database string:
/ DuckDB is embedded, so this is a file path and a mode, with no host, user
/ or password to supply.
credential_example:"DRIVER=DuckDB;Database=/path/fx_deals.duckdb;access_mode=READ_ONLY"

/ A timestamp as DuckDB epoch nanoseconds, through .qetl.io.odbc.literal.
/ @param ts a timestamp
/ @return SQL text for the same instant, to the nanosecond
/ @eg .qpipe.source.duckdb_deals.epoch_ns_literal[2026.09.11D09:00:00.000000001]  ->  "make_timestamp_ns(1789117200000000001)"
epoch_ns_literal:{[ts] "make_timestamp_ns(",.qetl.io.odbc.literal["j"$ts-1970.01.01D00:00],")"}

/ The SQL for one window: half-open [range_from;range_to) on deal_time,
/ ordered so a window is the same table on every fetch.
/ @param range_from inclusive lower bound
/ @param range_to exclusive upper bound
/ @return the SELECT statement text
/ @eg .qpipe.source.duckdb_deals.sql_for[2026.09.11D00:00:00.000000000;2026.09.12D00:00:00.000000000]
sql_for:{[range_from;range_to]
    "SELECT epoch_ns(deal_time) AS deal_time, deal_id, sym, side, notional, rate",
    " FROM deals",
    " WHERE deal_time >= ",epoch_ns_literal[range_from],
    " AND deal_time < ",epoch_ns_literal[range_to],
    " ORDER BY deal_time, deal_id"}

/ The driver's table in the declared types and column order.
/ @param raw the table .qetl.io.odbc.run_sql returns for sql_for's statement
/ @return the same rows as the contract declares them
/ @eg .qpipe.source.duckdb_deals.adapt[([] deal_time:enlist 1789117200000000000; deal_id:enlist 1; sym:enlist "EURUSD"; side:enlist "buy"; notional:enlist 1e6; rate:enlist 1.0842)]
adapt:{[raw]
    t:update deal_time:1970.01.01D00:00+deal_time from raw;
    t:@[t;`sym`side;{`$x}];
    columns xcols t}

/ One window of deals from DuckDB.
/ @param h an ODBC handle from .qetl.io.odbc.open
/ @param range_from inclusive lower bound
/ @param range_to exclusive upper bound
/ @return the deals in the window, in the contract's shape
query:{[h;range_from;range_to] adapt .qetl.io.odbc.run_sql[h;sql_for[range_from;range_to]]}

/ demo_deals' five deals, one a day from 2026.09.11 - the same rows that open
/ the DuckDB file - in this source's column names.
fixture:{[]
    ([] deal_time:2026.09.11D09:00:00.000000000+1D*til 5;
        deal_id:1 2 3 4 5j;
        sym:`EURUSD`GBPUSD`EURUSD`USDJPY`EURUSD;
        side:`buy`sell`buy`sell`buy;
        notional:1000000 2500000 750000 3000000 1250000f;
        rate:1.0842 1.2631 1.0847 149.82 1.0851)}

.qetl.source.define[source_name;
    `source`table_name`target`time_column`row_key`columns`types`query`fixture`tz`transport`credential_example!
    (source_name;table_name;target;time_column;row_key;columns;types;query;fixture;tz;transport;credential_example)];

\d .
