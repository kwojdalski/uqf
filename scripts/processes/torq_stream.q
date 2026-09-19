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
/ process, which is what keeps them under test and in docs/man.q.

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
    / .
    / The declared tables are checked against the plant BEFORE the seam is
    / wired: a table the tickerplant does not define swallows every row
    / without an error anywhere, which is how the whole FX positions
    / service published into nothing (#287). Refusing here costs one round
    / trip per process start and turns that into a startup failure naming
    / the table.
    if[count decl`publishes;
        .qpipe.assert_publishable[h;decl`publishes];
        .qstream.wire[job;.qpipe.publish[h;;]]];
    if[`on_batch in key decl; `upd set decl`on_batch];
    if[`timer_period in key decl;
        `.qproc.stream.tick set decl`on_timer;
        .qpipe.safe_timer[job;decl`timer_period;`.qproc.stream.tick;
            "Run the ",(string job)," streaming job"]];
    / A SECOND timer, when the job declares configuration worth auditing.
    / .
    / q has no hook on assignment, so the only way to notice that someone
    / set .qsub.x.notional over IPC is to look and compare (#295). It runs
    / here rather than in a central process because config lives in each
    / process's own memory: a poller elsewhere would need a handle per
    / process - and connections are the scarce resource (#285) - and could
    / only see what it thought to ask for. In-process costs nothing and
    / catches a change whatever caused it.
    / .
    / It publishes through the job's OWN publish seam, so the table is
    / declared in the job's .qstream.register like any other output and
    / verify_pipeline_edges needs no exemption.
    if[count .qcfgaudit.watching job;
        `.qproc.stream.audit_config set .qcfgaudit.publisher job;
        .qpipe.safe_timer[`$(string job),"_config";.qcfgaudit.period;
            `.qproc.stream.audit_config;
            "Audit ",(string job)," configuration changes"]];
    .lg.o[`qproc;"streaming job ",(string job),
        $[count decl`subscribes; " subscribed to ",", " sv string decl`subscribes; " producing"],
        $[count decl`publishes; ", publishing ",", " sv string decl`publishes; ", publishing nothing"]];
    job}

\d .

.qproc.stream.run .qproc.stream.which_job[];
