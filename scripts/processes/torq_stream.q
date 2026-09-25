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
/ column. `-job` on the command line overrides it for a manual run.
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

/ The job this process runs: the one `-job` names, otherwise whichever job
/ claims this process's own name - which is how the stack starts every one.
/ `-job` is for running a job by hand, and is the flag run_stream.q takes for
/ the same thing. It replaced a UQF_STREAM_JOB environment variable, which
/ outlived the run it was exported for.
/ @return the job name as a symbol
/ @throws error when -job names nothing registered, or no job claims this process
which_job:{[]
    opts:.Q.opt .z.x;
    raw:$[`job in key opts; first opts`job; ""];
    if[count raw;
        job:`$raw;
        if[not job in .qstream.defined[];
            '"torq_stream: -job names ",raw,", which is not a registered streaming job - registered: ",", " sv string .qstream.defined[]];
        .qlog.info[`qproc;"job chosen by -job";enlist[`job]!enlist job];
        :job];
    if[()~key `.proc;
        '"torq_stream: not running under TorQ and no -job given - one of the two has to say which job this is"];
    job:.qstream.for_procname .proc.procname;
    .qlog.info[`qproc;"job chosen by procname";`procname`job!(.proc.procname;job)];
    job}

/ The root `upd` the tickerplant calls, around the job's own on_batch.
/ .
/ Counts what arrives (.qpipe.record_received: the first batch per table at
/ INF, every one at DBG) and logs an on_batch error with its table before
/ re-raising it, so the error still reaches the caller exactly as before -
/ and is now also in this process's own log, which is where someone asking
/ "why is my output empty" looks.
/ @param t the table the batch is for
/ @param x the batch
upd:{[t;x]
    .qpipe.record_received[t;x];
    .[.qproc.stream.on_batch;(t;x);{[t;e]
        .qlog.err[.qproc.stream.job;"on_batch failed";`table`error!(t;e)];
        'e}[t]]}

/ Subscribe, wire the job's publish seam to the tickerplant, install the
/ root `upd` the tickerplant calls, and start the job's timer if it has one.
/ .
/ `upd` and the publish handle are at ROOT by necessity - the tickerplant
/ calls `upd` by name there (scripts/processes/torq_pipeline.q, invariant 5) - and
/ everything else stays inside the job's own namespace.
/ @param job the job's name
/ @return the job name
run:{[job]
    decl:.qstream.def job;
    `.qproc.stream.job set job;
    / Before anything that can block, so a process stuck waiting for the
    / tickerplant has already said what it was about to do.
    .qlog.info[job;"starting streaming job";
        `subscribe_to`publishes`timer`on_batch!(decl`subscribe_to;decl`publishes;
            $[`period in key decl; decl`period; 0Nn];`on_batch in key decl)];
    / A feed subscribes to nothing: it takes a publish handle and nothing
    / else. Asking subscribe_etl for one would make it wait for a
    / subscription it never wanted, and then subscribe to an empty list.
    h:$[count decl`subscribe_to; .qpipe.subscribe_etl[job;decl`subscribe_to]; .qpipe.feed_handle[]];
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
    if[`on_batch in key decl;
        `.qproc.stream.on_batch set decl`on_batch;
        `upd set .qproc.stream.upd];
    if[`period in key decl;
        `.qproc.stream.tick set decl`on_timer;
        .qpipe.safe_timer[job;decl`period;`.qproc.stream.tick;
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
    / declared in the job's .qstream.define like any other output and
    / verify_pipeline_edges needs no exemption.
    if[count .qcfgaudit.watching job;
        `.qcfgaudit.owner_here set job;
        .qpipe.safe_timer[`$(string job),"_config";.qcfgaudit.period;
            `.qcfgaudit.poll_and_publish;
            "Audit ",(string job)," configuration changes"]];
    .qlog.info[`qproc;"streaming job wired - running";
        `job`subscribe_to`publishes!(job;decl`subscribe_to;decl`publishes)];
    job}

\d .

/ Every plant subscriber needs these at ROOT - see .qpipe's own header.
.qpipe.install_period_handlers[];

.qproc.stream.run .qproc.stream.which_job[];
