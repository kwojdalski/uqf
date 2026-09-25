// smoke_databento_odbc.q - the backfill framework against real data, over
// ODBC.
//
// Runs databento_book_backfill over a range of the DuckDB database built by
// scripts/dev/dump_databento_duckdb.py, then checks what it published against
// DuckDB directly, through a second connection:
//
//   - the run completed with no failed window;
//   - every source row in the range was published, exactly once;
//   - the range is recorded as covered, and a second run is idle;
//   - the folded book agrees with the source's own level columns, for a
//     sample of rows per symbol - the transform is the part that could be
//     silently wrong while every count still matched.
//
// Not a qUnit suite and not in the default lane, for smoke_external_metadata.q's
// reasons: it needs a driver, a database file and minutes of fetching.
// It SKIPs, exit 0, when either is missing.
//
// Run, on macOS:
//   scripts/dev/odbc_rosetta.sh databento tests/q/smoke_databento_odbc.q [-from ts] [-to ts]
// The range defaults to the first trading hour of 2026-02-25.

\c 400 1000

\l src/init.q
\l scripts/processes/torq_pipeline.q
\l src/etl/init.q

opts:.Q.opt .z.x;
range_from:$[`from in key opts; "P"$first opts`from; 2026.02.25D14:30:00];
range_to:$[`to in key opts; "P"$first opts`to; 2026.02.25D15:30:00];

if[not .qsrc.has_credentials `databento_mbp10;
    -1 "SKIP  ",.qsrc.credential_var[`databento_mbp10]," is not set - run through scripts/dev/odbc_rosetta.sh databento";
    exit 0];
if[not .qodbc.available[];
    -1 "SKIP  the q ODBC client is not loadable in this process - run through scripts/dev/odbc_rosetta.sh";
    exit 0];

/ A status directory of its own, emptied first: the coverage ledger persists
/ there, and a previous run of this script would otherwise make this one
/ correctly idle - and fail every count below.
setenv[`UQFSTATUSDIR;"build/smoke-status"];
system"rm -rf build/smoke-status";
system"mkdir -p build/smoke-status";

failures:0;
check:{[name;ok;detail]
    -1 $[ok;"PASS  ";"FAIL  "],name,$[count detail;" - ",detail;""];
    if[not ok; `failures set failures+1];
    }

/ --- run ---------------------------------------------------------------------

-1 "range ",string[range_from]," .. ",string range_to;
.qwrk.databento_book_backfill.init[`source_version`range_from`range_to!(`smoke;range_from;range_to)];
t0:.z.p;
r:.qwrk.databento_book_backfill.run[];
-1 "run took ",string .z.p-t0;
check["run completed";`completed~r`state;.Q.s1 r];
check["no failed window";0=r`windows_failed;string r`windows_failed];

/ --- reconcile against DuckDB -------------------------------------------------

h:.qodbc.open .qsrc.require_credentials `databento_mbp10;
between_sql:" WHERE ts_event >= ",.qfeed.databento_mbp10.epoch_ns_literal[range_from]," AND ts_event < ",.qfeed.databento_mbp10.epoch_ns_literal[range_to];

source_rows:first exec n from .qodbc.run_sql[h;"SELECT count(*) AS n FROM mbp10",between_sql];
check["every source row published";source_rows=r`rows_published;
    "source ",string[source_rows],", published ",string r`rows_published];
check["target holds what was published";(count databento_book)=r`rows_published;string count databento_book];

keyed:select distinct sym, time, sequence, action, side, price, size from databento_book;
check["no row published twice";(count keyed)=count databento_book;
    string[(count databento_book)-count keyed]," duplicate(s) on the row key"];

check["every row inside the range";all databento_book[`time] within (range_from;range_to-1);""];
check["range recorded as covered";
    .qmatz.is_covered[`databento_book;`;`smoke;.z.p;range_from;range_to];""];

per_sym_source:.qodbc.run_sql[h;"SELECT symbol, count(*) AS n FROM mbp10",between_sql," GROUP BY symbol ORDER BY symbol"];
per_sym_book:select n:count i by sym from databento_book;
check["row count per symbol matches";
    ((`$per_sym_source`symbol)!per_sym_source`n)~exec sym!n from per_sym_book;
    .Q.s1 per_sym_source];

/ The fold, checked against the source: for the last record of each symbol in
/ the range, re-read its ten levels straight from DuckDB and compare.
sample:select from databento_book where i=(last;i) fby sym;
level_sql:{[h;row]
    sql:"SELECT ",(", " sv string .qfeed.databento_mbp10.level_fields)," FROM mbp10",
        " WHERE symbol = ",.qodbc.literal[string row`sym],
        " AND ts_event = ",.qfeed.databento_mbp10.epoch_ns_literal[row`time],
        " AND sequence = ",.qodbc.literal[row`sequence],
        " AND action = ",.qodbc.literal[string row`action],
        " AND side = ",.qodbc.literal[string row`side],
        " AND price = ",.qodbc.literal[row`price],
        " AND size = ",.qodbc.literal[row`size];
    src:.qodbc.run_sql[h;sql];
    if[not 1=count src; :0b];
    lv:{[src;p] "f"$raze src `$p,/:.qfeed.databento_mbp10.levels}[src];
    (row[`bid_prices]~lv "bid_px_") and (row[`ask_prices]~lv "ask_px_") and
        (("f"$row`bid_sizes)~lv "bid_sz_") and ("f"$row`ask_sizes)~lv "ask_sz_"}[h];
matches:level_sql each sample;
check["folded levels match the source";all matches;
    "mismatched sym(s): ",", " sv string exec sym from sample where not matches];

.qodbc.close h;

/ --- idempotence ---------------------------------------------------------------

r2:.qwrk.databento_book_backfill.run[];
check["second run is idle";`idle~r2`state;.Q.s1 r2];
check["second run published nothing";(count databento_book)=r`rows_published;string count databento_book];

.qwrk.databento_book_backfill.cleanup[];

-1 "";
-1 $[failures=0;"PASS  ";"FAIL  "],string[failures]," check(s) failed; ",string[count databento_book]," rows in databento_book";
exit $[failures=0;0;1]
