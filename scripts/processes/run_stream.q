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
/ This file may know about .qetl.tick, sockets and the clock. The job files may
/ not, and do not.

\l src/init.q
\l src/etl/init.q

\d .qproc.standalone

/ The parsed command line: -job, -tp, -port, -plant, -feed, -logdir.
opts:.Q.opt .z.x

/ -verbose switches DBG on, the same flag every uqf process script takes.
if[`verbose in key opts; .qetl.log.debug 1b];

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

/ Every plant table: this tree's and the vendored quote/trade/packets
/ (.qetl.plant, loaded by src/etl/init.q).
/ .
/ The plant learns its schemas from the SAME file the TorQ stack's
/ tickerplant does, so a job publishes identical rows either way. A plant
/ that invented its own schemas would be a second declaration of every
/ table, and the two would drift.
stack_tables:{[] .qetl.plant.names[]}

/ Declare every stack table to the plant.
/ @return the table names declared
declare_schemas:{[]
    t:stack_tables[];
    .qetl.tick.schema'[t;.qetl.plant.schema each t];
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
    existing:.qetl.tick.open_log[opt[`logdir;"tplog"];`$"uqf",$[null port;"local";string port];.z.D];
    if[not null port; system "p ",string port];
    .qetl.log.info[`run_stream;"plant started";
        `port`log`existing_messages!(port;.qetl.tick.log_path;existing)];
    / A subscriber that drops must stop receiving, or every publish throws
    / on a dead handle and takes the plant down with it.
    `.z.pc set {[h] .qetl.tick.unsubscribe neg h;};
    existing}

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
        @[row 1;::;{[job;e] .qetl.log.err[job;"timer function failed";enlist[`error]!enlist e];}[row 0]];
        }[now];
    fire each til count timers;
    }

/ Private: the timer half both transports share.
add_timer:{[name;period;f] `.qproc.standalone.timers set .qproc.standalone.timers,enlist (name;f;period;0Np);}

/ The transport for a plant IN this process: .qetl.tick directly.
/ .
/ A replay reads the plant's own log - only the tables asked for, since the
/ log carries every table the plant saw - BEFORE subscribing, so the log and
/ the live stream meet exactly once, at the message replay stopped on. A
/ job subscribing first would apply live batches among historical ones.
/ .
/ `1_m` in the subscription sink drops the `upd` each message leads with -
/ NOT `1 2#m`, which is a RESHAPE: a one-element list, so `.` would apply
/ the handler to one argument and make a projection rather than an error.
/ @return the transport dictionary
local_transport:{[]
    `connect`publisher`subscribe`timer!(
        {[] };
        {[] {[t;r] .qetl.tick.publish[t;r]}};
        {[tbls;handler;replay]
            if[replay;
                n:.qetl.tick.replay[.qetl.tick.log_path;{[tbls;h;t;r] if[t in tbls; h[t;r]];}[tbls;handler]];
                if[0<n; -1 "run_stream: recovered ",string[n]," message(s) from ",string .qetl.tick.log_path]];
            .qetl.tick.subscribe[tbls;{[h;m] h . 1_m}[handler]];};
        add_timer)}

/ The transport for a plant in ANOTHER process, on port `tp`.
/ .
/ The plant has to call this process back, so it needs a sink addressed at
/ it - which only the REMOTE can build, out of its own .z.w: send it a
/ lambda to apply. What comes back is (`upd;table;rows), evaluated here as
/ upd[table;rows], so the handler has to BE root upd.
/ .
/ Publishing is NOT a bare `neg h`. A handle is an integer, which
/ .qetl.job.stream.wire rejects (functions only), and `(neg h)[tbl;rows]`
/ would send a two-element message the remote evaluates as `tbl[rows]`. The
/ wrapper names the function to call over there.
/ .
/ No replay here: the log belongs to the other process. A job asking for one
/ is told so rather than silently started without it.
/ @param tp the plant's port
/ @return the transport dictionary
remote_transport:{[tp]
    `connect`publisher`subscribe`timer!(
        {[tp]
            .qetl.log.info[`run_stream;"connecting to the plant";enlist[`port]!enlist tp];
            `.qproc.standalone.h set @[hopen;tp;{[tp;e]
                '"run_stream: cannot connect to the plant on port ",string[tp]," (",e,") - is it running? start one with -plant ",string tp}[tp]];}[tp];
        {[] {[send;t;x] send(`.qetl.tick.publish;t;x)}[neg .qproc.standalone.h]};
        {[tbls;handler;replay]
            if[replay;
                .qetl.log.warn[`run_stream;"replay asked for, but the plant's log is in another process - starting without it";
                    enlist[`tables]!enlist tbls]];
            `upd set handler;
            .qproc.standalone.h({[want] .qetl.tick.subscribe[want;neg .z.w]};tbls);};
        add_timer)}

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
    tr:$[local; local_transport[]; remote_transport tp];
    if[not null job; started,:.qetl.job.stream.start[job;tr]];
    if[not null feed; started,:.qetl.job.stream.start[feed;tr]];
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
/ this file with no arguments defines every function above and starts
/ nothing, so the runner's own logic is testable without a port, a log or
/ a timer.
if[count `job`plant inter key .qproc.standalone.opts; .qproc.standalone.start[]];
