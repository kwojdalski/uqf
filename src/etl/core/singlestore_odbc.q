/ singlestore_odbc.q - a SingleStore source adapter over KX's q client for
/ ODBC (.qodbc).
/ .
/ The "skippable ODBC adapter" half of the question bank. The other half -
/ a generic file fixture - already ships, and that ordering is the whole
/ design: the driver is NOT a hard dependency, because a public single-host
/ demo cannot require a licensed one or the backfill path becomes
/ undemonstrable. Everything here degrades to a stated refusal when the
/ driver is absent, and no test in the default lane needs it.
/ .
/ Built against https://code.kx.com/q/interfaces/q-client-for-odbc/. Four
/ facts from that page shape this file, and each would be a bug if assumed
/ wrongly:
/ .
/   1. The library loads with `\l odbc.k`, NOT with 2:. It populates .odbc.
/   2. `.odbc.eval[h;sql]` takes a SQL STRING. There is no bind-parameter
/      API and no `.odbc.exec` at all, whatever other drivers offer.
/   3. `.odbc.open` accepts a connection string, a DSN symbol, or a
/      (string;timeout) pair; it returns a handle.
/   4. Linux needs unixODBC with LD_LIBRARY_PATH set, so "the driver is
/      missing" is a normal condition on a developer machine, not an error.
/ .
/ FACT 2 IS THE HARD ONE, and it is why this file exists rather than a
/ three-line wrapper. Per the question bank: parameterise where the driver
/ allows, ONE escape function otherwise. ODBC via .odbc.eval is the
/ "otherwise", so there is exactly one escaping path here and every value
/ goes through it. A second place that builds SQL is the bug this design
/ exists to prevent.

\d .qodbc

/ ------------------------------------------------------- AVAILABILITY

