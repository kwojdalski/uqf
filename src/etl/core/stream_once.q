/ stream_once.q - --once for a streaming job: poll, timer or subscriber (.qetl.job.stream.once, #2798).
/ .
/ The runner (scripts/processes/torq_stream.q) starts a job with .qetl.job.stream.start, which
/ connects, wires publish, subscribes and schedules the job's timers. A one-unit run is that
/ SAME start, with two things changed on the transport and nothing in the job:
/ .
/   timers   are captured instead of scheduled, so nothing fires on a clock
/   publish  is counted, so the outcome can say how many rows the unit published
/ .
/ and then the unit for the kind:
/ .
/   poll        the captured poll timer is called once: .qetl.job.stream.tick, the whole
/               fetch, normalize, check, publish, save-cursor sequence in its own order. A page
/               with nothing in it is `no_work (reason no_new_data); a page published is `done.
/   timer       the captured on_timer is called once. It is `done whether or not it published:
/               a cycle that found nothing to emit is a cycle that ran.
/   subscriber  the handler is wrapped so the FIRST live batch on a declared input, once the
/               job's own handler has returned, ends the run `done. A batch it throws on ends it
/               `failed. No batch within the timeout ends it `no_work, reason input_timeout.
/ .
/ WHAT IS NEVER DONE. A subscriber's own timers are not run, so no pending event-time state is
/ flushed to finish: what the job holds when the timeout ends it, it still holds, and the next
/ run's replay rebuilds it as any restart does. A batch that arrives during the replay is
/ replayed state, not input, and does not count. A batch for a table the job only reads to
/ restore itself (restore_from, a carry's snapshot) is not input either.
/ .
/ THE TIMEOUT is a wait for input that ended, and is reported as one: `no_work with reason
/ `input_timeout`, an INFO line. It is not a cancelled query - that is an error from the call
/ that was cancelled, and ends the unit `failed with the error as the reason.
/ .
/ Only this file ends a streaming job's --once run, and it does so through .qetl.once.conclude, so
/ the exit code, the log line and the outcome file are written in one place.

\d .

/ The timers the job asked for, name -> its function enlisted (as every registry here, #1030);
/ captured, never scheduled.
.qetl.job.stream.once.timers:(`symbol$())!()

/ The rows the job's publish carried to the transport during this run.
.qetl.job.stream.once.rows:0

/ What the run is, for the timeout and the batch wrapper: the context, the deadline, and whether
/ a unit has run. One run per process, so one global.
.qetl.job.stream.once.state:()!()

/ The timer a transport's `timer` is replaced with: keep the function, schedule nothing.
/ @param name the timer's name
/ @param period how often it would have fired
/ @param f the function it would have run
/ @return the empty list, as a scheduler returns
/ @private
.qetl.job.stream.once.capture:{[name;period;f] / noqa: QF016 a transport's timer takes (name; period; f)
    .qetl.job.stream.once.timers::.qetl.job.stream.once.timers,enlist[name]!enlist enlist f;
    ()}

/ A publisher that counts what it carries on to the transport.
/ @param p the transport's publisher, a function of (table; rows)
/ @param t the table
/ @param x the rows
/ @return what the publisher returned
/ @private
.qetl.job.stream.once.counted:{[p;t;x]
    r:p[t;x];
    .qetl.job.stream.once.rows+:.qetl.job.stream.row_count[t;x];
    r}

/ A subscriber's handler, wrapped so its first live input batch ends the run.
/ @param job the job's name
/ @param h the job's handler, a function of (table; rows)
/ @param t the table
/ @param x the rows
/ @return what the job's handler returned
/ @private
.qetl.job.stream.once.on_batch:{[job;h;t;x]
    s:.qetl.job.stream.once.state;
    r:.[{[h;t;x] (1b;h[t;x])};(h;t;x);{[e] (0b;e)}];
    counts:(not .qetl.job.stream.replaying) and (not s`seen) and t in (.qetl.job.stream.def job)`subscribe_to;
    if[not counts; if[not first r; 'last r]; :last r];
    .qetl.job.stream.once.state[`seen]:1b;
    $[first r;
        .qetl.once.conclude[s`ctx;`done;"";1;.qetl.job.stream.once.rows;string[t]," x ",string count x];
        .qetl.once.conclude[s`ctx;`failed;last r;0;.qetl.job.stream.once.rows;string t]];
    $[first r; last r; ::]}

/ The subscriber's wait: ends the run `no_work once the timeout has passed with no batch seen.
/ Run on a short timer rather than once at the deadline, because a scheduler's first call is
/ immediate and its period is only how often it looks.
/ @param job the job's name
/ @param unused the timer's argument
/ @return 1b when it ended the run
.qetl.job.stream.once.expire:{[job;unused]
    s:.qetl.job.stream.once.state;
    if[$[s`seen; 1b; .z.p<s`deadline]; :0b];
    .qetl.job.stream.once.state[`seen]:1b;
    .qetl.log.ch.info[`lifecycle;"once: no input arrived within the timeout - a wait that ended, not a cancelled query";
        `job`timeout!(job;s`timeout)];
    .qetl.once.conclude[s`ctx;`no_work;"input_timeout";0;.qetl.job.stream.once.rows;"no input within ",string s`timeout];
    1b}

/ The transport a one-unit run starts a job on: the real one's connect, subscribe and
/ publisher - with the timers captured, the publisher counted, and for a subscriber the handler
/ wrapped and the timeout armed.
/ @param job the job's name
/ @param tr the real transport, a dictionary of transport keys (and optional hooks)
/ @param kind the job's kind
/ @return the transport to start the job on
/ @private
.qetl.job.stream.once.transport:{[job;tr;kind]
    t:tr;
    t[`timer]:.qetl.job.stream.once.capture;
    pub:tr`publisher;
    t[`publisher]:{[pub;unused] .qetl.job.stream.once.counted[pub[]]}[pub];
    if[kind~`subscriber;
        sub:tr`subscribe;
        t[`subscribe]:{[sub;job;tbls;h;replay] sub[tbls;.qetl.job.stream.once.on_batch[job;h];replay]}[sub;job]];
    t}

/ The unit of a poll or timer job: call its one captured timer function.
/ @param job the job's name
/ @param kind `poll or `timer
/ @param ctx the run's context
/ @return the outcome document
/ @private
.qetl.job.stream.once.cycle:{[job;kind;ctx]
    if[not job in key .qetl.job.stream.once.timers;
        :.qetl.once.conclude[ctx;`failed;"the job declared no timer to run one cycle of";0;0;""]];
    r:@[{[f] (1b;f[::])};first .qetl.job.stream.once.timers job;{[e] (0b;e)}];
    if[not first r; :.qetl.once.conclude[ctx;`failed;last r;0;.qetl.job.stream.once.rows;""]];
    page:last r;
    / A poll's tick says whether it found a page; a timer's return is the job's own.
    if[(kind~`poll) and 99h=type page;
        if[(page`state)~`idle;
            :.qetl.once.conclude[ctx;`no_work;"no_new_data";0;0;"cursor ",.Q.s1 page`cursor]];
        :.qetl.once.conclude[ctx;`done;"";1;`long$page`rows;"cursor ",.Q.s1 page`cursor]];
    .qetl.once.conclude[ctx;`done;"";1;.qetl.job.stream.once.rows;""]}

/ Start a job on its transport and run ONE unit of it.
/ .
/ A subscriber returns once it is armed - the batch or the timeout ends it, from the event loop. A
/ poll or timer job ends here. Either way the exit is .qetl.once.exit's, through conclude.
/ @param job the job's name
/ @param tr the transport, a dictionary of transport keys
/ @param timeout how long a subscriber waits for its batch, a timespan
/ @return the outcome document for a poll or timer job, `waiting for a subscriber
/ @throws error when the job is not a streaming job
/ @eg .qetl.job.stream.once.run[`demo_deals_feed;tr;0D00:01:00]
.qetl.job.stream.once.run:{[job;tr;timeout]
    kind:.qetl.once.kind_of job;
    if[not kind in `poll`timer`subscriber; '"once: ",string[job]," is a ",string[kind]," job, which the stream runner does not run"];
    ctx:.qetl.once.context[.qetl.once.instance job;job;kind];
    `.qetl.job.stream.once.timers set (`symbol$())!();
    `.qetl.job.stream.once.rows set 0;
    `.qetl.job.stream.once.state set `ctx`seen`deadline`timeout!(ctx;0b;.z.p+timeout;timeout);
    .qetl.log.ch.info[`lifecycle;"once: running one unit";`job`kind`unit!(job;kind;.qetl.once.kinds[kind]`unit)];
    t:.qetl.job.stream.once.transport[job;tr;kind];
    r:.[{[job;t] .qetl.job.stream.start[job;t]; (1b;::)};(job;t);{[e] (0b;e)}];
    if[not first r; :.qetl.once.conclude[ctx;`failed;"start failed: ",last r;0;0;""]];
    if[kind~`subscriber;
        tr[`timer][`$string[job],"_once_wait";0D00:00:01;{[job;unused] .qetl.job.stream.once.expire[job;unused]}[job]];
        .qetl.log.ch.info[`lifecycle;"once: waiting for one input batch";`job`timeout!(job;timeout)];
        :`waiting];
    .qetl.job.stream.once.cycle[job;kind;ctx]}

\d .
