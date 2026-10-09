/ singlestore_odbc.q - a SingleStore source adapter over KX's q client for
/ ODBC (.qetl.io.odbc).
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

\d .qetl.io.odbc

/ ------------------------------------------------------- AVAILABILITY

/ Private: has the ODBC library been loaded into this process?
/ @private
loaded:{[] @[{`open in key x};`.odbc;{0b}]}

/ Load KX's ODBC client, once, and report whether it is usable.
/ .
/ Never throws. A missing driver is the EXPECTED state on any machine that
/ has not installed unixODBC and a SingleStore driver, which includes every
/ CI runner this repository uses - so a caller decides what to do about it
/ rather than being handed an error at load time.
/ @return 1b when .odbc is available, 0b otherwise
/ @eg .qetl.io.odbc.available[]
available:{[]
    if[loaded[]; :1b];
    @[{system"l odbc.k"; loaded[]};::;{[e] 0b}]}

/ Refuse, naming what is missing.
/ .
/ Called by every function below that needs the driver, so the message is
/ written once. Kept SHORT deliberately: q truncates a thrown string
/ silently, and the first draft of this one ran to 248 characters of
/ install advice - the tail, where the advice actually was, would have been
/ the part lost. Short ALSO because .qetl.job.bounded.connect wraps this
/ message inside its own, and the pair has to fit together - see
/ connect_error, where the wrap once cost 32 bytes of explanation.
/ .
/ The limit is 254 bytes, not the 255 stated here before: measured, a 254
/ byte string survives a throw intact and a 255 byte one comes back 254.
/ The long form lives here instead:
/ .
/   On a server, `uqs odbc install` puts an approved package - KX's client,
/   unixODBC and the driver - in a private ODBC home, and sourcing that
/   home's current/env.sh loads it: an overlay QHOME, ODBCSYSINI and
/   LD_LIBRARY_PATH, with nothing installed system-wide (#840). See
/   uqs.stack.odbc_home and code.kx.com/q/interfaces/q-client-for-odbc.
/ .
/   On macOS none of that is enough on its own: KX ships odbc.so for x86_64
/   only, so q has to run under Rosetta with an overlay QHOME.
/   scripts/dev/odbc_rosetta.sh setup builds it, and `uqs backfill` - which
/   launches plain arm64 q from ~/.kx - cannot reach a driver at all until
/   it does. That is why the message names the script rather than this file.
/ .
/   Every ODBC source in this tree is a DuckDB file, not SingleStore. The
/   module keeps its name for now, but the advice must not send a DuckDB
/   operator looking for a SingleStore installer.
/ .
/   You do NOT need any of that to exercise the backfill path - every source
/   declares a fixture, which is the question bank's whole point.
/ @throws error when the driver is unavailable
require_available:{[]
    if[not available[];
        '"qetl.io.odbc: driver not loaded - source an ODBC home's env.sh; macOS: scripts/dev/odbc_rosetta.sh setup"];
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
/ @private
escape_text:{[s] ssr[ssr[s;"\\";"\\\\"];"'";"''"]}

/ x divided by n, rounded up, for a positive n.
/ .
/ Never by dividing a NEGATIVE number: `neg (neg x) div n` is the usual
/ trick, and it relies on div flooring toward minus infinity, which KDB-X
/ does and PeachQ's Linux build does not - it truncated, so a bound meant to
/ round up rounded down there (#954). Each branch here divides a value that
/ is not negative.
/ @param x a long
/ @param n a positive long
/ @return the ceiling of x%n, as a long
/ @eg .qetl.io.odbc.ceil_div[1001;1000]  ->  2
ceil_div:{[x;n] $[x>=0; (x+n-1) div n; neg (neg x) div n]}

/ Private: a timestamp as SingleStore DATETIME(6) text - to the microsecond,
/ ROUNDED UP (#954).
/ .
/ It used to drop the whole fractional part (-10_ string v), so a bound at
/ 09:00:00.123456 read as 09:00:00 and a window either re-read the rows of
/ the one before it or lost its own. DATETIME(6) holds microseconds, and
/ every timestamp rendered here bounds a half-open window [from;to): for a
/ microsecond column, from <= r < to exactly when ceil_us(from) <= r <
/ ceil_us(to), so rounding both bounds UP keeps every row on the side it
/ belongs - truncating would not. A whole second renders as before, with no
/ fraction. q renders 2026.09.11D09:00:00.123456789; the date dots become
/ hyphens and the D a space.
/ @private
datetime6:{[v]
    if[null v; '"literal: a null timestamp has no SQL literal - a window bound must be an instant"];
    up:1970.01.01D00:00+`timespan$1000*ceil_div["j"$v-1970.01.01D00:00;1000];
    s:string up;
    base:ssr[ssr[19#s;".";"-"];"D";" "];
    $[0=(`long$up-1970.01.01D00:00) mod 1000000000; base; base,".",6#20_s]}

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
/ @eg .qetl.io.odbc.literal[`EURUSD]  ->  "'EURUSD'"
literal:{[v]
    t:abs type v;
    $[t=11h; "'",escape_text[string v],"'";
      t=10h; "'",escape_text[v],"'";
      t=12h; "'",datetime6[v],"'";
      t=14h; "'",ssr[string v;".";"-"],"'";
      t in 5 6 7h; string v;
      t=9h; string v;
      t=1h; $[v;"1";"0"];
      '"literal: no SQL rendering for type ",string[t],
       " - refusing rather than guessing, because a wrong rendering is usually still valid SQL"]}

/ A timestamp as DuckDB SQL for the same instant, to the nanosecond:
/ make_timestamp_ns over epoch nanoseconds, the long going through `literal`.
/ .
/ Not `literal` on the timestamp itself: that is SingleStore's DATETIME(6)
/ form, to the microsecond, and a DuckDB TIMESTAMP_NS bound that loses its
/ nanoseconds fetches rows either side of the window. Every DuckDB source
/ cuts its windows with this - databento_mbp10 and duckdb_deals each had
/ their own copy, byte for byte.
/ @param ts a timestamp
/ @return SQL text for the same instant
/ @eg .qetl.io.odbc.duckdb_timestamp[2026.09.11D09:00:00.000000001]  ->  "make_timestamp_ns(1789117200000000001)"
duckdb_timestamp:{[ts] "make_timestamp_ns(",literal["j"$ts-1970.01.01D00:00],")"}

/ ------------------------------------------------------- CONNECTING

/ Build a SingleStore connection string from its parts.
/ .
/ The credential is NOT a parameter: it is read from the environment by
/ .qetl.source.require_credentials, because nothing secret lives in this
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
      {[e] '"qetl.io.odbc.open: could not connect - ",e}]}

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
    if[(0h=type r) and 2=count r; if[`error~first r; '"qetl.io.odbc: ",last r]];
    r}

/ ---------------------------------------------------------- QUERYING

/ Run a SQL statement and return its rows.
/ .
/ `run_sql`, not `eval`: EVAL IS A Q BUILTIN, so defining it in a namespace
/ throws 'assign at LOAD time and aborts the rest of the file - leaving
/ .qetl.io.odbc half-populated while the enclosing script carries on. Ninth
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
/ .
/ The statement is logged at TRACE (.qetl.log.trace) before it is sent - so a
/ query that hangs is in the log - and again with the rows and milliseconds
/ when it returns. Every ODBC source sends through here, so this is the one
/ place a backfill's SQL is visible.
run_sql:{[h;sql]
    require_available[];
    if[not .qetl.log.enabled`TRACE;
        :.[{[hd;st] .odbc.eval[hd;st]};(h;sql);
          {[sql;e] '"qetl.io.odbc.run_sql: ",e," - statement: ",sql}[sql]]];
    t0:.z.p;
    / Numbered so sent, returned and failed share `request` - see
    / .qetl.source.ipc, which this mirrors for SQL.
    req:`transport`request!(`odbc;.qetl.log.next_request[]);
    .[{.qetl.log.trc[x;y;z]};(`odbc;"sql sent";(enlist[`statement]!enlist sql),req);::];
    r:.[{[hd;st] .odbc.eval[hd;st]};(h;sql);
      {[sql;req;t0;e]
        .[{.qetl.log.trc[x;y;z]};(`odbc;"sql failed";
            req,`error`ms!(e;`long$(.z.p-t0)%1000000));::];
        '"qetl.io.odbc.run_sql: ",e," - statement: ",sql}[sql;req;t0]];
    .[{.qetl.log.trc[x;y;z]};(`odbc;"sql returned";
        req,`rows`ms!(count r;`long$(.z.p-t0)%1000000));::];
    r}

/ The tables visible on a connection.
/ .
/ `table_names`, not `tables`: also a q builtin, and this one had already
/ cost this repository a debugging session once before.
/ .
/ For .qetl.source.validate_live, which verifies a DECLARED shape rather than
/ discovering one.
/ @param h an ODBC handle
/ @return a symbol vector of table names
table_names:{[h] require_available[]; .odbc.tables h}

/ A windowed SELECT over one table, with the bounds escaped.
/ .
/ This is the shape a .qetl.source source's `query` callback needs: half-open
/ [range_from;range_to), so >= on the lower bound and < on the
/ upper. Getting that one operator wrong double-publishes every boundary row,
/ which then appears as a duplicate nobody can explain.
/ .
/ The table and column names are q SYMBOLS from the source declaration, not
/ caller input - they are validated at registration - while the two bounds
/ are values and go through `literal`. That split is the whole of the question bank
/ here.
/ @param h an ODBC handle
/ @param table_name the table to read, as a symbol
/ @param time_column the timestamp column to window on, as a symbol
/ @param columns the columns to select, as a symbol vector
/ @param range_from inclusive lower bound
/ @param range_to exclusive upper bound
/ @return the rows in the window
/ @eg .qetl.io.odbc.window_query[h;`deals;`deal_time;`deal_id`rate;from_ts;to_ts]
window_query:{[h;table_name;time_column;columns;range_from;range_to]
    sql:"SELECT ",(", " sv string columns),
        " FROM ",string[table_name],
        " WHERE ",string[time_column],">=",literal[range_from],
        " AND ",string[time_column],"<",literal[range_to];
    run_sql[h;sql]}

\d .
