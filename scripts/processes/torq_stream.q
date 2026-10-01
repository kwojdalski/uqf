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
/ This file is the ONLY place a streaming job meets TorQ: .qtorq for the
/ subscription, the publish handle and the timer. The job files know nothing
/ about any of it - they call `publish` in their own namespace, which this
/ file wires. That is what lets src/etl/init.q load them in a plain q
/ process, which is what keeps them under test and in docs/man.q.

/ SOURCE: pull in uqf's own src/init.q and the ETL tree, which is where the
/ job declarations live.
.qtorq.load_uqf[];

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
        if[not job in .qetl.job.stream.defined[];
            '"torq_stream: -job names ",raw,", which is not a registered streaming job - registered: ",", " sv string .qetl.job.stream.defined[]];
        .qetl.log.info[`qproc;"job chosen by -job";enlist[`job]!enlist job];
        :job];
    if[()~key `.proc;
        '"torq_stream: not running under TorQ and no -job given - one of the two has to say which job this is"];
    job:.qetl.job.stream.for_procname .proc.procname;
    .qetl.log.info[`qproc;"job chosen by procname";`procname`job!(.proc.procname;job)];
    job}

/ The TorQ transport for .qetl.job.stream.start: the tickerplant through
/ .qtorq, TorQ's own timers, and the plant's table check.
/ .
/ A job that subscribes to nothing (a feed) takes a publish handle and
/ nothing else: asking connect_etl for a subscriber's setup would register
/ it for a subscription it never wanted.
/ @param job the job's name
/ @return the transport dictionary
transport:{[job]
    feed:0=count .qetl.job.stream.subscriptions job;
    `connect`publisher`check_publishable`subscribe`timer!(
        {[job;feed] `.qproc.stream.h set $[feed; .qtorq.feed_handle[]; .qtorq.connect_etl job];}[job;feed];
        {[] .qtorq.publish[.qproc.stream.h;;]};
        {[tbls] .qtorq.assert_publishable[.qproc.stream.h;tbls];};
        {[job;tbls;handler;replay]
            / The root `upd` the tickerplant calls, counting what arrives
            / (.qtorq.record_received) before the job's guarded handler.
            / Installed BEFORE subscribing: TorQ's replay calls whatever
            / `upd` is in place when the subscription is made.
            / .
            / A REPLAYED batch is not shaped like a live one. Live, the plant
            / sends a table; its log holds what .u.upd received - a list of
            / columns, `time` first - and that is what a replay delivers
            / (measured on stp1's own log). A job written against tables then
            / threw 'type on the first replayed batch, and TorQ abandoned the
            / rest of the file. The plant's column names, asked once here,
            / turn a list of columns back into the table the job expects.
            names:tbls!{[h;t] h({cols x};t)}[.qproc.stream.h] each tbls;
            `upd set {[names;h;t;x]
                x:$[0h=type x; flip (names t)!x; x];
                .qtorq.record_received[t;x];
                h[t;x]}[names;handler];
            .qtorq.subscribe_tables[job;tbls;replay];}[job];
        {[name;period;f]
            / .qtorq.safe_timer schedules a function by NAME, so each one
            / gets a global of its own under .qproc.stream.timers.
            fn:` sv `.qproc.stream.timers,name;
            fn set f;
            .qtorq.safe_timer[name;period;fn;"Run ",(string name)," for its streaming job"];})}

\d .

/ Every plant subscriber needs these at ROOT - see .qtorq's own header.
.qtorq.install_period_handlers[];

{[job] .qetl.job.stream.start[job;.qproc.stream.transport job]} .qproc.stream.which_job[];
