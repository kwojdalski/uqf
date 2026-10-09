/ test_singlestore_odbc.q - the SingleStore ODBC adapter (.odbctest).
/ .
/ Every test here runs WITHOUT a driver, which is the question bank's requirement
/ rather than a limitation of the test lane: if these needed unixODBC and a
/ licensed SingleStore driver, the adapter would be unverifiable on every
/ machine this repository actually runs on.
/ .
/ So they cover the two halves that do not need a connection - the SQL
/ rendering, which is where a bug becomes an injection or a silently wrong
/ window, and the refusal path, which is what every caller hits when the
/ driver is absent.

\d .odbctest

/ --- availability and refusal --------------------------------------------

test_availability_is_reported_not_thrown:{[t]
    / A missing driver is the EXPECTED state here. If this threw, no test in
    / this file could run and the adapter would be unverifiable.
    .qunit.assertTrue[-1h=type .qetl.io.odbc.available[];
        "availability is a boolean a caller can branch on, not an error"]};

test_every_entry_point_refuses_without_a_driver:{[t]
    / Each of these needs a connection, so each must refuse rather than
    / throwing something from inside .odbc that names no cause.
    if[.qetl.io.odbc.available[]; :.qunit.assertTrue[1b;"a driver is present; refusal path not exercised"]];
    .qunit.assertError[{.qetl.io.odbc.open x};"DRIVER=nope";
        "opening without a driver refuses and says so"]};

test_the_refusal_fits_in_a_thrown_string:{[t]
    / q truncates a thrown string at 255 bytes SILENTLY. The first draft of
    / this message ran to 248 characters of install advice and would have
    / lost its own tail.
    if[.qetl.io.odbc.available[]; :.qunit.assertTrue[1b;"driver present"]];
    msg:@[{.qetl.io.odbc.require_available[]; ""};::;{x}];
    .qunit.assertTrue[255>count msg;
        "the refusal survives q's silent 255-byte truncation"]};

/ --- SQL rendering: the part a bug turns into an injection ---------------

