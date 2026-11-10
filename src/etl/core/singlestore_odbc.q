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
/   1. KX's client is odbc.k over a native odbc.so. This tree does not load
/      odbc.k (k source, which PeachQ cannot run): src/etl/core/odbc.q is our own
/      wrapper over odbc.so with the same .odbc.open/eval/close/tables (#2508).
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

\d .

/ ------------------------------------------------------- AVAILABILITY

/ Private: is odbc.so bound in this process? Loading src/etl/core/odbc.q is not
/ enough - it binds nothing until first use - so this asks for the natives.
/ @private
.qetl.io.odbc.loaded:{[] @[{0<count .odbc.natives};::;{0b}]}

/ Load our odbc.q wrapper and bind KX's odbc.so, once, and report whether it is usable.
/ .
/ Never throws. A missing driver is the EXPECTED state on any machine that
/ has not installed unixODBC and a SingleStore driver, which includes every
/ CI runner this repository uses - so a caller decides what to do about it
/ rather than being handed an error at load time.
/ @return 1b when odbc.so is bound, 0b otherwise (no odbc.so in QHOME, or a q that cannot load one)
/ @eg .qetl.io.odbc.available[]
.qetl.io.odbc.available:{[]
    if[.qetl.io.odbc.loaded[]; :1b];
    @[{if[()~key `.odbc.bind; system"l src/etl/core/odbc.q"]; .odbc.bind[]; .qetl.io.odbc.loaded[]};::;{[e] 0b}]}

/ Can this interpreter load a shared library (`2:`)? The ODBC client is one, so a
/ static build - PeachQ's - can never reach a driver. Asked by trying a library
/ that does not exist: a static build answers "static-dlopen...", any other says
/ the file is missing. Never throws. The same expression
/ python/uqs/src/uqs/stack/capabilities.py's PROBE asks of a bare q (it cannot
/ load this tree), held to this one by test_peachq_pipelines.
/ @return 1b when `2:` can load a library, 0b on a static build
/ @eg .qetl.io.odbc.native_loadable[]
.qetl.io.odbc.native_loadable:{[]
    not (@[{`:./uqs_no_such_library 2:(`f;1)};::;{x}]) like "static-dlopen*"}

/ Refuse before anything is claimed, locked or written when this interpreter
/ cannot load a shared library - the preflight of the odbc transport (see
/ .qetl.source.preflight). The driver may be absent on a machine that CAN load
/ one, and a worker then runs on its fixture; a static build can do neither.
/ @return 1b
/ @throws error when `2:` is unavailable
.qetl.io.odbc.require_native:{[]
    if[not .qetl.io.odbc.native_loadable[];
        '"qetl.io.odbc: this q cannot load a shared library (2:), so no ODBC driver - run it on a q that can (a KDB-X or -glibc build)"];
    1b}

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
/   On a server, `uqs setup odbc install` puts an approved package - KX's client,
/   unixODBC and the driver - in a private ODBC home, and sourcing that
/   home's current/env.sh loads it: an overlay QHOME, ODBCSYSINI and
/   LD_LIBRARY_PATH, with nothing installed system-wide (#840). See
/   uqs.stack.odbc_home and code.kx.com/q/interfaces/q-client-for-odbc.
/ .
/   On macOS none of that is enough on its own: KX ships odbc.so for x86_64
/   only, so q has to run under Rosetta with an overlay QHOME.
/   scripts/dev/odbc_rosetta.sh setup builds it, and `uqs job backfill` - which
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
.qetl.io.odbc.require_available:{[]
    if[not .qetl.io.odbc.available[];
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
.qetl.io.odbc.escape_text:{[s] ssr[ssr[s;"\\";"\\\\"];"'";"''"]}

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
.qetl.io.odbc.datetime6:{[v]
    if[null v; '"literal: a null timestamp has no SQL literal - a window bound must be an instant"];
    / The sub-microsecond nanoseconds are the last three digits q prints,
    / and adding what is left to the next microsecond is an exact add. NOT
    / division on epoch nanoseconds: they exceed 2^53, and PeachQ's Linux
    / build divides them as doubles - a whole second came out .000001.
    below:"J"$-3#string v;
    up:v+`timespan$$[below>0; 1000-below; 0];
    s:string up;
    base:ssr[ssr[19#s;".";"-"];"D";" "];
    frac:6#20_s;
    $["000000"~frac; base; base,".",frac]}

/ Private: a float as SQL text for the same value, whatever the session's \P.
/ `string` formats with the display precision (7 digits by default), so
/ 1.23456789 came out "1.234568" - valid SQL for a different value (#2438). 17
/ significant digits round-trip every double. A null or infinite float has no
/ SQL number (`string 0n` is "", which leaves `x>= AND ...`), so it is refused.
/ @private
.qetl.io.odbc.float_text:{[f]
    if[any (null f),(abs f)=0w;
        '"literal: a null or infinite float has no SQL rendering - refusing rather than guessing"];
    p:system"P";
    system"P 17";
    r:string f;
    system"P ",string p;
    r}

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
.qetl.io.odbc.literal:{[v]
    t:abs type v;
    $[t=11h; "'",.qetl.io.odbc.escape_text[string v],"'";
      t=10h; "'",.qetl.io.odbc.escape_text[v],"'";
      t=12h; "'",.qetl.io.odbc.datetime6[v],"'";
      t=14h; "'",ssr[string v;".";"-"],"'";
      t in 5 6 7h; string v;
      t=9h; .qetl.io.odbc.float_text v;
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
.qetl.io.odbc.duckdb_timestamp:{[ts] "make_timestamp_ns(",.qetl.io.odbc.literal["j"$ts-1970.01.01D00:00],")"}

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
.qetl.io.odbc.build_connection_string:{[host;port;database;user;password]
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
.qetl.io.odbc.open:{[conn]
    .qetl.io.odbc.require_available[];
    @[{.odbc.open x};conn;
      {[e] '"qetl.io.odbc.open: could not connect - ",e}]}

/ Close a handle, tolerating one that is already closed.
/ .
/ Safe to call twice and safe in a failure path, which is what lets
/ with_connection close unconditionally.
.qetl.io.odbc.close:{[h] @[{.odbc.close x};h;{[e] (::)}]; (::)}

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
.qetl.io.odbc.with_connection:{[conn;f]
    h:.qetl.io.odbc.open conn;
    / The trap's outcome is tagged apart from f's value, so a callback that
    / returns (`error;...) is handed back rather than rethrown (#2309).
    r:@[{(1b;x y)}[f];h;{[e] (0b;e)}];
    .qetl.io.odbc.close h;
    if[not first r; '"qetl.io.odbc: ",last r];
    last r}

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
.qetl.io.odbc.run_sql:{[h;sql]
    .qetl.io.odbc.require_available[];
    if[not .qetl.log.enabled`TRACE;
        :.[{[hd;st] .odbc.eval[hd;st]};(h;sql);
          {[sql;e] '"qetl.io.odbc.run_sql: ",e," - statement: ",sql}[sql]]];
    t0:.z.p;
    / Numbered so sent, returned and failed share `request` - see
    / .qetl.source.ipc, which this mirrors for SQL.
    req:`transport`request!(`odbc;.qetl.log.next_request[]);
    .[{.qetl.log.ch.trc[x;y;z]};(`source;"sql sent";(enlist[`statement]!enlist sql),req);::];
    r:.[{[hd;st] .odbc.eval[hd;st]};(h;sql);
      {[sql;req;t0;e]
        .[{.qetl.log.ch.trc[x;y;z]};(`source;"sql failed";
            req,`error`ms!(e;`long$(.z.p-t0)%1000000));::];
        '"qetl.io.odbc.run_sql: ",e," - statement: ",sql}[sql;req;t0]];
    .[{.qetl.log.ch.trc[x;y;z]};(`source;"sql returned";
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
.qetl.io.odbc.table_names:{[h] .qetl.io.odbc.require_available[]; .odbc.tables h}

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
.qetl.io.odbc.window_query:{[h;table_name;time_column;columns;range_from;range_to]
    sql:"SELECT ",(", " sv string columns),
        " FROM ",string[table_name],
        " WHERE ",string[time_column],">=",.qetl.io.odbc.literal[range_from],
        " AND ",string[time_column],"<",.qetl.io.odbc.literal[range_to];
    .qetl.io.odbc.run_sql[h;sql]}

\d .
