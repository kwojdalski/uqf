/ source_fetch.q - fetching a window: the ipc and mock transports, fixture windowing, zone bounds.
/ .
/ Part of the external-source contract (.qetl.source): one namespace spread over
/ several files, loaded in order by src/etl/init.q after source_contract.q, which
/ carries the design record. Public names are unchanged by the split (#970).

\d .qetl.source

/ ------------------------------------------------------------- FETCHING

/ Send an IPC source's query: call `f` on the handle with the window's bounds,
/ logging the call at TRACE (.qetl.log.trace) - the lambda's text and the bounds
/ it runs with - before it is sent, and the rows and milliseconds when it
/ returns.
/ .
/ The ipc counterpart of .qetl.io.odbc.run_sql, so one switch shows every
/ query a backfill sends whichever transport it uses. A source's `query`
/ calls this rather than `h(f;from;to)` itself - a direct call is invisible
/ to tracing, which python/uqs/tests/test_source_queries.py refuses.
/ @param h the handle to the source process
/ @param f the function the source process runs, taking (from_ts;to_ts)
/ @param range_from the inclusive lower bound, in the source's clock
/ @param range_to the exclusive upper bound
/ @return what `f` returns on the far side: the window's rows
/ @eg .qetl.source.ipc[{value x};{[a;b] ([] x:a,b)};1;2] -> ([] x:1 2)
/ .
/ Each request is numbered (.qetl.log.next_request), so its sent, returned
/ and failed lines share `request`, and two requests in one window - a
/ source that reads two tables - stay apart. The run, worker, source,
/ window and attempt come from the scoped log context the bounded worker
/ sets, so a sidecar sending through here is correlated without saying so.
/ With TRACE off nothing is numbered, timed or formatted: the query is sent.
ipc:{[h;f;range_from;range_to]
    traced_ipc[h;f;(range_from;range_to);`range_from`range_to!(range_from;range_to)]}

/ .qetl.source.ipc for any arguments: `h(f;a;b...)`, traced the same way.
/ .
/ What a POLLING feed's fetch sends through: its query takes a cursor, not a
/ window, so `h({[c] select ... where time>c};cursor)` becomes
/ .qetl.source.ipc_call[h;{[c] select ... where time>c};enlist cursor] - and
/ `uqs stream preview --trace` shows it, as `uqs backfill --trace` shows a
/ window's. The arguments are logged at TRACE, so never pass a credential as
/ one: credentials belong to the handle, which is not logged.
/ @param h the handle to the source process
/ @param f the function the source process runs
/ @param args its arguments, as a list - enlist cursor for one
/ @return what `f` returns on the far side
/ @eg .qetl.source.ipc_call[{value x};{[c] c+1};enlist 41] -> 42
ipc_call:{[h;f;args] traced_ipc[h;f;args;enlist[`args]!enlist args]}

/ Private: send `f` with `args` over `h`, logging it at TRACE with `shown`
/ beside the call - before it is sent, and with the rows and milliseconds,
/ or the error, when it comes back. With TRACE off it is just sent.
/ @private
traced_ipc:{[h;f;args;shown]
    msg:enlist[f],args;
    if[not .qetl.log.enabled`TRACE; :h msg];
    t0:.z.p;
    req:`transport`request!(`ipc;.qetl.log.next_request[]);
    .[{.qetl.log.trc[x;y;z]};(`ipc;"query sent";(enlist[`call]!enlist call_text f),req,shown);::];
    r:@[h;msg;{[req;t0;e]
        .[{.qetl.log.trc[x;y;z]};(`ipc;"query failed";
            req,`error`ms!(e;`long$(.z.p-t0)%1000000));::];
        'e}[req;t0]];
    .[{.qetl.log.trc[x;y;z]};(`ipc;"query returned";
        req,`rows`ms!(count r;`long$(.z.p-t0)%1000000));::];
    r}

/ Private: a query lambda's text for a trace - IN FULL. `string` for a
/ lambda: -3! stops at the console width (79 characters by default), and cut
/ the traced query before log.q's full-width rendering ever saw it. A
/ projection has no source text of its own, so it is rendered at the widest
/ console .qetl.log.value1 allows.
/ @param f the function sent
/ @return its text
/ @eg count .qetl.source.call_text {[a;b] a+b} -> 11
/ @private
call_text:{[f] $[100h=type f; string f; .qetl.log.value1 f]}

/ A mock source's seed, from its credential.
/ @param cred the credential, a string
/ @return a long
/ @throws error when the credential is not an integer
/ @eg .qetl.source.mock_seed "42"  ->  42
mock_seed:{[cred]
    s:"J"$cred;
    if[null s; '"mock: the credential is an integer seed, such as 42"];
    s}

/ A mock table's columns: what the mock source reading it declares - there
/ is no table anywhere to ask.
/ @param tbl the table a mock source reads
/ @return ([] c:symbols; t:chars), one row per declared column
/ @throws error when no mock source reads `tbl`
mock_meta:{[tbl]
    / Indexed rather than selected: in qSQL `transport` is this table's
    / column or .qetl.source.transport, and the PeachQ converter will not guess.
    src:0!.qetl.source.sources;
    mock:src[`transport]=`mock;
    / A raw contract names the physical table; it is what the mock holds.
    byraw:where mock & {[tbl;r] tbl in key r}[tbl] each src`raw;
    if[count byraw;
        c:(src[`raw] first byraw) tbl;
        :([] c:cols c; t:type_chars[c])];
    s:first src[`name] where (src[`table_name]=tbl) & mock;
    if[null s; '"mock_meta: no mock source reads ",string tbl];
    d:.qetl.source.sources s;
    ([] c:d`columns; t:d`types)}

/ Fetch one window, from the live source or from the fixture.
/ .
/ The fixture is not a fallback for a FAILED connection - that would turn an
/ outage into silently synthetic data, which is the worst possible outcome
/ for a coverage ledger that then records the window as complete. It is the
/ path taken when no credential is configured at all, which is an explicit
/ statement that this is a demo.
/ .
/ So: credential present means live, and a failure there is a failure.
/ Credential absent means fixture, and that is announced in the return value
/ rather than inferred.
/ @param source a registered source name
/ @param h an open handle, or 0Ni when running on the fixture
/ @param range_from window start
/ @param range_to window end, exclusive
/ The fixture is WINDOWED here, on the declared time_column, using the same
/ half-open [range_from;range_to) bounds the live query uses. Without that
/ the fixture returns every row for every window, so a three-window run
/ publishes the fixture three times - triplicating the data while coverage
/ records each window as correctly complete. Nothing errors, the row counts
/ merely lie, and the target table quietly holds three copies.
/ .
/ It is done here rather than in each fixture so a fixture author cannot
/ forget it, and so the windowing is provably the same on both paths - the
/ live query filters `>=from, <to` and so does this.
/ @return (`live or `fixture; the table)
/ .
/ For a non-UTC source the window is translated in BOTH directions here -
/ bounds out, timestamps back - so neither the query nor the fixture author
/ has to know about zones. See source_bounds and narrow_to_utc.
fetch_window:{[source;h;range_from;range_to]
    t0:.z.p;
    decl:def[source];
    bounds:source_bounds[decl;range_from;range_to];
    .[{.qetl.log.dbg[x;y;z]};(source;"fetching";
        `path`range_from`range_to`source_from`source_to`tz!
            ($[null h;`fixture;`live];range_from;range_to;bounds 0;bounds 1;decl`tz));::];
    page:$[null h;
        (`fixture;window_fixture[decl;bounds 0;bounds 1]);
        (`live;(decl`query)[h;bounds 0;bounds 1])];
    out:narrow_to_utc[decl;page 1;range_from;range_to];
    / fetched vs kept differ only for a zoned source, whose bounds are padded:
    / the difference is the neighbouring windows' rows, dropped on purpose.
    .[{.qetl.log.dbg[x;y;z]};(source;"fetched";
        `path`fetched`kept`ms!(page 0;count primary[source;page 1];count primary[source;out];
            `long$(.z.p-t0)%1000000));::];
    (page 0;out)}

/ How far to widen a non-UTC source's window, in its own clock.
/ .
/ One day, which is far more than any offset change in tzdata (the largest
/ is the date-line jump, 24h). It buys correctness at the cost of
/ over-fetching, and the exact narrowing in narrow_to_utc throws the excess
/ away - so the only cost is bandwidth on sources that are not UTC.
bound_padding:1D

/ Private: the window bounds to hand the source, in the SOURCE's clock.
/ .
/ For `UTC this is the identity, and that is the path every source in this
/ tree takes today.
/ .
/ For a zoned source the bounds are deliberately WIDE rather than exact, and
/ this is the trap worth stating because the obvious implementation is
/ silently wrong. Converting each bound with the offset in effect AT THAT
/ BOUND is not monotonic across an autumn transition: measured for
/ Europe/London, the UTC window [2026.10.25D00:30; 2026.10.25D01:30)
/ converts to local [01:30; 01:30) - an EMPTY range. The query returns no
/ rows, nothing errors, and the coverage ledger records an hour of missing
/ trades as a complete window.
/ .
/ So: pad the bounds, fetch a superset, and narrow exactly in UTC afterwards
/ where the arithmetic is unambiguous.
/ @private
source_bounds:{[decl;range_from;range_to]
    tz:decl`tz;
    if[`UTC~tz; :(range_from;range_to)];
    require_zone_table[tz];
    (utc_to_local[tz;range_from-bound_padding];
     utc_to_local[tz;range_to+bound_padding])}

/ Private: convert a fetched page's time_column to UTC and narrow it to the
/ requested half-open range.
/ .
/ For `UTC this is the identity: the query (or window_fixture) has already
/ applied [range_from;range_to) and re-filtering would be dead code that
/ could only ever disagree.
/ .
/ For a zoned source the narrowing is NOT optional - source_bounds
/ deliberately over-fetched, so without this every window would publish up
/ to a day of its neighbours' rows while coverage recorded the narrow range.
/ .
/ It does NOT simply call local_to_utc on the column, and the reason is the
/ padding above. local_to_utc refuses an ambiguous reading outright, which is
/ right when a caller asks about one instant - but here the over-fetch has
/ pulled in up to a day of a NEIGHBOUR's rows, and failing this window
/ because of an ambiguous row that belongs to the next one would make every
/ window on an autumn transition day unbackfillable, not just the affected
/ hour. So the rule is range-scoped:
/ .
/   - an ambiguous row is refused only if one of its candidate instants
/     actually lands in [range_from;range_to). Otherwise it is dropped: it is
/     not this window's row, and the window that owns it will refuse it.
/   - a NONEXISTENT reading is refused unconditionally, range or no range.
/     There is no instant to compare against a range, and a source emitting a
/     wall-clock time its own calendar never had means the declared zone is
/     wrong - which is a contract breach, not a windowing question.
/ @private
narrow_to_utc:{[decl;tbl;range_from;range_to]
    tz:decl`tz;
    if[`UTC~tz; :tbl];
    f:decl`time_column;
    local_ts:tbl f;
    if[0=count local_ts; :tbl];
    cs:local_candidates[tz;local_ts];
    nvalid:count each cs;
    if[any 0=nvalid;
        '"narrow_to_utc: ",nonexistent_message[tz;first local_ts where 0=nvalid]];
    in_range:{[lo;hi;c] any (c>=lo) and c<hi}[range_from;range_to] each cs;
    if[any in_range and 1<nvalid;
        pos:first where in_range and 1<nvalid;
        '"narrow_to_utc: ",ambiguous_message[tz;local_ts pos;cs pos]];
    / Every surviving row now has exactly one reading, so the conversion is
    / unambiguous and the half-open bound is applied in UTC - the direction
    / where the arithmetic cannot double-count or skip.
    kept:tbl where in_range;
    ![kept;();0b;(enlist f)!enlist enlist first each cs where in_range]}

/ Private: apply the window to a fixture, on its declared time_column.
/ .
/ Functional select (`?[t;where;0b;()]`) rather than qSQL, because the column
/ name is a variable: `select from t where time_column>=from_ts` would compare
/ the literal symbol, not the column it names.
/ @private
window_fixture:{[decl;range_from;range_to]
    t:(decl`fixture)[];
    if[0=count decl`supporting; :window_rows[decl;t;range_from;range_to]];
    / Only the primary owns the window; supporting rows are context and may
    / lie outside it.
    if[not 99h=type t;
        '"window_fixture: ",string[decl`source],"'s fixture must return a dict of its inputs - it declares supporting input(s) ",
         ", " sv string key decl`supporting];
    @[t;decl`table_name;window_rows[decl;;range_from;range_to]]}

/ Private: one table's rows inside [range_from;range_to) on time_column.
/ @private
window_rows:{[decl;t;range_from;range_to]
    f:decl`time_column;
    if[not f in column_names t;
        '"window_fixture: ",string[decl`source],"'s fixture has no ",string[f],
         " column, so the window cannot be applied - it would return every row for every window and triplicate the data"];
    ?[t;((>=;f;range_from);(<;f;range_to));0b;()]}

\d .
