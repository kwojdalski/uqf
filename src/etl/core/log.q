/ log.q - the logging contract for ETL workers (.qetl.log).
/ .
/ Answers the question bank: "bare .lg.o/.lg.e, or a levelled layer on
/ top?" A levelled layer, and a thin one - four functions over TorQ's own
/ .lg, adding exactly the two things TorQ lacks and nothing else.
/ .
/ WHAT TORQ ALREADY PROVIDES, AND IS NOT DUPLICATED
/ .
/ TorQ's .lg.l takes [level;proctype;proc;id;message;dict], formats to
/ pipe-delimited or JSON (-jsonlogs), routes to stdout/stderr through
/ .lg.outmap, and publishes through .lg.pubmap. That is a complete transport
/ and this file does not reimplement any of it. Every message here ends up
/ in .lg.l, so `-jsonlogs`, log rolling and publication all keep working.
/ .
/ THE THINGS TORQ LACKS
/ .
/   1. A DEBUG level. .lg.outmap knows ERROR/ERR/INF/WARN only, and
/      `debug_level`, `verbose` and `log_verbose` in the requirements imply
/      a fourth. Without one, diagnostic detail is either always on (and a
/      million-row backfill floods its log with per-window lines) or never
/      on (and a stuck worker gives you nothing). DEBUG is registered in
/      outmap at 0 - off - and switched on per process with .qetl.log.debug[].
/ .
/   2. A TRACE level, below DEBUG: the exact query a source sends - the SQL
/      statement, or the q lambda and its arguments - logged before it goes
/      out and again with what came back. One per window, and long, so it
/      has its own switch, .qetl.log.trace[]: switching debug on to follow
/      a run must not bury it under every statement.
/ .
/   3. Structure. A worker logging "window done" is useless in aggregate;
/      a worker logging window=[from;to) rows=1234 is greppable across a
/      fleet. Every function here takes a DICT of fields and renders it
/      k=v, so the field names are the same in every worker and a log line
/      from one is comparable with a log line from another. That is what
/      "every worker needs the same answer or logs become unreadable in
/      aggregate" means in practice.
/ .
/ WHY THIS WORKS WITHOUT TORQ LOADED
/ .
/ The ETL core is unit-tested standalone, where .lg does not exist. So the
/ transport is resolved AT CALL TIME: .lg.l when present, a plain -1 to
/ stdout when not. Resolving at load time would bind the fallback forever
/ in any process that loads this file before torq.q - and q's late binding
/ of names is precisely what makes the call-time check cheap.

\d .qetl.log

/ Level names, in severity order - the full words, and the only five a log
/ line carries. TorQ's own logger writes INF, WARN and ERR; every TorQ
/ process this stack starts loads scripts/torqconfig/settings/default.q,
/ which renames those three on their way into the file, so a log reads one
/ vocabulary whichever layer wrote the line.
levels:`TRACE`DEBUG`INFO`WARNING`ERROR

/ The levels off unless a process switches them on, each with its own switch.
quiet:`TRACE`DEBUG

/ How TorQ routes the levels it already knows, for the names this file uses
/ instead: (TorQ's name; this file's). outmap and pubmap are keyed by level,
/ so a level they do not hold is neither printed nor published - INFO
/ unregistered would silence every line.
torq_names:`INF`WARN`ERR!`INFO`WARNING`ERROR

/ Register this file's levels with TorQ's routing tables if TorQ is loaded
/ and has not heard of them: INFO, WARNING and ERROR routed as TorQ routes
/ INF, WARN and ERR; TRACE and DEBUG off (0), so nothing changes for a
/ process until it opts in. Idempotent, and called by the first line logged.
register:{[]
    if[not torq_loaded[]; :0b];
    {[torq;ours]
        if[not ours in key .lg.outmap; .lg.outmap[ours]:0^.lg.outmap torq];
        if[not ours in key .lg.pubmap; .lg.pubmap[ours]:0^.lg.pubmap torq]}'[key torq_names;value torq_names];
    {if[not x in key .lg.outmap; .lg.outmap[x]:0];
     if[not x in key .lg.pubmap; .lg.pubmap[x]:0]} each quiet;
    1b}

/ Switch debug output on (or off) for THIS process.
/ .
/ Per process, not global, because the case for debug is one misbehaving
/ worker - turning it on fleet-wide is how you lose the signal in the noise
/ you switched it on to find.
/ @param on 1b to emit DEBUG lines, 0b to suppress them
debug:{[on]
    register[];
    if[torq_loaded[]; .lg.outmap[`DEBUG]:$[on;1;0]];
    debug_enabled::on;
    on}

debug_enabled:0b

