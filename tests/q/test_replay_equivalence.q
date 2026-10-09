/ test_replay_equivalence.q - a restart publishes what the running process would
/ have (#994).
/ .
/ One harness drives a replaying job live through a script of events, then does
/ it again with a restart after the first k of them: reset, replay the log the
/ plant would hold (the inputs, and the job's own restore_from outputs) with
/ publish muted and the process clock a day later, then carry on live. The two
/ publications must be identical, for every k. The scripts exercise the rules a
/ replay must not change - lateness, newest-wins - not just a happy path.
/ .
/ Every replay:1b job is either scripted here or excluded with its reason, and
/ a test refuses a job that is neither, so a new replaying job joins the check
/ rather than escaping it.

\d .reqtest

t0:2026.10.09D10:00:00

/ The clock a job's `now` reads, which the script moves.
clock:t0

/ What the job published this run, as (table; rows).
pubs:()

/ The plant's log this run: inputs, and the outputs a job restores from.
wal:()

/ An executions row received at rcv, for the event at src.
fill:{[src;rcv;s;px] ([] time:enlist rcv; source_time:enlist src; sym:enlist s; size:enlist 1f; price:enlist px)}

/ A market_data row: one level each side.
book:{[st;s;bid;ask]
    ([] time:enlist st; sym:enlist s; market:enlist `fx; source:enlist `lp; source_time:enlist st;
        bid_prices:enlist enlist bid; bid_sizes:enlist enlist 1e6;
        ask_prices:enlist enlist ask; ask_sizes:enlist enlist 1e6)}

/ An event: the clock it happens at, the table it arrives on (`tick for a timer
/ firing) and its rows.
ev:{[at;t;x] (at;t;x)}

/ exec_bars: in-time fills, the first minute closing, a late row for a minute
/ that has a bar, a late row for a minute that never will, and a second minute.
bars_script:{[]
    (ev[t0+0D00:00:10;`executions;fill[t0+0D00:00:10;t0+0D00:00:10;`EURUSD;1.10]];
     ev[t0+0D00:00:40;`executions;fill[t0+0D00:00:35;t0+0D00:00:40;`EURUSD;1.20]];
     ev[t0+0D00:01:10;`tick;()];
     ev[t0+0D00:05:00;`executions;fill[t0+0D00:00:30;t0+0D00:05:00;`EURUSD;1.30]];
     ev[t0+0D00:05:00;`executions;fill[t0+0D00:03:30;t0+0D00:05:00;`USDJPY;150f]];
     ev[t0+0D00:05:10;`executions;fill[t0+0D00:05:10;t0+0D00:05:10;`EURUSD;1.15]];
     ev[t0+0D00:05:50;`executions;fill[t0+0D00:05:20;t0+0D00:05:50;`EURUSD;1.16]];
     ev[t0+0D00:06:10;`tick;()];
     ev[t0+0D00:07:00;`tick;()])}

last_value_script:{[]
    (ev[t0+0D00:00:01;`market_data;book[t0+0D00:00:01;`EURUSD;1.10;1.12]];
     ev[t0+0D00:00:02;`market_data;book[t0+0D00:00:02;`GBPUSD;1.30;1.32]];
     ev[t0+0D00:00:03;`market_data;book[t0+0D00:00:03;`EURUSD;1.11;1.13]];
     ev[t0+0D00:00:04;`market_data;book[t0+0D00:00:00;`EURUSD;1.00;1.02]];
     ev[t0+0D00:00:05;`market_data;book[t0+0D00:00:05;`GBPUSD;1.31;1.33]])}

/ The scripted jobs: name -> the script and what to do on a tick.
scripts:`exec_bars`last_value!(
    `script`tick!(bars_script;{[j;n] .qpipe.job.exec_bars.close_ready n});
    `script`tick!(last_value_script;{[j;n] ::}))

/ Replaying jobs deliberately not scripted: job -> why.
excluded:`fx_positions`posbook`kafka_flow!(
    "its time-based rule is the breach throttle on the timer's .z.p (fresh_breaches), and a tick is not a logged row; no event script yet";
    "no time-based rule; the carried book is covered by its own replay tests; no event script yet";
    "no time-based rule; replayed rows are held for on_replayed (test_kafka_flow); no event script yet")

recorder:{[j;t;x]
    d:.qetl.job.stream.def j;
    `.reqtest.pubs set pubs,enlist (t;x);
    if[t in $[`restore_from in key d; d`restore_from; `symbol$()]; `.reqtest.wal set wal,enlist (t;x)];
    count x}

/ Private: one event, live.
/ @private
step:{[j;spec;e]
    `.reqtest.clock set e 0;
    if[`tick=e 1; spec[`tick][j;e 0]; :()];
    `.reqtest.wal set wal,enlist (e 1;e 2);
    .qetl.job.stream.handler[j][e 1;e 2];
    }

/ Private: the restart - reset, replay the log with publish muted, as start does.
/ @private
restart:{[j]
    pub:` sv ((.qetl.job.stream.def j)`ns),`publish;
    live:get pub;
    .qetl.job.stream.reset j;
    `.reqtest.clock set t0+1D;
    pub set {[t;x] count x};
    `.qetl.job.stream.replaying set 1b;
    h:.qetl.job.stream.handler j;
    r:.[{[h;lg] {[h;b] h . b}[h] each lg; 1b};(h;wal);{[e] e}];
    `.qetl.job.stream.replaying set 0b;
    pub set live;
    if[not 1b~r; 'r];
    d:.qetl.job.stream.def j;
    if[`on_replayed in key d; (d`on_replayed)[]]}

