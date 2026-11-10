/ odbc.q - our own q client over KX's native odbc.so (.odbc) (#2508).
/ .
/ Replaces `\l odbc.k`. KX's odbc.k is k source, which PeachQ cannot run; the
/ native odbc.so it wraps is a plain k.h extension, and PeachQ implements `2:`.
/ This file is written against odbc.so's entry points (open, close, eval,
/ tables) and keeps odbc.k's behaviour for the four functions this tree uses:
/ .
/   .odbc.open   a connection string, a DSN symbol, or a (string;timeout) pair
/   .odbc.eval   a handle or (handle;timeout), and a SQL string
/   .odbc.close  a handle
/   .odbc.tables a handle
/ .
/ Not carried over: odbc.k's Access/SQL-Server file-DSN conveniences (a symbol
/ ending .dsn, .mdb or .mdf), fkeys, keys, views, load. A dotted symbol is
/ refused rather than guessed at. Nothing in this tree uses them.
/ .
/ Nothing here binds odbc.so at load time. The library is bound by .odbc.bind,
/ on first use, so this file loads on any interpreter and the refusal logic
/ (unknown handles) is testable without a driver. A q that cannot load a shared
/ library, or has no odbc.so in QHOME, fails in .odbc.bind - which is exactly
/ what .qetl.io.odbc.available traps.
/ .
/ Handles are registered on open and checked on eval and close: the native
/ library treats a stale handle as a pointer, and a refusal is better than that.
/ .
/ Whole-day datetime columns: odbc.so returns an SQL DATE or a midnight TIMESTAMP
/ as a legacy datetime column (15h). odbc.k turns every such column whose values
/ are all whole days into dates, and so does eval here - the legacy type is
/ matched because it is what the native library returns, and no datetime is made.

/ The native entry points, once bound: a dictionary of name to function.
/ Empty until .odbc.bind runs; a test double may assign it instead.
/ @private
.odbc.natives:()!()

/ The handles open has returned and close has not yet released.
/ @private
.odbc.handles:()

/ Bind odbc.so's open, close, eval and tables, once.
/ .
/ `2:` finds odbc.so in the current directory first, then QHOME's platform
/ directory (l64, m64). Throws when the interpreter cannot load libraries or
/ the file is absent; leaves .odbc.natives empty in that case.
/ @return 1b
/ @throws error from `2:` when odbc.so cannot be loaded
/ @eg .odbc.bind[]
.odbc.bind:{[]
    if[count .odbc.natives; :1b];
    names:`open`close`eval`tables;
    .odbc.natives:names!{`odbc 2:(x;y)}'[names;2 1 3 1];
    1b}

/ Convert the datetime columns that hold only whole days to dates, as odbc.k does.
/ A column that holds any time of day, and every other column, is left alone.
/ A result that is not a table (a row count, for instance) is returned as is.
/ @param t a query result
/ @return t, with its whole-day datetime columns as dates
/ @private
.odbc.whole_days:{[t]
    if[not 98h=type t; :t];
    c:cols t;
    dt:c where 15h=type each t c;
    whole:dt where {[v] all v=`date$v} each t dt;
    if[not count whole; :t];
    ![t;();0b;whole!{(`date$;x)} each whole]}

/ Open a connection and register its handle.
/ @param x a connection string, a DSN symbol, or a (string;timeout) pair
/ @return the connection handle
/ @throws error from the driver when the connection fails
/ @throws error naming the symbol when a DSN symbol contains a dot
/ @eg .odbc.open "DRIVER=DuckDB;Database=:memory:"
.odbc.open:{[x]
    .odbc.bind[];
    timeout:0;
    if[0h=type x; timeout:last x; x:first x];
    if[(-11h=type x) and "." in string x;
        '"odbc.open: a dotted DSN symbol is unsupported"];
    h:.odbc.natives[`open][x;timeout];
    .odbc.handles,:h;
    h}

/ Release a registered handle.
/ @param h a handle returned by .odbc.open
/ @return the native library's answer
/ @throws `invalid when h is not an open handle
/ @eg .odbc.close h
.odbc.close:{[h]
    if[not .odbc.registered h; 'invalid];
    .odbc.handles:.odbc.handles except h;
    .odbc.natives[`close] h}

/ Run SQL on a registered handle.
/ @param x a handle, or a (handle;timeout) pair
/ @param y the SQL, as a string
/ @return a table (whole-day datetime columns as dates), or whatever the driver returns for a statement
/ @throws `invalid when the handle is not an open handle
/ @eg .odbc.eval[h; "select 1 as a"]
.odbc.eval:{[x;y]
    timeout:0;
    if[1<count x; timeout:last x; x:first x];
    if[not .odbc.registered x; 'invalid];
    .odbc.whole_days[.odbc.natives[`eval][x;y;timeout]]}

/ The tables a registered handle can see.
/ @param h a handle returned by .odbc.open
/ @return what the native library returns
/ @eg .odbc.tables h
.odbc.tables:{[h] .odbc.bind[]; .odbc.natives[`tables] h}

/ Is h an open handle?
/ @private
.odbc.registered:{[h] h in .odbc.handles}