/ Switch trace output - every query a source sends - on (or off) for THIS
/ process. Independent of debug: either can be on without the other.
/ @param on 1b to emit TRACE lines, 0b to suppress them
trace:{[on]
    register[];
    if[torq_loaded[]; .lg.outmap[`TRACE]:$[on;1;0]];
    trace_enabled::on;
    on}

trace_enabled:0b

/ Private: is a quiet level switched on, outside TorQ?
switched:{[level] $[level=`DEBUG; debug_enabled; level=`TRACE; trace_enabled; 1b]}

/ Private: render a field dict as space-separated k=v, values via .Q.s1 so a
/ symbol, a timestamp and a string all render unambiguously and a list does
/ not spread across the line. Field ORDER is preserved, so a worker that
/ always logs (worker;window;rows) produces columns a human can scan.
render:{[fields]
    if[0=count fields; :""];
    " " sv {[k;v] string[k],"=",value1 v}'[key fields;value fields]}

/ Private: one field's value as q text, IN FULL.
/ .
/ Not bare .Q.s1: on KDB-X it stops at the console width (\c, 80 columns by
/ default) and ends the text with "..", so a traced SQL statement or query
/ lambda was logged cut off at 79 characters - the one place the whole
/ query was meant to be visible. A string is escaped here, with no limit;
/ anything else is rendered with the console widened to its 2000-column
/ maximum for the one call, and put back even if rendering throws.
/ @param v any value
/ @return its q literal, as .Q.s1 spells it
value1:{[v]
    if[10h=type v; :quoted v];
    c:@[system;"c";{[e] ()}];
    if[2<>count c; :.Q.s1 v];
    @[system;"c ",string[c 0]," 2000";::];
    r:@[.Q.s1;v;{[e] "'",e}];
    @[system;"c "," " sv string c;::];
    r}

/ Private: a string as a q string literal - quoted, with \ " newline,
/ carriage return and tab escaped as q writes them, and every other byte
/ outside printable ASCII as a three-digit octal escape - exactly as -3!
/ spells it, without its console-width cut.
/ @param s a string
/ @return the literal, e.g. "\"a\\nb\""
quoted:{[s]
    esc:{[ch] i:`int$ch;
        $[ch in "\\\""; "\\",ch;
          ch="\n"; "\\n";
          ch="\r"; "\\r";
          ch="\t"; "\\t";
          (i<32) or i>126; "\\",raze string 8 8 8 vs i;
          enlist ch]};
    "\"",(raze esc each s),"\""}

/ Private: is TorQ's logging loaded?
/ .
/ `l in key `.lg, NOT `lg in key `. - the latter is always false, because
/ key `. lists the ROOT namespace's own names and namespaces are not among
/ them. The first draft used it, so this layer took the stdout fallback in
/ every process including ones with TorQ fully loaded: right level gating,
/ wrong transport, and no error to say so. Caught only by running the layer
/ against TorQ's real .lg definitions and noticing the line had three
/ fields where TorQ's format has six.
torq_loaded:{[] @[{`l in key x};`.lg;{0b}]}

/ Private: the transport. TorQ's .lg.l when loaded, stdout otherwise.
/ .
/ Level gating outside TorQ mirrors TorQ's own default: DEBUG is suppressed
/ unless debug[] was called, TRACE unless trace[] was, everything else prints.
/ That way a test that asserts "this DEBUG line was not emitted" gets the same
/ answer whether or not torq.q happens to be loaded.
emit:{[level;id;msg]
    $[torq_loaded[];
        .lg.l[level;`etl;id;id;msg;()!()];
      not switched level;
        ::;
      -1 "|" sv string[(.z.p;level;id)],enlist msg]}

/ Private: is this level going to be emitted at all?
/ .
/ Split out so `line` can check BEFORE rendering. Mirrors the transport's own
/ gating: TorQ's outmap when it is loaded, the debug switch when it is not.
enabled:{[level]
    if[torq_loaded[]; if[not level in key .lg.outmap; register[]]];
    $[torq_loaded[];
        0<0^.lg.outmap level;
      switched level]}

/ Private: assemble and emit one line.
/ .
/ The suppression check comes FIRST, before the fields are rendered (bank
/ the logging question). Rendering a message nobody will read is pure waste, and
/ the waste is concentrated exactly where it hurts: DEBUG is off by default and
/ is the level a worker emits per WINDOW, so a million-row backfill with
/ debug off would otherwise render and discard one message per window.
/ .
/ q has no lazy arguments, so a caller that wants to avoid building an
/ expensive FIELD VALUE still has to check `enabled` itself - this only
/ avoids the rendering, not the caller's own work. That is the honest limit
/ of the fix, and it is why `enabled` is not private.
/ Written as `if[enabled ...; emit ...]` rather than an early return: the
/ first draft used `if[not enabled level; :::]` and `:::` does NOT parse as
/ "return generic null" - the if body was a no-op, execution fell through,
/ and the rendering happened anyway. The gate read as correct and did
/ nothing. Wrapping the emit has no such ambiguity.
line:{[level;id;text;fields]
    if[enabled level;
        f:with_scope fields;
        emit[level;id;$[0=count f; text; text," ",render f]]];
    }

