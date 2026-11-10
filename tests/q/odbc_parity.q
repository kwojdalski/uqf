/ odbc_parity.q - run one scenario list against ONE ODBC client and print what it
/ answered, so tests/qrun/test_odbc_parity.py can compare KX's odbc.k with our
/ src/etl/core/odbc.q (#2508).
/ .
/ Not a suite: it needs a driver, so it is run only through
/ scripts/dev/odbc_rosetta.sh (DuckDB driver, x86_64 under Rosetta on macOS).
/ .
/   scripts/dev/odbc_rosetta.sh q tests/q/odbc_parity.q -impl k   [-q]
/   scripts/dev/odbc_rosetta.sh q tests/q/odbc_parity.q -impl q   [-q]
/ .
/ Each line is `name|<-3! of the outcome>`, where an outcome is (1b;result) or
/ (0b;error text). A handle is printed as H, which is the only thing allowed to
/ differ between two runs.
/ .
/ Timeouts: DuckDB's driver does not interrupt a statement, so no run here can
/ EXPIRE one. What is compared is that a timeout is accepted in both places and
/ changes nothing about the result.

o:.Q.opt .z.x;
impl:first `$o`impl;
if[not impl in `k`q; -1 "usage: -impl k|q"; exit 2];
$[impl=`k; system"l odbc.k"; system"l src/etl/core/odbc.q"];

conn:"DRIVER=DuckDB;Database=:memory:";
out:{[name;r] -1 string[name],"|",-3!r};
try:{[f;a] .[{(1b;x . y)}f;enlist a;{(0b;x)}]};

h:.odbc.open conn;
out[`open;(1b;`H)];

q1:"select 1 as a, DATE '2020-01-02' as d, TIMESTAMP '2020-01-02 00:00:00' as whole, TIMESTAMP '2020-01-02 03:04:05' as part";
out[`eval_whole_day;try[.odbc.eval;(h;q1)]];
out[`eval_timeout;try[.odbc.eval;((h;5);q1)]];
out[`eval_mixed_column;try[.odbc.eval;(h;"select ts from (values (TIMESTAMP '2020-01-02 00:00:00'),(TIMESTAMP '2020-01-03 00:00:01')) v(ts)")]];
out[`eval_null_and_whole;try[.odbc.eval;(h;"select ts from (values (TIMESTAMP '2020-01-02 00:00:00'),(NULL::timestamp)) v(ts)")]];
out[`eval_empty;try[.odbc.eval;(h;"select TIMESTAMP '2020-01-02 00:00:00' as ts, 1 as a where 1=0")]];
out[`eval_ddl;try[.odbc.eval;(h;"create table t(a int, d timestamp)")]];
out[`eval_insert;try[.odbc.eval;(h;"insert into t values (1, TIMESTAMP '2020-02-03 00:00:00'), (2, TIMESTAMP '2020-02-04 00:00:00')")]];
out[`eval_table;try[.odbc.eval;(h;"select * from t order by a")]];
out[`eval_sql_error;try[.odbc.eval;(h;"select * from no_such_table")]];
out[`tables;try[.odbc.tables;enlist h]];

h2:.odbc.open (conn;5);
out[`open_with_timeout;(1b;`H)];
out[`close_with_timeout_handle;try[.odbc.close;enlist h2]];

out[`eval_unknown_handle;try[.odbc.eval;(h+1000;"select 1")]];
out[`eval_unknown_handle_timeout;try[.odbc.eval;((h+1000;5);"select 1")]];
out[`eval_closed_handle;try[.odbc.eval;(h2;"select 1")]];
out[`close_unknown;try[.odbc.close;enlist h+1000]];
out[`close_twice;try[.odbc.close;enlist h2]];
out[`open_bad_driver;try[.odbc.open;enlist "DRIVER=nope"]];
out[`open_bad_dsn;try[.odbc.open;enlist `no_such_dsn]];

out[`close;try[.odbc.close;enlist h]];
out[`close_then_eval;try[.odbc.eval;(h;"select 1")]];
exit 0
