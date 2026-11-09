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
/ PROCESS AND CHANNEL (#2274)
/ .
/ A record answers two different questions, and TorQ's line has a column for
/ each: `proc` is WHICH PROCESS wrote it, and `id` is WHICH SUBSYSTEM it
/ concerns - the channel (see .qetl.log.channels). This file used to put the
/ worker or component name in both, so `proc` named a source rather than the
/ process, and `id` named nothing a reader could select on. Now `proc` is the
/ emitting process (TorQ's procname; outside TorQ, the runner's name -
/ .qetl.log.process), and a call made through .qetl.log.ch carries a
/ channel from the one declared list: every call goes through .qetl.log.ch,
/ which refuses a name that is not declared. There is no id-first form, so a
/ worker or source name can no longer pass as a category (#2279).
/ .
/ A channel FILTERS WHAT A READER SHOWS (`uqs logs --channel`). It does not
/ stop a record being generated, and does not save the cost of rendering it:
/ that is the level's job (.qetl.log.enabled). Per-channel verbosity is
/ follow-up work, not a property of this list.
/ .
/ WHY THIS WORKS WITHOUT TORQ LOADED
/ .
/ The ETL core is unit-tested standalone, where .lg does not exist. So the
/ transport is resolved AT CALL TIME: .lg.l when present, a plain -1 to
/ stdout when not. Resolving at load time would bind the fallback forever
/ in any process that loads this file before torq.q - and q's late binding
/ of names is precisely what makes the call-time check cheap.

\d .

/ Level names, in severity order - the full words, and the only five a log
/ line carries. TorQ's own logger writes INF, WARN and ERR; every TorQ
/ process this stack starts loads scripts/torqconfig/settings/default.q,
/ which renames those three on their way into the file, so a log reads one
/ vocabulary whichever layer wrote the line.
.qetl.log.levels:`TRACE`DEBUG`INFO`WARNING`ERROR

/ The channels - the subsystems a record can concern - as the ONE declared
/ list: .qetl.log.ch refuses a name that is not in it, uqs's `logs --channel`
/ takes exactly these (generated into q_facts.LOG_CHANNELS), and the docs
/ table is checked against it. A job's name or a window is a context FIELD,
/ never a channel of its own.
/   lifecycle  process and job start and stop, run and window completion
/   ipc        connections, requests, responses, timeouts, retries
/   source     source readiness, fetching, queries
/   transform  normalisation, matching, calculations, rejected rows
/   storage    writes, persistence, sorting, compression
/   coverage   coverage updates, checkpoints, replay decisions
/   eod        rollover, handover, reload coordination
/   config     configuration, file loading, declarations
.qetl.log.channels:`lifecycle`ipc`source`transform`storage`coverage`eod`config

/ The levels off unless a process switches them on, each with its own switch.
.qetl.log.quiet:`TRACE`DEBUG

/ How TorQ routes the levels it already knows, for the names this file uses
/ instead: (TorQ's name; this file's). outmap and pubmap are keyed by level,
/ so a level they do not hold is neither printed nor published - INFO
/ unregistered would silence every line.
.qetl.log.torq_names:`INF`WARN`ERR!`INFO`WARNING`ERROR

/ Register this file's levels with TorQ's routing tables if TorQ is loaded
/ and has not heard of them: INFO, WARNING and ERROR routed as TorQ routes
/ INF, WARN and ERR; TRACE and DEBUG off (0), so nothing changes for a
/ process until it opts in. Idempotent, and called by the first line logged.
.qetl.log.register:{[]
    if[not .qetl.log.torq_loaded[]; :0b];
    {[torq;ours]
        if[not ours in key .lg.outmap; .lg.outmap[ours]:0^.lg.outmap torq];
        if[not ours in key .lg.pubmap; .lg.pubmap[ours]:0^.lg.pubmap torq]}'[key .qetl.log.torq_names;value .qetl.log.torq_names];
    {if[not x in key .lg.outmap; .lg.outmap[x]:0];
     if[not x in key .lg.pubmap; .lg.pubmap[x]:0]} each .qetl.log.quiet;
    1b}

/ Switch debug output on (or off) for THIS process.
/ .
/ Per process, not global, because the case for debug is one misbehaving
/ worker - turning it on fleet-wide is how you lose the signal in the noise
/ you switched it on to find.
/ @param on 1b to emit DEBUG lines, 0b to suppress them
.qetl.log.debug:{[on]
    .qetl.log.register[];
    if[.qetl.log.torq_loaded[]; .lg.outmap[`DEBUG]:$[on;1;0]];
    .qetl.log.debug_enabled::on;
    on}

.qetl.log.debug_enabled:0b

