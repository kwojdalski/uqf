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
    .qunit.assertTrue[-1h=type .qodbc.available[];
        "availability is a boolean a caller can branch on, not an error"]};

test_every_entry_point_refuses_without_a_driver:{[t]
    / Each of these needs a connection, so each must refuse rather than
    / throwing something from inside .odbc that names no cause.
    if[.qodbc.available[]; :.qunit.assertTrue[1b;"a driver is present; refusal path not exercised"]];
    .qunit.assertError[{.qodbc.open x};"DRIVER=nope";
        "opening without a driver refuses and says so"]};

test_the_refusal_fits_in_a_thrown_string:{[t]
    / q truncates a thrown string at 255 bytes SILENTLY. The first draft of
    / this message ran to 248 characters of install advice and would have
    / lost its own tail.
    if[.qodbc.available[]; :.qunit.assertTrue[1b;"driver present"]];
    msg:@[{.qodbc.require_available[]; ""};::;{x}];
    .qunit.assertTrue[255>count msg;
        "the refusal survives q's silent 255-byte truncation"]};

/ --- SQL rendering: the part a bug turns into an injection ---------------

test_a_symbol_is_quoted:{[t]
    .qunit.assertEquals[.qodbc.literal[`EURUSD];"'EURUSD'";
        "a symbol becomes a quoted SQL string"]};

test_an_embedded_quote_is_doubled:{[t]
    / The classic injection. Without this the literal ends early and the rest
    / of the value is parsed as SQL.
    .qunit.assertEquals[.qodbc.literal["O'Brien"];"'O''Brien'";
        "a quote inside a value is escaped, not left to end the literal"]};

test_a_backslash_is_escaped_too:{[t]
    / SingleStore is MySQL-compatible, so backslash starts an escape
    / sequence. Escaping only the quote is the classic half-fix: it leaves
    / \' reading as an escaped quote, so the literal never ends.
    .qunit.assertEquals[.qodbc.literal["back\\slash"];"'back\\\\slash'";
        "a backslash is escaped, or it would escape the quote that follows it"]};

test_the_half_fix_is_actually_prevented:{[t]
    / The value that defeats quote-only escaping. Rendered correctly, the
    / backslash is doubled so it cannot consume the closing quote.
    r:.qodbc.literal["ends\\"];
    .qunit.assertEquals[r;"'ends\\\\'";
        "a value ending in a backslash cannot swallow its own closing quote"]};

test_a_timestamp_becomes_a_datetime_literal:{[t]
    / q renders 2026.09.11D09:00:00.000000000 - the dots and the D are q's
    / syntax, not SQL's, and passing them unchanged is a syntax error at best
    / and a wrong window at worst.
    .qunit.assertEquals[.qodbc.literal[2026.09.11D09:00:00.000000000];
        "'2026-09-11 09:00:00'";
        "a q timestamp is rendered as a SQL DATETIME literal"]};

test_a_date_becomes_a_date_literal:{[t]
    .qunit.assertEquals[.qodbc.literal[2026.09.11];"'2026-09-11'";
        "a q date renders with SQL's hyphens"]};

test_numbers_are_unquoted:{[t]
    .qunit.assertEquals[(.qodbc.literal[42j];.qodbc.literal[1.5]);("42";"1.5");
        "numbers are not quoted, or SingleStore compares them as strings"]};

test_a_boolean_becomes_one_or_zero:{[t]
    .qunit.assertEquals[(.qodbc.literal[1b];.qodbc.literal[0b]);("1";"0");
        "a q boolean becomes SQL's 1/0"]};

test_an_unknown_type_is_refused:{[t]
    / Refused rather than rendered with `string`, because `string` on an
    / unexpected type usually produces something that is still valid SQL and
    / occasionally means something else - which is a wrong answer, not an
    / error.
    .qunit.assertError[{.qodbc.literal x};2026.09m;
        "a type with no defined SQL rendering is refused rather than guessed"]};

/ --- the windowed query --------------------------------------------------

test_the_window_is_half_open:{[t]
    / ETL-08: >= on the lower bound, < on the upper. One wrong operator
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
    conn:.qodbc.build_connection_string["db.example";3306;"deals";"svc";"s3cret"];
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
    conn:.qodbc.build_connection_string["h";1;"d";"u";"p"];
    .qunit.assertTrue[conn like "DRIVER=*";"DRIVER leads the string"]};

test_the_password_is_a_parameter_not_a_literal:{[t]
    / the question bank: the credential comes from the environment, so this function
    / must never carry a default. Two different passwords must produce two
    / different strings - a hardcoded one would make them identical.
    a:.qodbc.build_connection_string["h";1;"d";"u";"one"];
    b:.qodbc.build_connection_string["h";1;"d";"u";"two"];
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
    r:.odbctest.with_fake_driver[{.qodbc.run_sql[7;"select 1"]}];
    .qunit.assertTrue[98h=type r;"run_sql hands back the table .odbc.eval returns, not a projection of it"]};

test_run_sql_passes_handle_and_statement_separately:{[t]
    r:.odbctest.with_fake_driver[{.qodbc.run_sql[7;"select 1"]}];
    .qunit.assertEquals[(first r`h;first r`sql);(7;"select 1");"the handle and the statement reach the driver as two arguments"]};

test_run_sql_names_the_statement_on_failure:{[t]
    err:.odbctest.with_fake_driver[{
        `.odbc.eval set {[h;sql] '"syntax"};
        @[.qodbc.run_sql[7;];"select nope";{x}]}];
    / two likes: a pattern with more than one inner `*` throws 'nyi here
    .qunit.assertTrue[(err like "*syntax*") and err like "*select nope";"a failing statement is named in the error, with the driver's reason"]};

/ Build the statement without a connection, by calling the renderer the query
/ uses. Keeps every assertion above driver-free.
built_sql:{[]
    "SELECT ",(", " sv string `deal_id`rate),
    " FROM deals",
    " WHERE deal_time>=",.qodbc.literal[2026.09.11D00:00:00.000000000],
    " AND deal_time<",.qodbc.literal[2026.09.12D00:00:00.000000000]};

\d .