/ What a job published across its script, restarted after k events (k=0: never).
/ @param j the scripted job
/ @param k how many events run before the restart; 0 for an uninterrupted run
/ @return the publications, as a list of (table; rows)
run:{[j;k]
    spec:scripts j;
    evs:spec[`script][];
    pub:` sv ((.qetl.job.stream.def j)`ns),`publish;
    keep:get pub;
    .qetl.job.stream.reset j;
    if[j=`exec_bars; `.qpipe.job.exec_bars.now set {[] .reqtest.clock}];
    `.reqtest.pubs set (); `.reqtest.wal set ();
    .qetl.job.stream.wire[j;recorder[j]];
    step[j;spec] each k#evs;
    if[k>0; restart j];
    step[j;spec] each k _ evs;
    out:pubs;
    if[j=`exec_bars; `.qpipe.job.exec_bars.now set {[] .z.p}];
    pub set keep;
    .qetl.job.stream.reset j;
    out}

/ A restart after any number of events publishes what the uninterrupted run did.
equivalent:{[j]
    base:run[j;0];
    n:count scripts[j][`script][];
    bad:(1+til n) where not base ~/: run[j;] each 1+til n;
    (base;bad)}

test_a_restart_at_any_point_publishes_what_the_running_bars_job_did:{[t]
    r:equivalent `exec_bars;
    .qunit.assertTrue[0<sum count each last each first r;"the script publishes bars"];
    .qunit.assertEquals[last r;`long$();"no restart point changes the output (restart points that differ)"]};

test_a_restart_at_any_point_publishes_what_the_running_last_value_job_did:{[t]
    r:equivalent `last_value;
    .qunit.assertEquals[count first r;4;"the script publishes four changes: the older book is dropped"];
    .qunit.assertEquals[last r;`long$();"no restart point changes the output (restart points that differ)"]};

/ The check is a check: a replay that judged lateness on the restart's process
/ clock - what the log clock replaces - is caught by the same script.
test_the_check_catches_a_replay_that_reads_the_process_clock:{[t]
    keep:.qetl.job.stream.clock_of;
    `.qetl.job.stream.clock_of set {[now;x] count[x]#now};
    r:@[equivalent;`exec_bars;{[e] e}];
    `.qetl.job.stream.clock_of set keep;
    .qunit.assertTrue[not 10h=type r;"the run completed rather than erroring"];
    .qunit.assertTrue[0<count last r;"a restart that republished a late row differs"]};

/ The log clock: the process clock live, the receipt stamp on replay.
test_the_log_clock_is_the_process_clock_live_and_the_receipt_stamp_on_replay:{[t]
    x:([] time:t0+0D00:00:01 0D00:00:02; a:1 2);
    now:t0+1D;
    .qunit.assertEquals[.qetl.job.stream.clock_of[now;x];2#now;"live: now, for every row"];
    `.qetl.job.stream.replaying set 1b;
    r:.qetl.job.stream.clock_of[now;x];
    n:.qetl.job.stream.clock_of[now;([] a:1 2)];
    `.qetl.job.stream.replaying set 0b;
    .qunit.assertEquals[r;x`time;"replay: each row's own stamp"];
    .qunit.assertEquals[n;2#0Np;"replay without a stamp: null, which no comparison satisfies"]};

/ The replaying jobs the tree ships, read as this file loads - before any
/ test registers a throwaway job of its own.
tree_replaying:asc .qetl.job.stream.defined[] where {[j] $[`replay in key d:.qetl.job.stream.def j; d`replay; 0b]} each .qetl.job.stream.defined[]

/ Every replaying job is scripted or excluded with a reason, and no job is both.
test_every_replaying_job_is_scripted_or_excluded:{[t]
    reps:tree_replaying;
    .qunit.assertEquals[asc (key scripts),key excluded;reps;"scripted + excluded is exactly the replaying jobs"];
    .qunit.assertEquals[(key scripts) inter key excluded;`symbol$();"scripted or excluded, not both"]};

/ The horizon kind is outside the check, and this is why: it declares no replay,
/ so a restart empties its pending events rather than rebuilding them, and its
/ scoring is decided at a timer tick the log does not record. If a horizon job
/ gains replay it must be scripted above, and this test says so.
test_the_horizon_kind_is_excluded_because_it_does_not_replay:{[t]
    hz:.qetl.job.stream.horizons.defined[];
    .qunit.assertTrue[0<count hz;"horizon jobs are defined"];
    .qunit.assertEquals[0i;`int$sum {[j] $[`replay in key d:.qetl.job.stream.def j; d`replay; 0b]} each hz;
        "none replays - script it in test_replay_equivalence.q the day one does"]};

afterNamespace_reset_the_jobs:{[] .sjtest.reset[];}

\d .