/ Switch trace output - every query a source sends - on (or off) for THIS
/ process. Independent of debug: either can be on without the other.
/ @param on 1b to emit TRACE lines, 0b to suppress them
.qetl.log.trace:{[on]
    .qetl.log.register[];
    if[.qetl.log.torq_loaded[]; .lg.outmap[`TRACE]:$[on;1;0]];
    .qetl.log.trace_enabled::on;
    on}

.qetl.log.trace_enabled:0b

/ Private: is a quiet level switched on, outside TorQ?
/ @private
.qetl.log.switched:{[level] $[level=`DEBUG; .qetl.log.debug_enabled; level=`TRACE; .qetl.log.trace_enabled; 1b]}

/ Private: render a field dict as space-separated k=v, values via .Q.s1 so a
/ symbol, a timestamp and a string all render unambiguously and a list does
/ not spread across the line. Field ORDER is preserved, so a worker that
/ always logs (worker;window;rows) produces columns a human can scan.
/ @private
.qetl.log.render:{[fields]
    if[0=count fields; :""];
    " " sv {[k;v] string[k],"=",.qetl.log.value1[v]}'[key fields;value fields]}

/ Private: one field's value as q text, IN FULL.
/ .
/ Not bare .Q.s1: on KDB-X it stops at the console width (\c, 80 columns by
/ default) and ends the text with "..", so a traced SQL statement or query
/ lambda was logged cut off at 79 characters - the one place the whole
/ query was meant to be visible. A string is escaped here, with no limit;
/ anything else is rendered with the console widened to its 2000-column
/ maximum and floats at 15 significant digits (\P 15, #2623; .qf.util.full
/ says why 15) for the one call, both put back even if rendering throws.
/ @param v any value
/ @return its q literal, as .Q.s1 spells it
/ @private
.qetl.log.value1:{[v]
    if[10h=type v; :.qetl.log.quoted[v]];
    c:@[system;"c";{[e] ()}];
    p:@[system;"P";{[e] ()}];
    if[(2<>count c) and 1<>count p; :.Q.s1 v];
    if[2=count c; @[system;"c ",string[c 0]," 2000";::]];
    if[1=count p; @[system;"P 15";::]];
    r:@[.Q.s1;v;{[e] "'",e}];
    if[2=count c; @[system;"c "," " sv string c;::]];
    if[1=count p; @[system;"P ",string first p;::]];
    r}

/ Private: a string as a q string literal - quoted, with \ " newline,
/ carriage return and tab escaped as q writes them, and every other byte
/ outside printable ASCII as a three-digit octal escape - exactly as -3!
/ spells it, without its console-width cut.
/ @param s a string
/ @return the literal, e.g. "\"a\\nb\""
/ @private
.qetl.log.quoted:{[s]
    esc:{[ch] i:`int$ch;
        $[ch in "\\\""; "\\",ch;
          ch="\n"; "\\n";
          ch="\r"; "\\r";
          ch="\t"; "\\t";
          (i<32) or i>126; "\\",raze string 8 8 8 vs i;
          enlist ch]};
    / -3! keeps a one-character string's leading "," - without it the
    / literal reads as a char atom (#2626).
    $[1=count s; ",";""],"\"",(raze esc each s),"\""}

/ Private: is TorQ's logging loaded?
/ .
/ `l in key `.lg, NOT `lg in key `. - the latter is always false, because
/ key `. lists the ROOT namespace's own names and namespaces are not among
/ them. The first draft used it, so this layer took the stdout fallback in
/ every process including ones with TorQ fully loaded: right level gating,
/ wrong transport, and no error to say so. Caught only by running the layer
/ against TorQ's real .lg definitions and noticing the line had three
/ fields where TorQ's format has six.
/ @private
.qetl.log.torq_loaded:{[] @[{`l in key x};`.lg;{0b}]}

/ The name a record gives as the process that wrote it when TorQ gives none:
/ the runner script's file name without `.q` (run_backfill.q -> `run_backfill),
/ unless the runner called .qetl.log.set_process. Never a worker's or a
/ source's name - that is what a field is for.
.qetl.log.standalone_name:`

/ Name this process in the records it writes outside TorQ.
/ @param name the runner's name, as a symbol
/ @return name
/ @eg .qetl.log.set_process`attached_backfill
.qetl.log.set_process:{[name] .qetl.log.standalone_name::name; name}

/ The process a record is attributed to: TorQ's procname when there is one
/ (.qetl.run.proc_name, the one read of it in src/), else the runner's name.
/ @return a symbol
/ @eg .qetl.log.process[]
.qetl.log.process:{[]
    n:@[.qetl.run.proc_name;::;{[e] `}];
    if[not null n; :n];
    if[not null .qetl.log.standalone_name; :.qetl.log.standalone_name];
    f:string @[value;`.z.f;`];
    $[0=count f; `standalone; `$first "." vs last "/" vs f]}

/ Private: the transport. TorQ's .lg.l when loaded, stdout otherwise.
/ .
/ `id` is the channel: TorQ's `id` column. `proc` is the emitting process, never the id.
/ Level gating outside TorQ mirrors TorQ's own default: DEBUG is suppressed
/ unless debug[] was called, TRACE unless trace[] was, everything else prints.
/ That way a test that asserts "this DEBUG line was not emitted" gets the same
/ answer whether or not torq.q happens to be loaded. The stdout line is
/ time|level|id|message and names no process: outside TorQ a log is one
/ process's own file (the runner's stdout), so the file says whose it is.
/ @private
.qetl.log.emit:{[level;id;msg]
    $[.qetl.log.torq_loaded[];
        .lg.l[level;`etl;.qetl.log.process[];id;msg;()!()];
      not .qetl.log.switched level;
        ::;
      -1 "|" sv string[(.z.p;level;id)],enlist msg]}

