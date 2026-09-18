/ run_stream.q - run any registered streaming job on stock kdb+, with no
/ TorQ (.qproc.standalone).
/ .
/ The sibling of torq_stream.q, which does the same work against TorQ. The
/ two exist because a streaming job's code has never needed TorQ - it
/ calls `publish` in its own namespace and something else wires it - but
/ until this file the only thing that ever wired it was TorQ, so "the jobs
/ are TorQ-free" was a claim about the source tree rather than about
/ running one. This file makes it true of the service.
/ .
/ THREE ROLES, chosen by flags, because a tickerplant and a job are
/ different processes in production and the same one in a demo:
/ .
/   q scripts/processes/run_stream.q -plant 5010
/       a bare tickerplant on port 5010, publishing nothing itself.
/ .
/   q scripts/processes/run_stream.q -job fx_orders_feed -tp 5010
/       run the feed, publishing into the plant at 5010.
/ .
/   q scripts/processes/run_stream.q -job fx_positions -tp 5010 -port 5011
/       run the service, subscribing to the plant at 5010 and listening on
/       5011 so a client can query its book.
/ .
/   q scripts/processes/run_stream.q -job fx_positions -feed fx_orders_feed
/       BOTH, plus the plant, in one process. No ports, no sockets, no
/       second terminal - which is what makes the whole service testable
/       in one q session and is how tests/q/test_fx_positions.q runs it.
/ .
/ RECOVERY. The plant logs every message it carries, and a job started
/ against an existing log replays it before subscribing, so a restart
/ mid-session rebuilds the book it had rather than starting flat. That is
/ the half of a tickerplant that matters most and the half a demo usually
/ skips.
/ .
/ This file may know about .qtick, sockets and the clock. The job files may
/ not, and do not.

\l src/init.q
\l src/etl/init.q
\l scripts/processes/uqf_stack_tables.q

\d .qproc.standalone

/ The parsed command line: -job, -tp, -port, -plant, -feed, -logdir.
opts:.Q.opt .z.x

/ Private: one option's value, or a default. .Q.opt gives a list per key,
/ so a flag given once is a one-element list and a flag given twice is
/ two - taking `first` quietly accepts the second spelling, which is how
/ a typo'd duplicate becomes a silently ignored setting.
opt:{[nm;dflt]
    if[not nm in key opts; :dflt];
    v:opts nm;
    if[1<count v;
        '"run_stream: -",string[nm]," was given ",string[count v]," times - which one binds would depend on argument order"];
    first v}

/ Every table name declared in scripts/processes/uqf_stack_tables.q.
/ .
/ The plant learns its schemas from the SAME file the TorQ stack's
/ tickerplant does, so a job publishes identical rows either way. A plant
/ that invented its own schemas would be a second declaration of every
/ table, and the two would drift.
stack_tables:{[] (tables `) where {[t] `time = first cols get t} each tables `}

/ Declare every stack table to the plant.
/ @return the table names declared
declare_schemas:{[]
    t:stack_tables[];
    .qtick.schema'[t;get each t];
    t}

/ Start a tickerplant in this process: schemas, a log for today, and a
/ port to listen on when one was asked for.
/ .
/ The log name is the plant's port, or `local` when it has none, so two
/ plants on one machine cannot write into each other's log.
/ @param port the port to listen on, or 0N for an in-process plant
/ @return the number of messages already in today's log
start_plant:{[port]
    declare_schemas[];
    existing:.qtick.open_log[opt[`logdir;"tplog"];`$"uqf",$[null port;"local";string port];.z.D];
    if[not null port; system "p ",string port];
    / A subscriber that drops must stop receiving, or every publish throws
    / on a dead handle and takes the plant down with it.
    `.z.pc set {[h] .qtick.unsubscribe neg h;};
    existing}

/ Rebuild a job's state from the plant's log before it sees live traffic.
/ .
/ WHY BEFORE SUBSCRIBING, and not after. A job that subscribes first would
/ take live batches while replaying historical ones, and apply them out of
/ order - so its book would be right only if nothing traded during
/ recovery. Subscribing afterwards means the plant's log and the live
/ stream meet exactly once, at the message replay stopped on.
/ @param job the job's name
/ @return the number of messages replayed
recover:{[job]
    decl:.qstream.declaration job;
    if[0=count decl`subscribes; :0];
    if[not `on_batch in key decl; :0];
    wanted:decl`subscribes;
    / Replay only the tables this job subscribes to. The log carries every
    / table the plant ever saw, and handing a job a batch it never asked
    / for is a bug the live path cannot produce.
    handler:{[wanted;h;t;r] if[t in wanted; h[t;r]];}[wanted;decl`on_batch];
    .qtick.replay[.qtick.log_path;handler]}

