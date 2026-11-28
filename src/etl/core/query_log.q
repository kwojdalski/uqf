/ query_log.q - the one road a query takes to the log (.qetl.query).
/ .
/ When something reads the wrong rows the first question is "what did it
/ actually run?". Every place the framework builds a query and runs it - a
/ lambda sent to another process, a functional select over a partition, a
/ read through the gateway - goes through `.qetl.query.run`, so ONE switch
/ (.qetl.log.queries, per process, runtime-settable; `-trace` sets it too)
/ shows them all, and one function decides how a query is spelt.
/ .
/ With the switch on, a query logs one INFO line when it has run:
/ .
/   request=3 transport=`ipc target=`hdb1 qsql_form=`qsql qsql="select ..." rows=120 ms=14
/ .
/ `qsql` is the query as readable qSQL (.qetl.qsql, which parses its rendering
/ back and falls back to the whole parse tree - `qsql_form` says which you
/ got). `target` is `local` or the NAME of the process it was sent to. A handle,
/ a host and port, a user or a password is never an input here: the caller
/ passes the handle to RUN the query, never to describe it.
/ .
/ With TRACE on the line is the source trace's own pair instead - "query
/ sent" before, "query returned" or "query failed" after - so a hung query is
/ visible while it hangs. With neither on the query is only run: nothing is
/ numbered, timed or rendered, and the renderer is not called.
/ .
/ A new query site MUST use this. The query-sites gate (`python3
/ scripts/gates/check.py query-sites`, scripts/gates/policy/query_sites.py)
/ refuses a functional select or a gateway call that neither does nor says
/ why it need not.

\d .

/ Is any query logging on - the queries switch, or TRACE?
/ @return 1b when `run` will log
/ @eg .qetl.query.enabled[]  ->  0b
.qetl.query.enabled:{[] .qetl.log.queries_enabled or .qetl.log.enabled`TRACE}

/ Private: a query lambda's text for a trace - IN FULL. `string` for a
/ lambda: -3! stops at the console width (79 characters by default). A
/ projection has no source text of its own, so it is rendered at the widest
/ console .qetl.log.value1 allows.
/ @param f the function sent
/ @return its text
/ @eg count .qetl.query.call_text {[a;b] a+b}  ->  11
/ @private
.qetl.query.call_text:{[f] $[100h=type f; string f; .qetl.log.value1 f]}

/ Private: the query as qSQL, for a log line - `f` and `args` resolved by
/ .qetl.qsql.of_call, or the spec's own `describe` thunk for a query that is
/ not qSQL (a program string, a gateway request). A rendering that throws is
/ reported as the line's own `qsql_error` rather than costing the query.
/ @param spec the query's spec (see run)
/ @return a dictionary of `qsql_form and `qsql
/ @eg first key .qetl.query.shown `f`args!({[a] a+1};enlist 41)  ->  `qsql_form
/ @private
.qetl.query.shown:{[spec]
    r:@[{[s] $[`describe in key s; s[`describe][]; .qetl.qsql.of_call[s`f;s`args]]};spec;{[e] (`error;"qsql: ",e)}];
    `qsql_form`qsql!(r 0;r 1)}

/ Private: a log call that can never cost a query its answer.
/ @private
.qetl.query.say:{[level;channel;text;fields]
    .[{[l;c;t;f] .qetl.log.line[l;.qetl.log.require_channel c;t;f]};(level;channel;text;fields);::];}

/ Run a query and log it (see the file header).
/ .
/ `spec` describes the query and `run` is what runs it - a function of one
/ argument, called as run[::], usually a projection that closed over the
/ handle and the message: `{[h;msg;unused_u] h msg}[h;msg]`. The spec holds
/   channel    the .qetl.log.channels name the line goes under
/   transport  `ipc, `local, `csv, `hdb, `gateway ...
/   target     `local, or the name of the process it was sent to
/   f, args    the lambda and its arguments, rendered with .qetl.qsql.of_call
/   describe   optional: a function of no arguments returning (form;text),
/              for a query that is not a lambda
/   shown      optional: extra fields for the TRACE "query sent" line
/ Nothing is rendered unless a line will be logged. A query that throws logs
/ "query failed" with the error, and rethrows it.
/ @param spec a dictionary as above
/ @param run a function called as run[::]
/ @return what `run` returns
/ @eg .qetl.query.run[`channel`transport`target`f`args!(`storage;`local;`local;{[a] a+1};enlist 1);{[u] 2}]  ->  2
.qetl.query.run:{[spec;run]
    tr:.qetl.log.enabled`TRACE;
    if[not .qetl.log.queries_enabled or tr; :run[::]];
    t0:.z.p;
    ch:spec`channel;
    req:`transport`request!(spec`transport;.qetl.log.next_request[]);
    sh:.qetl.query.shown spec;
    if[tr;
        call:$[`f in key spec; enlist[`call]!enlist .qetl.query.call_text spec`f; ()!()];
        extra:$[`shown in key spec; spec`shown; ()!()];
        .qetl.query.say[`TRACE;ch;"query sent";call,sh,req,(enlist[`target]!enlist spec`target),extra]];
    r:@[run;::;{[say;ch;tr;req;sh;tg;t0;e]
        ms:`long$(.z.p-t0)%1000000;
        $[tr; say[`TRACE;ch;"query failed";req,`error`ms!(e;ms)];
            say[`WARNING;ch;"query failed";req,sh,(enlist[`target]!enlist tg),`error`ms!(e;ms)]];
        'e}[.qetl.query.say;ch;tr;req;sh;spec`target;t0]];
    ms:`long$(.z.p-t0)%1000000;
    $[tr; .qetl.query.say[`TRACE;ch;"query returned";req,`rows`ms!(count r;ms)];
        .qetl.query.say[`INFO;ch;"query";req,sh,(enlist[`target]!enlist spec`target),`rows`ms!(count r;ms)]];
    r}

/ Run `f` on `args` in this process, as a logged query: f . args.
/ @param channel the log channel
/ @param f the query lambda
/ @param args its arguments, a list
/ @return f . args
/ @eg .qetl.query.local[`storage;{[a;b] a+b};1 2]  ->  3
.qetl.query.local:{[channel;f;args]
    .qetl.query.run[`channel`transport`target`f`args!(channel;`local;`local;f;args);{[f;args;unused_u] .[f;args]}[f;args]]}

/ The name of the process a handle reaches, for a log line - never the handle,
/ the host or the port. `remote when TorQ's server table is not there or does
/ not know the handle.
/ @param h an open handle
/ @return a symbol
/ @eg .qetl.query.target_of 0i  ->  `remote
.qetl.query.target_of:{[h] @[{[hd] .qetl.job.bounded.runtime.procname_of hd};h;{[e] `remote}]}

\d .