/ Private: is this level going to be emitted at all?
/ .
/ Split out so `line` can check BEFORE rendering. Mirrors the transport's own
/ gating: TorQ's outmap when it is loaded, the debug switch when it is not.
/ @private
.qetl.log.enabled:{[level]
    if[.qetl.log.torq_loaded[]; if[not level in key .lg.outmap; .qetl.log.register[]]];
    $[.qetl.log.torq_loaded[];
        0<0^.lg.outmap level;
      .qetl.log.switched[level]]}

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
/ @private
.qetl.log.line:{[level;id;text;fields]
    if[.qetl.log.enabled[level];
        f:.qetl.log.with_scope[fields];
        .qetl.log.emit[level;id;$[0=count f; text; text," ",.qetl.log.render[f]]]];
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

.qetl.log.context:()!()

/ Private: a line's fields, then whatever context it does not set itself.
/ @private
.qetl.log.with_scope:{[fields]
    if[0=count .qetl.log.context; :fields];
    f:$[0=count fields; ()!(); fields];
    f,(key[.qetl.log.context] except key f)#.qetl.log.context}

/ Run f[args] with `ctx` added to the context, and restore the context
/ afterwards - on success or on error, which is rethrown.
/ @param ctx a dict of fields, e.g. `worker`source!(`w;`s)
/ @param f the function to run
/ @param args its arguments, as a list; enlist(::) for a niladic f
/ @return what f returns
/ @eg .qetl.log.with_context[enlist[`worker]!enlist `w;{x+1};enlist 1] -> 2
.qetl.log.with_context:{[ctx;f;args]
    / `outer`, not `prev`: prev is a q builtin, and assigning it throws
    / 'assign when the file loads.
    outer:.qetl.log.context;
    .qetl.log.context::outer,ctx;
    restore:{[outer;e] .qetl.log.context:outer; 'e}[outer];
    / A niladic f through @, not `.`: on KDB-X `.[f;enlist ::;handler]` throws
    / an UNCATCHABLE 'type - the handler never runs, and the context leaked
    / into every line after it.
    / The generic null itself, not any 101h: a unary primitive (neg, til)
    / is 101h too, and passing one as the argument is a real call. Spell the
    / niladic case enlist(::) - `enlist ::` is a bare primitive, not a list.
    niladic:(args~(::)) or (1=count args) and (::)~first args;
    r:$[niladic; @[f;::;restore]; .[f;args;restore]];
    .qetl.log.context::outer;
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
.qetl.log.request_seq:0
.qetl.log.next_request:{[] .qetl.log.request_seq::.qetl.log.request_seq+1; .qetl.log.request_seq}

/ ------------------------------------------------------------ CHANNELS
/ .
/ The five levels, each taking (channel; text; fields). The channel must be one of .qetl.log.channels and
/ is refused otherwise - at the call site, so a test that reaches the line
/ finds a misspelling. The worker, source or window the line is about is a
/ FIELD (or the scoped context), not an argument.

/ Private: refuse a channel that is not declared.
/ @param channel the channel, as a symbol
/ @return channel
/ @throws a symbol naming the channel and the declared list
/ @private
.qetl.log.require_channel:{[channel]
    if[not channel in .qetl.log.channels;
        '"unknown log channel ",.Q.s1[channel],": one of ",", " sv string .qetl.log.channels];
    channel}

/ err records an error. It does NOT throw and does NOT exit - TorQ's .lg.e does
/ both depending on .proc state, right for an init failure and wrong for a
/ worker recording that one window failed and moving on. A worker that wants
/ to abort throws itself; this only records.
/ @param channel one of .qetl.log.channels
/ @param text a short fixed message - what happened, not the values
/ @param fields a dict of the values, rendered k=v after the text
/ @throws if channel is not declared
/ @eg .qetl.log.ch.info[`storage;"partition swapped";enlist[`rows]!enlist 3]
.qetl.log.ch.trc:{[channel;text;fields]  .qetl.log.line[`TRACE;.qetl.log.require_channel channel;text;fields]}
.qetl.log.ch.dbg:{[channel;text;fields]  .qetl.log.line[`DEBUG;.qetl.log.require_channel channel;text;fields]}
.qetl.log.ch.info:{[channel;text;fields] .qetl.log.line[`INFO;.qetl.log.require_channel channel;text;fields]}
.qetl.log.ch.warn:{[channel;text;fields] .qetl.log.line[`WARNING;.qetl.log.require_channel channel;text;fields]}
.qetl.log.ch.err:{[channel;text;fields]  .qetl.log.line[`ERROR;.qetl.log.require_channel channel;text;fields]}

\d .