/ Wire a job's publish seam and start its timer.
/ @param job the job's name
/ @param sink where its output goes - the local plant, or a handle to a remote one
/ @return the job name
start_job:{[job;sink]
    decl:.qstream.declaration job;
    if[count decl`publishes; .qstream.wire[job;sink]];
    if[`timer_period in key decl;
        `.qproc.standalone.timers set .qproc.standalone.timers,enlist (job;decl`on_timer;decl`timer_period;0Np)];
    job}

/ The timers this process runs: (job; body; period; last fired).
/ .
/ ONE q timer driving many jobs, rather than one timer each - q has a
/ single \t, so a process running a feed and a service has to multiplex
/ them itself. Each job's period is honoured independently.
timers:();

/ Fire whichever timers are due. Installed as .z.ts by start.
/ .
/ Each body is trapped: a job that throws on one tick must not stop every
/ other job's timer, and must not stop its own next tick either - a
/ service that goes quiet after one bad batch is worse than one that
/ complains every five seconds.
tick:{[]
    now:.z.p;
    fire:{[now;i]
        row:.qproc.standalone.timers i;
        if[not (null row 3) or now>=(row 3)+row 2; :()];
        .qproc.standalone.timers[i;3]:now;
        @[row 1;::;{[job;e] -2 "timer ",string[job]," failed: ",e;}[row 0]];
        }[now];
    fire each til count timers;
    }

/ Subscribe a job to a plant and hand back the sink it should publish to.
/ @param job the job's name
/ @param tp the plant's port, or 0N for the plant in this process
/ @return the sink
connect:{[job;tp]
    decl:.qstream.declaration job;
    if[null tp;
        / `1_m`, dropping the `upd` the message leads with - NOT `1 2#m`,
        / which is a RESHAPE: it yields a one-element list, so `.` applies
        / on_batch to a single argument, which makes a projection rather
        / than an error. The job then receives nothing and reports healthy.
        if[count decl`subscribes;
            .qtick.subscribe[decl`subscribes;
                {[handler;m] handler . 1_m}[decl`on_batch]]];
        :{[t;r] .qtick.publish[t;r]}];
    h:hopen tp;
    if[count decl`subscribes;
        h(`.qtick.subscribe;decl`subscribes;`;::)];
    neg h}

/ Start everything this process was asked to run.
/ @return the jobs started
start:{[]
    plant_port:"J"$opt[`plant;""];
    job:`$opt[`job;""];
    feed:`$opt[`feed;""];
    tp:"J"$opt[`tp;""];
    listen:"J"$opt[`port;""];
    if[(null job) and null plant_port;
        '"run_stream: give -job <name>, or -plant <port> to run a bare tickerplant"];
    / A process with no -tp carries its own plant, so the bare-plant role
    / and the everything-in-one-process role are one code path.
    local:null tp;
    if[local; start_plant plant_port];
    if[not null listen; system "p ",string listen];
    started:();
    if[not null job;
        replayed:$[local; recover job; 0];
        if[0<replayed;
            -1 "run_stream: recovered ",string[replayed]," message(s) from ",string .qtick.log_path];
        started,:start_job[job;connect[job;tp]]];
    if[not null feed; started,:start_job[feed;connect[feed;tp]]];
    if[count timers;
        `.z.ts set {[] .qproc.standalone.tick[]};
        / A fixed 250ms wakeup that each job's own period is checked
        / against, rather than \t set to some job's period: q has one
        / timer, and setting it to the shortest period would fire every
        / other job late by up to that much.
        system "t 250"];
    -1 "run_stream: ",$[count started; ", " sv string started; "tickerplant only"],
        $[local; " (plant in this process)"; " (plant at ",string[tp],")"];
    started}

\d .

/ Start only when the command line actually asked for something. Loading
/ this file with no arguments - which is what tests/q/test_fx_positions.q
/ does - defines every function above and starts nothing, so the runner's
/ own logic is testable without a port, a log or a timer.
if[count `job`plant inter key .qproc.standalone.opts; .qproc.standalone.start[]];
