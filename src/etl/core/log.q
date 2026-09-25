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
/ THE TWO THINGS TORQ LACKS
/ .
/   1. A DEBUG level. .lg.outmap knows ERROR/ERR/INF/WARN only, and
/      `debug_level`, `verbose` and `log_verbose` in the requirements imply
/      a fourth. Without one, diagnostic detail is either always on (and a
/      million-row backfill floods its log with per-window lines) or never
/      on (and a stuck worker gives you nothing). DBG is registered in
/      outmap at 0 - off - and switched on per process with .qetl.log.debug[].
/ .
/   2. Structure. A worker logging "window done" is useless in aggregate;
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

/ Level names, in severity order. DBG is this file's addition; the other
/ three are TorQ's own, kept identical so outmap and pubmap apply unchanged.
levels:`DBG`INF`WARN`ERR

/ Register DBG with TorQ's routing tables if TorQ is loaded and has not
/ heard of it. Off by default (0): nothing changes for an existing process
/ until it opts in. Idempotent, so it is safe to call from every worker's
/ init rather than exactly once.
register:{[]
    if[not torq_loaded[]; :0b];
    if[not `DBG in key .lg.outmap; .lg.outmap[`DBG]:0];
    if[not `DBG in key .lg.pubmap; .lg.pubmap[`DBG]:0];
    1b}

/ Switch debug output on (or off) for THIS process.
/ .
/ Per process, not global, because the case for debug is one misbehaving
/ worker - turning it on fleet-wide is how you lose the signal in the noise
/ you switched it on to find.
/ @param on 1b to emit DBG lines, 0b to suppress them
debug:{[on]
    register[];
    if[torq_loaded[]; .lg.outmap[`DBG]:$[on;1;0]];
    debug_enabled::on;
    on}

debug_enabled:0b

/ Private: render a field dict as space-separated k=v, values via .Q.s1 so a
/ symbol, a timestamp and a string all render unambiguously and a list does
/ not spread across the line. Field ORDER is preserved, so a worker that
/ always logs (worker;window;rows) produces columns a human can scan.
render:{[fields]
    if[0=count fields; :""];
    " " sv {[k;v] string[k],"=",.Q.s1 v}'[key fields;value fields]}

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
/ Level gating outside TorQ mirrors TorQ's own default: DBG is suppressed
/ unless debug[] was called, everything else prints. That way a test that
/ asserts "this DBG line was not emitted" gets the same answer whether or
/ not torq.q happens to be loaded.
emit:{[level;id;msg]
    $[torq_loaded[];
        .lg.l[level;`etl;id;id;msg;()!()];
      (level=`DBG) and not debug_enabled;
        ::;
      -1 "|" sv string[(.z.p;level;id)],enlist msg]}

/ Private: is this level going to be emitted at all?
/ .
/ Split out so `line` can check BEFORE rendering. Mirrors the transport's own
/ gating: TorQ's outmap when it is loaded, the debug switch when it is not.
enabled:{[level]
    $[torq_loaded[];
        0<0^.lg.outmap level;
      level=`DBG;
        debug_enabled;
      1b]}

/ Private: assemble and emit one line.
/ .
/ The suppression check comes FIRST, before the fields are rendered (bank
/ the logging question). Rendering a message nobody will read is pure waste, and
/ the waste is concentrated exactly where it hurts: DBG is off by default and
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
        emit[level;id;$[0=count fields; text; text," ",render fields]]];
    }

/ The four levels. `id` is the worker or component name - it becomes
/ TorQ's `id` column, so `select from logmsg where id=`demo_deals_backfill`
/ works on a published log.
/ @param id the worker or component, as a symbol
/ @param text a short fixed message - what happened, not the values
/ @param fields a dict of the values, rendered k=v after the text
/ @eg .qetl.log.info[`demo_deals_backfill;"window published";
/        `range_from`range_to`rows!(2026.09.11D00:00;2026.09.12D00:00;1234)]
dbg:{[id;text;fields]  line[`DBG;id;text;fields]}
info:{[id;text;fields] line[`INF;id;text;fields]}
warn:{[id;text;fields] line[`WARN;id;text;fields]}

/ Log an error. Does NOT throw and does NOT exit - TorQ's .lg.e does both
/ depending on .proc state, which is right for an init failure and wrong
/ for a worker recording that one window failed and moving on. A
/ worker that wants to abort throws itself; this only records.
err:{[id;text;fields]  line[`ERR;id;text;fields]}

\d .
