/ hdb_executions.q - the fills the executions job published, from the local HDB (.qpipe.source.hdb_executions).
/ .
/ The live bars job (src/etl/streaming/exec_bars.q) builds bars from fills as
/ they stream past, in memory that dies with its process: a minute it was down
/ for has no bar. This source is what hdb_exec_bars_backfill reads instead -
/ the HDB's `executions`, which end of day saved - and the worker aggregates
/ them with the transform the live job applies.
/ .
/ The window is on source_time, the clock the live bars are cut on, not the
/ plant's `time` the HDB is partitioned and sorted by. A fill is stamped at the
/ plant after the source stamped it, so a fill just before midnight can sit in
/ the next date's partition: the query reads one date either side.
/ .
/ Live: export UQF_SOURCE_CRED_HDB_EXECUTIONS=localhost:<hdb1's port> in the
/ shell `uqs backfill` runs from. Without it, the fixture below is used.

\d .qpipe.source.hdb_executions

source_name:`hdb_executions

columns:`source_time`sym`size`price
types:"psff"

target:`exec_bar
time_column:`source_time

/ The table carries a fill id only where the venue gave one, so two fills of a
/ sym at one instant share this key; the worker writes by its own target_key.
row_key:`sym`source_time

/ .u.upd stamps `time` in UTC, and the source's own stamps are UTC.
tz:`UTC

/ The window's fills, from the HDB. Parameterised, half-open on source_time;
/ the table is date-partitioned, so the partition is constrained first, one
/ date either side for the reason in the header.
/ @param h an open handle to the HDB
/ @param range_from inclusive lower bound
/ @param range_to exclusive upper bound
/ @return the fills, by source_time
query:{[h;range_from;range_to]
    .qetl.source.ipc[h;{[from_ts;to_ts]
        `source_time xasc select source_time, sym, size, price from `executions
            where date within (-1 1)+`date$(from_ts;to_ts), source_time>=from_ts, source_time<to_ts
      };range_from;range_to]}

/ The fixture's fills, which are the bars example of exec_bars.q: EURUSD in
/ two minutes (one fill exactly on the boundary) and one USDJPY fill.
/ @return fills on 2026.10.09
/ @eg count .qpipe.source.hdb_executions.fixture[]  ->  5
fixture:{[]
    ([] source_time:2026.10.09D10:00:05 2026.10.09D10:00:50 2026.10.09D10:00:20 2026.10.09D10:01:00 2026.10.09D10:00:30;
        sym:`EURUSD`EURUSD`EURUSD`EURUSD`USDJPY;
        size:1e6 3e6 2e6 1e6 5e5;
        price:1.10 1.12 1.11 1.20 150.0)}

.qetl.source.define[source_name;
    `source`table_name`target`time_column`row_key`columns`types`query`fixture`tz!
    (source_name;`executions;target;time_column;row_key;columns;types;query;fixture;tz)];

\d .
