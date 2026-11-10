/ test_odbc.q - our own odbc.q client (.qt.odbc), with odbc.so DOUBLED (#2508).
/ .
/ No driver and no odbc.so is needed: odbc.q binds nothing until first use, so
/ these assign .odbc.natives a double and test the layer that is ours - handle
/ registry, refusals, timeout routing, the whole-day conversion. That the
/ doubled natives behave like odbc.so is what tests/qrun/test_odbc_parity.py
/ checks against KX's odbc.k, where an ODBC setup exists.
/ .
/ Whole-day columns are built with "z"$ because the native library answers
/ legacy datetime (15h); that is the type the code under test must recognise.

\l src/etl/core/odbc.q

/ The calls the double saw, newest last: (name;args).
.qt.odbc.calls:()

.qt.odbc.natives:{[result]
    `open`close`eval`tables!(
        {[c;t] .qt.odbc.calls,:enlist (`open;(c;t)); 41j};
        {.qt.odbc.calls,:enlist (`close;x); ::};
        {[r;h;sql;tmo] .qt.odbc.calls,:enlist (`eval;(h;sql;tmo)); r}[result];
        {.qt.odbc.calls,:enlist (`tables;x); ([] name:`a`b)})}

/ Run f with the doubled natives and an empty registry, then put both back.
.qt.odbc.with:{[result;f]
    saved:(.odbc.natives;.odbc.handles);
    .odbc.natives:.qt.odbc.natives result;
    .odbc.handles:();
    .qt.odbc.calls:();
    r:@[{(1b;x[])};f;{(0b;x)}];
    .odbc.natives:saved 0;
    .odbc.handles:saved 1;
    if[not first r; 'last r];
    last r}

.qt.odbc.test_open_registers_the_handle_and_close_unregisters_it:{[t]
    r:.qt.odbc.with[::;{
        h:.odbc.open "DRIVER=x"; opened:.odbc.handles; .odbc.close h; (h;opened;.odbc.handles)}];
    .qunit.assertEquals[r;(41j;enlist 41j;`long$());"open registers, close unregisters"]};

.qt.odbc.test_a_second_close_is_refused:{[t]
    .qt.odbc.with[::;{
        h:.odbc.open "DRIVER=x"; .odbc.close h;
        .qunit.assertThrows[.odbc.close;h;"invalid";"closing a closed handle is refused"]}]};

.qt.odbc.test_eval_and_close_refuse_a_handle_never_opened:{[t]
    .qt.odbc.with[::;{
        .qunit.assertThrows[{.odbc.eval[7j;x]};"select 1";"invalid";"eval of an unknown handle"];
        .qunit.assertThrows[{.odbc.eval[(7j;5);x]};"select 1";"invalid";"eval of an unknown handle with a timeout"];
        .qunit.assertThrows[.odbc.close;7j;"invalid";"close of an unknown handle"];
        .qunit.assertEquals[.qt.odbc.calls;();"the native library was never reached"]}]};

.qt.odbc.test_a_timeout_reaches_the_native_library_and_defaults_to_zero:{[t]
    r:.qt.odbc.with[::;{
        h:.odbc.open "DRIVER=x"; .odbc.open ("DRIVER=x";9);
        .odbc.eval[h;"select 1"]; .odbc.eval[(h;5);"select 2"];
        .qt.odbc.calls}];
    .qunit.assertEquals[r;(
        (`open;("DRIVER=x";0));(`open;("DRIVER=x";9));
        (`eval;(41j;"select 1";0));(`eval;(41j;"select 2";5)));
        "open and eval pass their timeout, 0 when none is given"]};

.qt.odbc.test_a_dotted_dsn_symbol_is_refused:{[t]
    .qt.odbc.with[::;{
        .qunit.assertThrows[.odbc.open;`a.b;"odbc.open: a dotted DSN symbol is unsupported";"dotted symbol"];
        .qunit.assertEquals[.odbc.handles;();"nothing was registered"]}]};

.qt.odbc.test_a_dsn_symbol_is_passed_through:{[t]
    r:.qt.odbc.with[::;{.odbc.open `mydsn; first .qt.odbc.calls}];
    .qunit.assertEquals[r;(`open;(`mydsn;0));"a plain DSN symbol reaches the native library"]};

.qt.odbc.test_whole_day_datetime_columns_become_dates:{[t]
    day:"z"$2020.01.02;
    raw:([] a:1 2; whole:day,"z"$2020.01.03; part:day,day+0.5; id:1 2);
    r:.qt.odbc.with[raw;{h:.odbc.open "x"; .odbc.eval[h;"q"]}];
    .qunit.assertEquals[r;([] a:1 2; whole:2020.01.02 2020.01.03; part:("z"$2020.01.02;"z"$2020.01.02)+0 0.5; id:1 2);
        "the all-midnight column is a date; a column with any time of day, and the rest, are untouched"]};

.qt.odbc.test_an_empty_or_null_datetime_column_counts_as_whole_days:{[t]
    e:.qt.odbc.with[([] e:0#"z"$2020.01.01);{.odbc.eval[.odbc.open "x";"q"]}];
    n:.qt.odbc.with[([] n:"z"$(2020.01.02;0N));{.odbc.eval[.odbc.open "x";"q"]}];
    .qunit.assertEquals[(type e`e;type n`n);(14h;14h);"as odbc.k does: an empty column and a null one convert"]};

.qt.odbc.test_a_result_that_is_not_a_table_is_returned_as_is:{[t]
    r:.qt.odbc.with[3j;{.odbc.eval[.odbc.open "x";"insert"]}];
    .qunit.assertEquals[r;3j;"a row count passes through"]};

.qt.odbc.test_tables_asks_the_native_library:{[t]
    r:.qt.odbc.with[::;{.odbc.tables .odbc.open "x"}];
    .qunit.assertEquals[r;([] name:`a`b);"tables returns the native answer"]};
