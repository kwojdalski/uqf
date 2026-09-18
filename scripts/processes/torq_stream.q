/ torq_stream.q - run one streaming job as a discoverable TorQ process
/ (.qproc.stream).
/ .
/ WHAT THIS REPLACES. There were four of these: torq_cross_etl.q,
/ torq_markout_etl.q, torq_posbook_etl.q and torq_vectorize_etl.q, each
/ carrying the same twenty lines of subscribe/upd/timer wiring around one
/ job's computation, and each keeping that computation in a file of its own
/ (src/etl/transforms/stream.q) so it could be tested. A job is now one file
/ under src/etl/streaming/ holding every step, and this file runs any of
/ them - the same shape torq_backfill.q has for bounded workers, where the
/ worker is named by the environment and the process is generic.
/ .
/ WHICH job this process runs comes from the name it was started under:
/ every job declares the procname that runs it, and TorQ sets .proc.procname
/ before this file loads, so process.csv needs nothing beyond the load
/ column. UQF_STREAM_JOB overrides it for a manual run outside TorQ.
/ .
/ This file is the ONLY place a streaming job meets TorQ: .qpipe for the
/ subscription, the publish handle and the timer. The job files know nothing
/ about any of it - they call `publish` in their own namespace, which this
/ file wires. That is what lets src/etl/init.q load them in a plain q
/ process, which is what keeps them under test and in docs/man.q (B-09).

/ SOURCE: pull in uqf's own src/init.q and the ETL tree, which is where the
/ job declarations live.
.qpipe.load_uqf[];

\d .qproc.stream

/ The job this process runs: UQF_STREAM_JOB when set, otherwise whichever
/ job claims this process's own name.
/ @return the job name as a symbol
/ @throws error when the override names nothing, or no job claims this process
which_job:{[]
    raw:getenv `UQF_STREAM_JOB;
    if[count raw;
        job:`$raw;
        if[not job in .qstream.registered[];
            '"torq_stream: UQF_STREAM_JOB names ",raw,", which is not a registered streaming job - registered: ",", " sv string .qstream.registered[]];
        :job];
    if[()~key `.proc;
        '"torq_stream: not running under TorQ and UQF_STREAM_JOB is unset - one of the two has to say which job this is"];
    .qstream.for_procname .proc.procname}

/ Subscribe, wire the job's publish seam to the tickerplant, install the
/ root `upd` the tickerplant calls, and start the job's timer if it has one.
/ .
/ `upd` and the publish handle are at ROOT by necessity - the tickerplant
/ calls `upd` by name there (scripts/processes/torq_pipeline.q, invariant 5) - and
/ everything else stays inside the job's own namespace.
/ @param job the job's name
/ @return the job name
run:{[job]
    decl:.qstream.declaration job;
    / A feed subscribes to nothing: it takes a publish handle and nothing
    / else. Asking subscribe_etl for one would make it wait for a
    / subscription it never wanted, and then subscribe to an empty list.
    h:$[count decl`subscribes; .qpipe.subscribe_etl[job;decl`subscribes]; .qpipe.feed_handle[]];
    / A job that publishes nothing keeps its unwired stub, so a later edit
    / that starts publishing without declaring it fails loudly instead of
    / sending rows nowhere.
    if[count decl`publishes; .qstream.wire[job;.qpipe.publish[h;;]]];
    if[`on_batch in key decl; `upd set decl`on_batch];
    if[`timer_period in key decl;
        `.qproc.stream.tick set decl`on_timer;
        .qpipe.safe_timer[job;decl`timer_period;`.qproc.stream.tick;
            "Run the ",(string job)," streaming job"]];
    .lg.o[`qproc;"streaming job ",(string job),
        $[count decl`subscribes; " subscribed to ",", " sv string decl`subscribes; " producing"],
        $[count decl`publishes; ", publishing ",", " sv string decl`publishes; ", publishing nothing"]];
    job}

\d .

.qproc.stream.run .qproc.stream.which_job[];