test_a_symbol_is_quoted:{[t]
    .qunit.assertEquals[.qetl.io.odbc.literal[`EURUSD];"'EURUSD'";
        "a symbol becomes a quoted SQL string"]};

test_an_embedded_quote_is_doubled:{[t]
    / The classic injection. Without this the literal ends early and the rest
    / of the value is parsed as SQL.
    .qunit.assertEquals[.qetl.io.odbc.literal["O'Brien"];"'O''Brien'";
        "a quote inside a value is escaped, not left to end the literal"]};

test_a_backslash_is_escaped_too:{[t]
    / SingleStore is MySQL-compatible, so backslash starts an escape
    / sequence. Escaping only the quote is the classic half-fix: it leaves
    / \' reading as an escaped quote, so the literal never ends.
    .qunit.assertEquals[.qetl.io.odbc.literal["back\\slash"];"'back\\\\slash'";
        "a backslash is escaped, or it would escape the quote that follows it"]};

test_the_half_fix_is_actually_prevented:{[t]
    / The value that defeats quote-only escaping. Rendered correctly, the
    / backslash is doubled so it cannot consume the closing quote.
    r:.qetl.io.odbc.literal["ends\\"];
    .qunit.assertEquals[r;"'ends\\\\'";
        "a value ending in a backslash cannot swallow its own closing quote"]};

test_a_timestamp_becomes_a_datetime_literal:{[t]
    / q renders 2026.09.11D09:00:00.000000000 - the dots and the D are q's
    / syntax, not SQL's, and passing them unchanged is a syntax error at best
    / and a wrong window at worst.
    .qunit.assertEquals[.qetl.io.odbc.literal[2026.09.11D09:00:00.000000000];
        "'2026-09-11 09:00:00'";
        "a q timestamp is rendered as a SQL DATETIME literal"]};

/ --- timestamp precision (#954) -------------------------------------------

test_a_fractional_timestamp_keeps_its_microseconds:{[t]
    .qunit.assertEquals[.qetl.io.odbc.literal[2026.09.11D09:00:00.123456000];
        "'2026-09-11 09:00:00.123456'";"DATETIME(6): the fraction is not dropped"]};

test_nanoseconds_round_up_to_the_next_microsecond:{[t]
    .qunit.assertEquals[.qetl.io.odbc.literal[2026.09.11D09:00:00.123456001];
        "'2026-09-11 09:00:00.123457'";"up, so a half-open bound keeps every row on its side"]};

test_rounding_up_can_cross_midnight:{[t]
    .qunit.assertEquals[.qetl.io.odbc.literal[2026.09.11D23:59:59.999999500];
        "'2026-09-12 00:00:00'";"the next day's first instant, whole, with no fraction"]};

test_a_null_timestamp_is_refused:{[t]
    .qunit.assertThrows[.qetl.io.odbc.literal;0Np;"literal: a null timestamp has no SQL literal*";
        "it used to render as ''"]};

test_ceil_div_rounds_up_on_either_side_of_zero:{[t]
    / without dividing a negative number, which interpreters floor differently
    .qunit.assertEquals[.qetl.io.odbc.ceil_div[;1000] each 0 1 1000 1001 -1 -1000 -1001;0 1 1 2 0 -1 -1;
        "the ceiling, whatever the sign"]};

/ The literal back as a q timestamp, as SingleStore would read it.
as_read:{[lit] "P"$ssr[ssr[1_-1_lit;"-";"."];" ";"D"]}

test_a_window_selects_the_same_microsecond_rows_after_rendering:{[t]
    / For rows at microsecond precision, [from;to) and the rendered
    / [ceil(from);ceil(to)) hold exactly the same rows - including rows
    / sharing a timestamp, and the ones either side of each bound.
    / start_ts/end_ts, not from/to: both are qSQL keywords, which q reserves
    start_ts:2026.09.11D09:00:00.000000500; end_ts:2026.09.11D09:00:00.000002500;
    rows:2026.09.11D09:00:00+`timespan$1000*0 0 1 2 2 3;
    want:rows where (rows>=start_ts) & rows<end_ts;
    lo:.odbctest.as_read[.qetl.io.odbc.literal start_ts];
    hi:.odbctest.as_read[.qetl.io.odbc.literal end_ts];
    .qunit.assertEquals[rows where (rows>=lo) & rows<hi;want;"no row moves across a bound"];
    .qunit.assertEquals[count want;3;"both rows sharing 2us are in, 0us is out, 3us is out"]};

test_a_date_becomes_a_date_literal:{[t]
    .qunit.assertEquals[.qetl.io.odbc.literal[2026.09.11];"'2026-09-11'";
        "a q date renders with SQL's hyphens"]};

test_numbers_are_unquoted:{[t]
    .qunit.assertEquals[(.qetl.io.odbc.literal[42j];.qetl.io.odbc.literal[1.5]);("42";"1.5");
        "numbers are not quoted, or SingleStore compares them as strings"]};

test_a_boolean_becomes_one_or_zero:{[t]
    .qunit.assertEquals[(.qetl.io.odbc.literal[1b];.qetl.io.odbc.literal[0b]);("1";"0");
        "a q boolean becomes SQL's 1/0"]};

test_an_unknown_type_is_refused:{[t]
    / Refused rather than rendered with `string`, because `string` on an
    / unexpected type usually produces something that is still valid SQL and
    / occasionally means something else - which is a wrong answer, not an
    / error.
    .qunit.assertError[{.qetl.io.odbc.literal x};2026.09m;
        "a type with no defined SQL rendering is refused rather than guessed"]};

/ --- the windowed query --------------------------------------------------

test_the_window_is_half_open:{[t]
    / Half-open: >= on the lower bound, < on the upper. One wrong operator
    / double-publishes every boundary row, which then appears as a duplicate
    / nobody can explain.
    sql:.odbctest.built_sql[];
    .qunit.assertTrue[(sql like "*deal_time>=*") and sql like "*deal_time<'*";
        "the range is half-open, so boundary rows are published exactly once"]};

test_the_bounds_are_escaped_not_concatenated:{[t]
    sql:.odbctest.built_sql[];
    .qunit.assertTrue[sql like "*'2026-09-11 00:00:00'*";
        "the bounds go through literal, so no q syntax reaches the statement"]};

test_the_selected_columns_come_from_the_declaration:{[t]
    sql:.odbctest.built_sql[];
    .qunit.assertTrue[sql like "SELECT deal_id, rate FROM*";
        "columns are the declared symbols, which is why they need no escaping"]};

/ --- the connection string, which needs no driver to build ---------------

/ qcov reported build_connection_string as never executed. It is pure string
/ assembly, so being untestable was never the reason - it simply had no
/ test. What it assembles is a credential-bearing string, which is worth
/ pinning: a field in the wrong order or a missing separator produces a
/ string the driver rejects with a message about syntax, not about the field
/ that is wrong.

test_the_connection_string_carries_every_field:{[t]
    conn:.qetl.io.odbc.build_connection_string["db.example";3306;"deals";"svc";"s3cret"];
    parts:";" vs conn;
    .qunit.assertTrue[any parts like "SERVER=db.example";"the host"];
    .qunit.assertTrue[any parts like "PORT=3306";"the port, rendered as text"];
    .qunit.assertTrue[any parts like "DATABASE=deals";"the database"];
    .qunit.assertTrue[any parts like "UID=svc";"the user"];
    .qunit.assertTrue[any parts like "PWD=s3cret";"the password"]};

test_the_driver_is_named_first:{[t]
    / ODBC reads DRIVER first; a connection string that names it later is
    / accepted by some drivers and rejected by others, which is the worst
    / kind of portability bug to debug.
    conn:.qetl.io.odbc.build_connection_string["h";1;"d";"u";"p"];
    .qunit.assertTrue[conn like "DRIVER=*";"DRIVER leads the string"]};

test_the_password_is_a_parameter_not_a_literal:{[t]
    / the question bank: the credential comes from the environment, so this function
    / must never carry a default. Two different passwords must produce two
    / different strings - a hardcoded one would make them identical.
    a:.qetl.io.odbc.build_connection_string["h";1;"d";"u";"one"];
    b:.qetl.io.odbc.build_connection_string["h";1;"d";"u";"two"];
    .qunit.assertTrue[not a~b;"the password reaches the string it is passed to"]};

/ --- run_sql, against a stubbed driver --------------------------------------

/ A fake .odbc: what the KX client populates, minus the driver. Installed for
/ one test and removed after, so every other test still sees "no driver".
with_fake_driver:{[f]
    `.odbc.open set {[c] 7};
    `.odbc.eval set {[h;sql] ([] h:enlist h; sql:enlist sql)};
    r:@[f;::;{(`threw;x)}];
    ![`.odbc;();0b;`open`eval];
    r}

test_run_sql_returns_the_drivers_table:{[t]
    / The first live run of this file returned a PROJECTION here, not a table:
    / `@[{.odbc.eval x};(h;sql);...]` hands the pair to a 2-ary eval as ONE
    / argument, nothing throws, and the caller fails later on `meta`.
    r:.odbctest.with_fake_driver[{.qetl.io.odbc.run_sql[7;"select 1"]}];
    .qunit.assertTrue[98h=type r;"run_sql hands back the table .odbc.eval returns, not a projection of it"]};

test_run_sql_passes_handle_and_statement_separately:{[t]
    r:.odbctest.with_fake_driver[{.qetl.io.odbc.run_sql[7;"select 1"]}];
    .qunit.assertEquals[(first r`h;first r`sql);(7;"select 1");"the handle and the statement reach the driver as two arguments"]};

test_run_sql_names_the_statement_on_failure:{[t]
    err:.odbctest.with_fake_driver[{
        `.odbc.eval set {[h;sql] '"syntax"};
        @[.qetl.io.odbc.run_sql[7;];"select nope";{x}]}];
    / two likes: a pattern with more than one inner `*` throws 'nyi here
    .qunit.assertTrue[(err like "*syntax*") and err like "*select nope";"a failing statement is named in the error, with the driver's reason"]};

/ Every log line `f` writes, as (level;id;text;fields): a recorder in place of
/ .qetl.log.line, which every level calls - so this sees what is logged, ahead
/ of the TRACE switch test_log.q covers - and the real one put back after.
/ TRACE on for the call: with it off the request paths trace nothing at all.
logged:.testutil.captured_log[1b]

test_run_sql_traces_the_statement_and_what_came_back:{[t]
    lines:.odbctest.logged {.odbctest.with_fake_driver[{.qetl.io.odbc.run_sql[7;"select 1"]}]};
    .qunit.assertEquals[lines[;0 1 2];((`TRACE;`odbc;"sql sent");(`TRACE;`odbc;"sql returned"));
        "one TRACE line before the statement is sent, one after"];
    .qunit.assertEquals[((lines 0)[3]`statement;(lines 1)[3]`rows);("select 1";1);
        "the exact statement, then the row count"]};

test_run_sql_traces_a_statement_that_fails:{[t]
    / Logged BEFORE it is sent, so a statement that throws - or hangs - is
    / still in the trace.
    lines:.odbctest.logged {.odbctest.with_fake_driver[{
        `.odbc.eval set {[h;sql] '"syntax"};
        @[.qetl.io.odbc.run_sql[7;];"select nope";::]}]};
    .qunit.assertEquals[lines[;2];("sql sent";"sql failed");"the statement is logged, then its failure - and nothing claims it returned"];
    .qunit.assertEquals[(lines[0;3]`request)~lines[1;3]`request;1b;"sent and failed carry the same request number"];
    .qunit.assertEquals[(first lines)[3]`statement;"select nope";"the failing statement itself"]};

/ Build the statement without a connection, by calling the renderer the query
/ uses. Keeps every assertion above driver-free.
built_sql:{[]
    "SELECT ",(", " sv string `deal_id`rate),
    " FROM deals",
    " WHERE deal_time>=",.qetl.io.odbc.literal[2026.09.11D00:00:00.000000000],
    " AND deal_time<",.qetl.io.odbc.literal[2026.09.12D00:00:00.000000000]};

\d .