/ ------------------------------------------------------- SCOPED CONTEXT
/ .
/ What a line is ABOUT - the run, worker, source, window and attempt it was
/ logged inside - added to every line logged in that scope, so a request
/ trace, the fetch that made it, the sidecar's own normalisation and the
/ write after it can all be put side by side. A line's own field of the
/ same name wins; context fields follow the line's own.
/ .
/ Set by with_context around a piece of work and restored when it ends,
/ whether it returned or threw, so one worker's or window's context can
/ never leak into the next.
/ .
/ WHAT IS INSIDE A SCOPE, AND WHAT IS NOT
/ .
/ The context is one process global, not a property of the code that set
/ it. A line gets whatever fields are set when it is logged, which means
/ every call made while with_context's work is on the stack, whoever wrote
/ that call:
/ .
/   - A reaction fired from the write path IS inside. notify_published runs
/     synchronously from window_body, so a reaction's lines carry the
/     publishing window's worker, run and range_from/range_to, even though
/     the reaction is not part of that window. The reaction's own fields win
/     on a clash, so it can set its own `worker` field to override.
/     Reactions replayed at the start of a run (replay_reactions) run before
/     any window opens, so they carry none of these fields.
/   - Timers (.z.ts) and IPC handlers (.z.pg, .z.ps) are NOT inside. q runs
/     one thing at a time, and the event loop that calls them does not turn
/     while a window is on the stack (sleep_ms blocks in the shell, it does
/     not yield), so they run between windows and see the context as it
/     was outside. Anything added later that yields to the event loop in
/     the middle of a scope would break this: a handler it let run would
/     inherit the window's fields.

context:()!()

/ Private: a line's fields, then whatever context it does not set itself.
with_scope:{[fields]
    if[0=count context; :fields];
    f:$[0=count fields; ()!(); fields];
    f,(key[context] except key f)#context}

/ Run f[args] with `ctx` added to the context, and restore the context
/ afterwards - on success or on error, which is rethrown.
/ @param ctx a dict of fields, e.g. `worker`source!(`w;`s)
/ @param f the function to run
/ @param args its arguments, as a list; enlist(::) for a niladic f
/ @return what f returns
/ @eg .qetl.log.with_context[enlist[`worker]!enlist `w;{x+1};enlist 1] -> 2
with_context:{[ctx;f;args]
    / `outer`, not `prev`: prev is a q builtin, and assigning it throws
    / 'assign when the file loads.
    outer:context;
    context::outer,ctx;
    restore:{[outer;e] .qetl.log.context:outer; 'e}[outer];
    / A niladic f through @, not `.`: on KDB-X `.[f;enlist ::;handler]` throws
    / an UNCATCHABLE 'type - the handler never runs, and the context leaked
    / into every line after it.
    / The generic null itself, not any 101h: a unary primitive (neg, til)
    / is 101h too, and passing one as the argument is a real call. Spell the
    / niladic case enlist(::) - `enlist ::` is a bare primitive, not a list.
    niladic:(args~(::)) or (1=count args) and (::)~first args;
    r:$[niladic; @[f;::;restore]; .[f;args;restore]];
    context::outer;
    r}

/ Number one outgoing request, so its sent, returned and failed lines carry
/ the same `request` and two requests in one window stay apart.
/ .
/ The count is per process, starts at 1 in each one, and is never
/ persisted. In logs merged from several processes, `request` identifies a
/ request only together with the process that logged it, and the process
/ comes from the log file (out_<procname>.log), not from the line itself.
/ emit passes the worker id in TorQ's proc slot, and the stdout fallback
/ writes no process at all.
/ @return the next request number in this process
request_seq:0
next_request:{[] request_seq::request_seq+1; request_seq}

/ The five levels. `id` is the worker or component name - it becomes
/ TorQ's `id` column, so `select from logmsg where id=`demo_deals_backfill`
/ works on a published log.
/ @param id the worker or component, as a symbol
/ @param text a short fixed message - what happened, not the values
/ @param fields a dict of the values, rendered k=v after the text
/ @eg .qetl.log.info[`demo_deals_backfill;"window published";
/        `range_from`range_to`rows!(2026.09.11D00:00;2026.09.12D00:00;1234)]
trc:{[id;text;fields]  line[`TRACE;id;text;fields]}
dbg:{[id;text;fields]  line[`DEBUG;id;text;fields]}
info:{[id;text;fields] line[`INFO;id;text;fields]}
warn:{[id;text;fields] line[`WARNING;id;text;fields]}

/ Log an error. Does NOT throw and does NOT exit - TorQ's .lg.e does both
/ depending on .proc state, which is right for an init failure and wrong
/ for a worker recording that one window failed and moving on. A
/ worker that wants to abort throws itself; this only records.
err:{[id;text;fields]  line[`ERROR;id;text;fields]}

\d .