/ Private: has the ODBC library been loaded into this process?
loaded:{[] @[{`open in key x};`.odbc;{0b}]}

/ Load KX's ODBC client, once, and report whether it is usable.
/ .
/ Never throws. A missing driver is the EXPECTED state on any machine that
/ has not installed unixODBC and a SingleStore driver, which includes every
/ CI runner this repository uses - so a caller decides what to do about it
/ rather than being handed an error at load time.
/ @return 1b when .odbc is available, 0b otherwise
/ @eg .qodbc.available[]
available:{[]
    if[loaded[]; :1b];
    @[{system"l odbc.k"; loaded[]};::;{[e] 0b}]}

/ Refuse, naming what is missing.
/ .
/ Called by every function below that needs the driver, so the message is
/ written once. Kept SHORT deliberately: q truncates a thrown string at 255
/ bytes silently, and the first draft of this one ran to 248 characters of
/ install advice - the tail, where the advice actually was, would have been
/ the part lost. The long form lives here instead:
/ .
/   Install unixODBC and a SingleStore ODBC driver, put odbc.k where q can
/   load it, and on Linux set LD_LIBRARY_PATH so the driver manager is
/   found. See code.kx.com/q/interfaces/q-client-for-odbc.
/ .
/   You do NOT need any of that to exercise the backfill path - every source
/   declares a fixture, which is the question bank's whole point.
/ @throws error when the driver is unavailable
require_available:{[]
    if[not available[];
        '"qodbc: ODBC driver not loaded - see singlestore_odbc.q's require_available for the install, or use the source's fixture"];
    1b}

/ ---------------------------------------------------------- ESCAPING

/ Private: the one escape for a SQL string literal.
/ .
/ SingleStore is MySQL-compatible, so BOTH the quote and the backslash are
/ special: `'` ends the literal and `\` starts an escape sequence. Escaping
/ only the quote is the classic half-fix - it leaves `\'` reading as an
/ escaped quote, so the literal never ends and the next value is parsed as
/ SQL.
/ .
/ Backslash FIRST, or escaping the quotes would then double the backslashes
/ this very function adds.
escape_text:{[s] ssr[ssr[s;"\\";"\\\\"];"'";"''"]}

/ A q value as a SQL literal.
/ .
/ Every value reaching a query goes through here - that is the question bank's "one
/ escape function", and it is only true while there is no second path. A
/ type this does not know is REFUSED rather than rendered with `string`,
/ because `string` on an unexpected type produces something that is usually
/ valid SQL and occasionally means something else.
/ @param v a symbol, string, timestamp, date, long, int, float or boolean
/ @return the SQL literal text
/ @throws error naming the type when it is not one this understands
/ @eg .qodbc.literal[`EURUSD]  ->  "'EURUSD'"
literal:{[v]
    t:abs type v;
    $[t=11h; "'",escape_text[string v],"'";
      t=10h; "'",escape_text[v],"'";
      / SingleStore's DATETIME(6) literal. q renders a timestamp as
      / 2026.09.11D09:00:00.000000000 - the dots and the D are q's, not
      / SQL's, so the date separators become hyphens and the D a space.
      / -10_ drops q's ".000000000" - ten characters - leaving
      / 2026.09.11D09:00:00. Then the date dots become hyphens and the D a
      / space. The first draft wrote `9#"0"_ -1_` which is drop-BY-STRING and
      / a plain type error; it looked like character surgery and was not.
      t=12h; "'",(ssr[ssr[-10_ string v;".";"-"];"D";" "]),"'";
      t=14h; "'",ssr[string v;".";"-"],"'";
      t in 5 6 7h; string v;
      t=9h; string v;
      t=1h; $[v;"1";"0"];
      '"literal: no SQL rendering for type ",string[t],
       " - refusing rather than guessing, because a wrong rendering is usually still valid SQL"]}

/ ------------------------------------------------------- CONNECTING

/ Build a SingleStore connection string from its parts.
/ .
/ The credential is NOT a parameter: it is read from the environment by
/ .qsrc.require_credentials, because nothing secret lives in this
/ tree and a parameter is something a caller can log.
/ @param host the SingleStore host
/ @param port the port, as a long
/ @param database the database name
/ @param user the username
/ @param password the password, from the environment
/ @return the ODBC connection string
build_connection_string:{[host;port;database;user;password]
    ";" sv ("DRIVER=SingleStore ODBC Driver";
            "SERVER=",host;
            "PORT=",string port;
            "DATABASE=",database;
            "UID=",user;
            "PWD=",password)}

/ Open a connection, or refuse.
/ @param conn the connection string
/ @return an ODBC handle
/ @throws error when the driver is absent, or the connection fails
open:{[conn]
    require_available[];
    @[{.odbc.open x};conn;
      {[e] '"qodbc.open: could not connect - ",e}]}

/ Close a handle, tolerating one that is already closed.
/ .
/ Safe to call twice and safe in a failure path, which is what lets
/ with_connection close unconditionally.
close:{[h] @[{.odbc.close x};h;{[e] (::)}]; (::)}

/ Run f against a fresh connection and ALWAYS close it.
/ .
/ The reason this exists rather than open/use/close at each call site: a
/ backfill that throws mid-window would otherwise leak the handle, and a
/ leaked ODBC handle holds a server-side session until something times it
/ out. The error is rethrown after closing, so the caller still sees it.
/ @param conn the connection string
/ @param f a function taking the handle
/ @return whatever f returns
/ @throws whatever f throws, after the handle is closed
with_connection:{[conn;f]
    h:open conn;
    r:@[f;h;{[e] (`error;e)}];
    close h;
    if[(0h=type r) and 2=count r; if[`error~first r; '"qodbc: ",last r]];
    r}

/ ---------------------------------------------------------- QUERYING

/ Run a SQL statement and return its rows.
/ .
/ `run_sql`, not `eval`: EVAL IS A Q BUILTIN, so defining it in a namespace
/ throws 'assign at LOAD time and aborts the rest of the file - leaving
/ .qodbc half-populated while the enclosing script carries on. Ninth
/ reserved-name collision in this repository, and the first the trap checker
/ did not already know about; it does now.
/ @param h an ODBC handle
/ @param sql the statement
/ @return a table
/ @throws error naming the statement when it fails
/ .
/ `.` with the argument list, not `@[{.odbc.eval x};(h;sql);...]`: @ is UNARY
/ apply, so that form handed the pair to .odbc.eval as one argument and, eval
/ being 2-ary, returned a PROJECTION rather than a table. Nothing threw, the
/ handler never fired, and the first live caller found out when `meta` on
/ the "table" failed. Found the first time this file ran against a driver.
run_sql:{[h;sql]
    require_available[];
    .[{[hd;st] .odbc.eval[hd;st]};(h;sql);
      {[sql;e] '"qodbc.run_sql: ",e," - statement: ",sql}[sql]]}

/ The tables visible on a connection.
/ .
/ `table_names`, not `tables`: also a q builtin, and this one had already
/ cost this repository a debugging session once before.
/ .
/ For .qsrc.validate_live, which verifies a DECLARED shape rather than
/ discovering one.
/ @param h an ODBC handle
/ @return a symbol vector of table names
table_names:{[h] require_available[]; .odbc.tables h}

/ A windowed SELECT over one table, with the bounds escaped.
/ .
/ This is the shape a .qsrc source's `query` callback needs: half-open
/ [range_from;range_to) per ETL-08, so >= on the lower bound and < on the
/ upper. Getting that one operator wrong double-publishes every boundary row,
/ which then appears as a duplicate nobody can explain.
/ .
/ The table and column names are q SYMBOLS from the source declaration, not
/ caller input - they are validated at registration - while the two bounds
/ are values and go through `literal`. That split is the whole of the question bank
/ here.
/ @param h an ODBC handle
/ @param tbl the table to read, as a symbol
/ @param time_field the timestamp column to window on, as a symbol
/ @param fields the columns to select, as a symbol vector
/ @param range_from inclusive lower bound
/ @param range_to exclusive upper bound
/ @return the rows in the window
/ @eg .qodbc.window_query[h;`deals;`deal_time;`deal_id`rate;from_ts;to_ts]
window_query:{[h;tbl;time_field;fields;range_from;range_to]
    sql:"SELECT ",(", " sv string fields),
        " FROM ",string[tbl],
        " WHERE ",string[time_field],">=",literal[range_from],
        " AND ",string[time_field],"<",literal[range_to];
    run_sql[h;sql]}

\d .
