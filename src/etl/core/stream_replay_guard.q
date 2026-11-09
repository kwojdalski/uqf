/ stream_replay_guard.q - what a streaming job does with a batch it cannot apply
/ while it is catching up from a log, and how it learns it has stopped listening
/ (.qetl.job.stream.quarantine, .qetl.job.stream.tolerant, #2195, #2201).
/ .
/ LIVE, a batch that throws costs that batch: the plant sends the next one.
/ REPLAYING, it used to cost the rest of the log. TorQ traps -11! once per log
/ file, so one error ended the replay of every later message in that file
/ and `.sub.subscribe` returned normally; the standalone runner re-raised it
/ and could not start again until the plant's day rolled, because every restart
/ replayed the same message. A restart must be a pure function of the log, so
/ a catch-up treats a bad batch as live does - counted, logged, skipped - and
/ ALSO keeps it: the batch is quarantined with its table, its position in the
/ catch-up, the error and its rows, so nothing it held is silently gone.
/ .
/ WHAT REPORTS IT. guarded (stream_job.q) has already counted the failure in
/ .qetl.stream_health before the error reaches this file, so the next uptime
/ beat ends the session and `uqs job gaps` / `uqs status` show the job
/ failing. This adds the rows, in `.qetl.job.stream.quarantined` and in the
/ status directory (stream_quarantine_<job>), where the operator can read
/ and re-apply them.
/ .
/ SUBSCRIPTION WATCH. A job whose transport reports it is no longer subscribed
/ (a tickerplant restart marks TorQ's subscription inactive) is recorded as
/ failing at every beat until it is subscribed again: that ends its uptime
/ session, so the outage is a gap and not a healthy job that receives nothing.

\d .

/ Set to 1b by a runner while it replays a log or catches up on a gap: a batch
/ that throws is then quarantined instead of re-raised.
.qetl.job.stream.trapping:0b

/ The batches this process quarantined: where they came from, why, and what
/ they held. position is the batch's ordinal in the catch-up that delivered it
/ (1 for the first message the handler was given).
.qetl.job.stream.quarantined:([] job:`symbol$(); at:`timestamp$(); table:`symbol$(); position:`long$(); error:(); rows:())

/ Messages given to the handlers since trapping began, per job.
.qetl.job.stream.catchup_position:(`symbol$())!`long$()

/ Keep a batch a catch-up could not apply.
/ @param job the streaming job's name
/ @param t the table the batch was for
/ @param position the batch's ordinal in the catch-up
/ @param err the error, a string
/ @param rows the batch as delivered
/ @return the number of batches `job` has quarantined in this process
/ @eg .qetl.job.stream.quarantine[`eg_job;`fills;3;"bad qty";([] qty:enlist 0n)]  / 1
.qetl.job.stream.quarantine:{[job;t;position;err;rows]
    `.qetl.job.stream.quarantined insert (job;.z.p;t;position;err;rows);
    .[{.qetl.log.ch.err[`transform;y;(enlist[`job]!enlist x),z]};(job;"batch quarantined - replay continues";`table`position`error!(t;position;err));::];
    / Never let keeping the evidence stop the catch-up it is evidence of.
    .[{[who] q:select from .qetl.job.stream.quarantined where job=who;
        / set makes the status directory if it is missing: no shell needed
        (hsym `$.qetl.status.status_dir[],"/stream_quarantine_",string who) set q};enlist job;
        {[job;e] .qetl.log.ch.warn[`storage;"could not write its quarantine file";(enlist[`job]!enlist job),enlist[`error]!enlist e];}[job]];
    n:job;
    exec count i from .qetl.job.stream.quarantined where job=n}

/ The ordinal of the next message a catch-up delivers to `job`. A runner that
/ rejects a message before the handler sees it (torq_stream.q, naming its
/ columns) takes one too, so a position counts every message delivered.
/ @param job the streaming job's name
/ @return the ordinal, 1 for the first message
/ @eg .qetl.job.stream.catching_up[`eg_job;1b]; .qetl.job.stream.next_position`eg_job  / 1
.qetl.job.stream.next_position:{[job]
    p:1+0^.qetl.job.stream.catchup_position job;
    .qetl.job.stream.catchup_position[job]:p;
    p}

/ A job's handler for a catch-up: the handler, with an error quarantined and
/ not re-raised while trapping is 1b, and the handler exactly as it is
/ otherwise (live batches keep their own semantics).
/ @param job the streaming job's name
/ @param h the handler, a function of (table; rows)
/ @param t the table
/ @param x the rows
/ @return what the handler returns, or 0 for a quarantined batch
/ @eg .qetl.job.stream.tolerant[`eg_job;{[t;x] 'boom};`fills;()]  / throws 'boom while trapping is 0b
.qetl.job.stream.tolerant:{[job;h;t;x]
    if[not .qetl.job.stream.trapping; :h[t;x]];
    p:.qetl.job.stream.next_position[job];
    r:.[{(1b;x[y;z])}[h];(t;x);{(0b;x)}];
    if[first r; :last r];
    .qetl.job.stream.quarantine[job;t;p;last r;x];
    0}

/ Begin or end a catch-up: while on, tolerant quarantines and numbers batches.
/ @param on 1b to begin (the position counter restarts), 0b to end
/ @return the flag
/ @eg .qetl.job.stream.catching_up[`eg_job;0b]  / 0b
.qetl.job.stream.catching_up:{[job;on]
    if[on; .qetl.job.stream.catchup_position[job]:0];
    `.qetl.job.stream.trapping set on;
    on}

/ At each beat: if the transport can say whether the job is still subscribed
/ and it is not, count a failed batch and warn, so the beat ends the job's
/ uptime session. A transport that cannot say (no `subscribed` key), or whose
/ answer throws, is taken as subscribed: this must never stop the beat.
/ @param job the streaming job's name
/ @param tr the transport
/ @return 1b when the job is subscribed (or it cannot be told)
/ @eg .qetl.job.stream.watch_subscription[`eg_job;()!()]  / 1b
.qetl.job.stream.watch_subscription:{[job;tr]
    if[not `subscribed in key tr; :1b];
    r:.[{(1b;x[])};enlist tr`subscribed;{(0b;x)}];
    ok:$[first r; last r; 1b];
    if[not ok;
        .[.qetl.stream_health.record;(job;0b;"not subscribed: the tickerplant subscription is inactive");::];
        .[{.qetl.log.ch.warn[`ipc;y;(enlist[`job]!enlist x),z]};(job;"subscription inactive - receiving nothing until it is re-established";()!());::]];
    ok}

\d .
