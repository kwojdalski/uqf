/ stream_job.q - the contract a continuous (streaming) job declares, and the
/ seam its process runs it through (.qetl.job.stream).
/ .
/ The bounded half of this tree has had a shell since #124: a worker declares
/ its source, dataset, width and transform, and .qetl.job.bounded runs it. The continuous
/ half had nothing. Each of the four tickerplant subscriber jobs was TWO
/ files - its computation in src/etl/transforms/stream.q so it could be
/ tested, its subscription, buffers, timer and publish in its own
/ scripts/torq_*_etl.q so it could connect - and the four wiring scripts were
/ the same twenty lines four times.
/ .
/ A job is now ONE file (src/etl/streaming/<job>.q) holding every step:
/ schemas, transform, the batch handler, the timer body, its state, and the
/ declaration below. One runner (scripts/processes/torq_stream.q) runs any of them.
/ .
/ WHAT MAKES THAT POSSIBLE is the publish seam. A job never calls TorQ: it
/ calls `publish` in its OWN namespace, which is a stub that throws until
/ something wires it. The runner wires it to the tickerplant; a test wires it
/ to a recorder and gets the job's output as data. So the job file can be
/ loaded by src/etl/init.q in a plain q process with no TorQ present, which
/ is what keeps its functions in docs/man.q and under test - the reason the
/ computations were moved out to src/ in the first place (nothing in
/ src/etl/ may depend on TorQ).

\d .qetl.job.stream

/ job -> its declaration, ENLISTED. Keyed by the job's own name, which is
/ also the segment of its namespace: `demo_markout` is `.qpipe.job.demo_markout`.
/ .
/ The enlist is load-bearing, and the reason is a q trap worth knowing: a
/ dictionary whose values are dictionaries with the SAME keys is a table, and
/ q makes it one silently. The four feeds register first and declare exactly
/ the same fields, so by the fifth registration `jobs` had become a keyed
/ table - and markout's declaration, which carries an on_batch the feeds do
/ not, was refused with a bare 'mismatch naming nothing. Enlisting each
/ declaration keeps the values a general list, so a job may declare whatever
/ its shape needs.
jobs:(`symbol$())!();

/ procname -> the job that runs there. A second index rather than a scan:
/ the runner looks itself up by procname on every start, and a scan over
/ declarations to answer it would have to reach inside each one.
procnames:(`symbol$())!`symbol$();

/ job -> its declared state at define time, ENLISTED as `jobs` is (#967).
initial:(`symbol$())!();

/ What every job must declare.
/ .
/ `on_batch` is required of a job that SUBSCRIBES and `on_timer` of one that
/ only produces - a feed subscribes to nothing and publishes on a timer, and
/ demanding a batch handler of it would mean writing an empty one. A job
/ with neither is declared but does nothing, which is refused.
/ .
/ `subscribe_to` and `publishes` are the wiring the runner performs on the
/ job's behalf, and they are also what uqs's pipeline_edges
/ checks the Python registry against - so the q file and the registry cannot
/ drift. `procname` is the TorQ process that runs this job, and is how the
/ runner knows which job it is: one generic process script, and the name it
/ was started under decides. `on_batch` is the job itself.
required_declarations:`ns`procname`subscribe_to`publishes

/ Private: can this value be called?
/ .
/ Not `100h=type`, which is a LAMBDA only. The natural way to hand a job a
/ publisher is to bind the handle into one - `.qtorq.publish[h;;]` - and
/ that is a projection (104h), as is a test's recorder bound to a job name.
/ Refusing those would mean the seam only accepted the one shape nobody
/ writes. 100-112h covers lambdas, operators, projections, compositions and
/ q's own iterators.
/ @private
is_callable:{[v] (type v) within 100 112h}

/ The namespace every job instance lives under, as .qpipe.job.<job>.
/ .
/ Separate from this framework's own `.qetl.job.stream` for the reason `.qpipe.job` is
/ separate from `.qetl.job.bounded`: a job called `jobs` or `define` nested inside the
/ framework would overwrite it.
job_root:`.qpipe.job

/ The namespace a job's implementation lives in.
/ @param job the job's name
/ @return the namespace symbol, e.g. `.qpipe.job.demo_markout
/ @eg .qetl.job.stream.namespace `demo_markout  ->  `.qpipe.job.demo_markout
namespace:{[job] ` sv job_root,job}

/ Private: refuse a malformed optional key - deployment facts, transform,
/ check and on_fail. Split out of `define`, whose single body was too big
/ for q to compile once the coverage tool instrumented it ('limit).
/ @param job the job's name
/ @param decl its declaration
/ @return nothing - it throws naming the job and the key
/ @private
check_optional:{[job;decl]
    / Deployment facts, both optional. uqs derives its process registry
    / from these declarations, so this is where a job says whether it starts
    / with the stack (default: on demand) and why it is deployed as it is.
    if[(`start_with_all in key decl) and not -1h=type decl`start_with_all;
        '"define: ",string[job],"'s start_with_all must be a boolean, 1b to start with the stack"];
    if[(`note in key decl) and not 10h=type decl`note;
        '"define: ",string[job],"'s note must be a string"];
    / What the job does when the plant's day ends (#943): roll, snapshot or
    / reset whatever state it carries. Called with the date that ended.
    if[(`on_endofday in key decl) and not is_callable decl`on_endofday;
        '"define: ",string[job],"'s on_endofday must be a function of the date that ended"];
    / The shared transform the job applies, when it applies one: what a twin
    / refilling its table must apply too, and what `uqs job new --twin-of`
    / scaffolds into one (#884). Checked here, so a declared name cannot
    / point at nothing.
    if[`transform in key decl;
        if[not -11h=type decl`transform;
            '"define: ",string[job],"'s transform must be a symbol naming a registered transform"];
        if[not (decl`transform) in .qetl.transform.defined[];
            '"define: ",string[job]," declares transform ",string[decl`transform],", which is not registered - define it before the job"]];
    / A quality gate on what the job publishes (#944), applied by `wire` to
    / every publish the job makes. Declaring what to do on failure is
    / required with it: whether one bad row holds back its neighbours is the
    / decision, and a default would make it for the author without asking.
    if[(`on_fail in key decl) and not `check in key decl;
        '"define: ",string[job]," declares on_fail without a check - there is nothing to fail"];
    if[`check in key decl;
        if[not is_callable decl`check;
            '"define: ",string[job],"'s check must be a function of the rows about to be published, returning the offending rows"];
        if[not (`on_fail in key decl) and -11h=type decl`on_fail;
            '"define: ",string[job]," declares a check but no on_fail - say `drop (publish the clean rows) or `hold (withhold the whole batch)"];
        if[not (decl`on_fail) in `drop`hold;
            '"define: ",string[job],"'s on_fail must be `drop or `hold, not ",string decl`on_fail]];
    }

/ Private: refuse a malformed replay declaration, and record the job's
/ declared state for `reset`. Split out of `define` for the same reason.
/ @param job the job's name
/ @param decl its declaration, `ns` set
/ @return nothing - it throws naming the job and the key
/ @private
check_replay:{[job;decl]
    / Restoring state at start - see `start` below.
    if[(`replay in key decl) and not -1h=type decl`replay;
        '"define: ",string[job],"'s replay must be a boolean, 1b to rebuild state from the day's log at start"];
    replays:$[`replay in key decl; decl`replay; 0b];
    if[`restore_from in key decl;
        if[not 11h=abs type decl`restore_from;
            '"define: ",string[job],"'s restore_from must be a symbol list of table names"];
        if[not replays;
            '"define: ",string[job]," declares restore_from without replay 1b - those tables are read only by a replay"];
        if[not `on_batch in key decl;
            '"define: ",string[job]," declares restore_from but no on_batch to read it with"]];
    if[`carry in key decl; check_carry[job;decl;replays]];
    if[`on_replayed in key decl;
        if[not is_callable decl`on_replayed;
            '"define: ",string[job],"'s on_replayed must be a niladic function"];
        if[not replays;
            '"define: ",string[job]," declares on_replayed without replay 1b - it would never be called"]];
    if[replays and not `on_batch in key decl;
        '"define: ",string[job]," declares replay 1b but no on_batch - a replay delivers batches"];
    / The variables the job carries between batches, declared once. Their value
    / now - define runs at the end of the job's file - is the empty state
    / `reset` restores, so no test restates it (#967).
    if[`state in key decl;
        if[not 11h=type decl`state;
            '"define: ",string[job],"'s state must be a symbol list naming variables in ",string[decl`ns]];
        absent:decl[`state] where not (decl`state) in key decl`ns;
        if[count absent; '"define: ",string[job],"'s state names ",(", " sv string absent),", which ",string[decl`ns]," does not define"];
        initial[job]:enlist (decl`state)!get each ` sv'decl[`ns],/:decl`state];
    }

/ Declare a streaming job. Called by the job's own file as it loads, so a
/ declaration and its implementation cannot drift - there is no way to have
/ one without the other.
/ @param job the job's name, e.g. `demo_markout
/ @param decl dict of procname, subscribe_to, publishes, and then on_batch
/   (required when it subscribes), period and on_timer (a pair) - or period
/   and poll, for a polling feed (see stream_poll.q); optionally
/   start_with_all (a boolean, default 0b), note (a string), transform
/   (the registered transform it applies, which its backfill twin applies
/   too - #884), on_endofday (a function of the date that ended, #943), and
/   check with on_fail (a quality gate on what is published, #944), and
/   state (a symbol list of the job's private variables, #967: their values
/   as the job's file leaves them are what `reset` restores)
/ @return the job name
/ @throws error naming every missing or malformed field at once
define:{[job;decl]
    / Both execution modes share .qpipe.job, so a name cannot belong to both.
    if[job in @[{.qetl.job.bounded.defined[]};::;{[e] `symbol$()}];
        '"define: ",string[job]," is already a bounded job - job names must be unique across execution modes"];
    if[not 99h=type decl; '"define: ",string[job],"'s declaration must be a dictionary"];
    decl[`ns]:namespace[job];
    missing:required_declarations where not required_declarations in key decl;
    if[count missing;
        '"define: ",string[job]," is missing ",", " sv string missing];
    if[not -11h=type decl`procname;
        '"define: ",string[job],"'s procname must be a symbol naming the TorQ process that runs it, e.g. `demo_markout1"];
    if[(decl`procname) in key procnames;
        '"define: ",string[job]," claims procname ",string[decl`procname]," which ",(string procnames decl`procname)," already runs - one process runs one job"];
    if[not 11h=abs type decl`subscribe_to;
        '"define: ",string[job],"'s subscribe_to must be a symbol list of table names"];
    if[not 11h=abs type decl`publishes;
        '"define: ",string[job],"'s publishes must be a symbol list, empty for a job that keeps its output local"];
    / A polling feed declares its steps rather than its timer, and gets the
    / timer built from them (stream_poll.q) - before the checks below, which
    / then see an ordinary feed.
    if[`poll in key decl; decl:poll_declared[job;decl]];
    if[(`on_batch in key decl) and not is_callable decl`on_batch;
        '"define: ",string[job],"'s on_batch must be a function taking (table name; rows), [t;x] as in TorQ's upd"];
    / A subscriber with no handler receives every batch and drops it, and a
    / job with neither handler nor timer runs nothing at all - both look
    / healthy from outside, which is why each is refused by name here.
    if[(count decl`subscribe_to) and not `on_batch in key decl;
        '"define: ",string[job]," subscribes to ",(", " sv string decl`subscribe_to)," but declares no on_batch - every batch would arrive and be dropped"];
    if[not any (`on_batch;`on_timer) in \:key decl;
        '"define: ",string[job]," declares neither on_batch nor on_timer - it would subscribe to nothing, publish nothing and run nothing"];
    / A timer is optional, but half a timer is a job whose scoring never runs
    / while every test still passes - so the pair is checked together.
    has_period:`period in key decl;
    has_body:`on_timer in key decl;
    if[has_period<>has_body;
        '"define: ",string[job]," declares ",$[has_period;"period without on_timer";"on_timer without period"]," - a timer is both or neither"];
    if[has_period;
        if[not 16h=abs type decl`period;
            '"define: ",string[job],"'s period must be a timespan, e.g. 0D00:00:01"];
        if[not (decl`period)>0D00:00;
            '"define: ",string[job],"'s period must be positive"];
        if[not is_callable decl`on_timer;
            '"define: ",string[job],"'s on_timer must be a niladic function"]];
    check_optional[job;decl];
    check_replay[job;decl];
    jobs[job]:enlist decl;
    procnames[decl`procname]:job;
    .[{.qetl.log.dbg[x;y;z]};(job;"streaming job registered";
        `procname`subscribe_to`publishes`timer!(decl`procname;decl`subscribe_to;decl`publishes;
            $[has_period; decl`period; 0Nn]));::];
    job}

/ One job's declaration, or a refusal naming it.
/ @param job the job's name
/ @return the declaration dict
/ @throws error naming the job when register was never called for it
/ @eg .qetl.job.stream.def[`demo_markout]`subscribe_to  ->  `trades`quote
def:{[job]
    if[not job in key jobs;
        '"def: ",string[job]," is not a registered streaming job - a job registers as its own file loads, so this is a wiring bug rather than a lookup miss"];
    first jobs job}

/ The job a TorQ process runs, by the name the process was started under.
/ .
/ The runner is generic: one script serves every streaming job, and this is
/ how a started process finds out which one it is. A procname nothing claims
/ is an error naming it rather than a process that comes up subscribed to
/ nothing and reports healthy.
/ @param procname the TorQ process name, e.g. `demo_markout1
/ @return the job's name
/ @throws error when no registered job claims that process
/ @eg .qetl.job.stream.for_procname `demo_markout1  ->  `demo_markout
for_procname:{[procname]
    if[not procname in key procnames;
        '"for_procname: no streaming job runs as ",string[procname]," - registered processes: ",", " sv string key procnames];
    procnames procname}

/ Put a job's declared state back to what its file defined (#967).
/ .
/ The one way to start a job from empty, for a test or a runner that
/ re-initialises: a job names its variables in `state` at define, so nothing
/ restates them by name and shape. A job that declares none has nothing to
/ restore and this is a no-op.
/ @param job the job's name
/ @return the job name
/ @throws error when the job is not registered
/ @eg .qetl.job.stream.reset `cross  ->  `cross
reset:{[job]
    def job;
    if[job in key initial;
        s:first initial job;
        {[ns;k;v] (` sv ns,k) set v}[namespace job]'[key s;value s]];
    job}

/ Every registered job, for the runner and for tests.
/ @return symbol list of job names
defined:{[] key jobs}

/ Point a job's `publish` at something that can actually publish.
/ .
/ The one call that turns a declaration into a running job. The runner passes
/ a tickerplant publisher; a test passes a recorder and then reads what the
/ job would have published. Until this is called the job's own stub throws,
/ so "nobody wired it" is an error rather than rows quietly going nowhere.
/ @param job the job's name
/ @param publisher a function of (table name; rows)
/ @return the job name
wire:{[job;publisher]
    if[not is_callable publisher;
        '"wire: ",string[job],"'s publisher must be callable as (table; rows) - a lambda or a projection over one"];
    d:def[job];
    / A declared check sits in the seam itself, so every route a row takes out
    / of the job - batch handler, timer, poll, on_replayed - passes through it.
    (` sv (d`ns),`publish) set $[`check in key d; checked[job;d`check;d`on_fail;publisher]; publisher];
    .[{.qetl.log.dbg[x;y;z]};(job;"publish seam wired";enlist[`publishes]!enlist d`publishes);::];
    job}

/ Private: a publisher that runs the job's declared check first (#944).
/ .
/ The check gets the rows about to be published and returns a table of
/ offending rows, the bounded worker's failure shape (check, status, detail -
/ .qetl.job.bounded.no_failures) with one optional column: `row`, the index in
/ the batch of the row that offends. Under `drop` the rows named by `row` are
/ withheld and the rest published; a failure that names no row, and every
/ failure under `hold`, withholds the whole batch - never publishing a row
/ that might be the bad one.
/ .
/ A check that THROWS is a failure of the whole batch under either choice:
/ rows nobody could vouch for are not published. Every withheld batch is
/ logged with its failures and counted in .qetl.stream_health, which is what
/ shows the job as failing in `uqs summary`.
/ @private
checked:{[job;chk;mode;publisher;t;x]
    r:@[{[chk;x] (1b;chk x)}[chk];x;{[e] (0b;e)}];
    if[not first r;
        :withhold[job;t;x;"check threw: ",last r;();count x]];
    f:last r;
    if[not .Q.qt f;
        :withhold[job;t;x;"check returned something other than a table of failures";();count x]];
    if[0=count f; :publisher[t;x]];
    n:count x;
    rows:$[(`drop=mode) and (`row in cols f) and (98h=type x) and not any null f`row;
        distinct "j"$f`row; til n];
    clean:$[98h=type x; x where not (til n) in rows; x];
    withhold[job;t;x;"check failed: ",string[count f]," failure(s)";f;count rows];
    $[count clean; publisher[t;clean]; ::]}

/ Private: log and count a batch the check withheld, in whole or part.
/ @private
withhold:{[job;t;x;msg;f;n]
    .[.qetl.stream_health.record;(job;0b;msg);::];
    .[{.qetl.log.err[x;y;z]};(job;"publish check withheld rows";
        `table`rows`withheld`failures!(t;count x;n;$[.Q.qt f;300#.Q.s1 5#f;""]));::];
    ::}

/ ------------------------------------------------------------ THE BUFFER

/ Take every row matching mask out of a buffer table and return it, leaving
/ the rest behind - the queue block's drain step. mask is evaluated ONCE by
/ the caller and used for both the read and the delete, so a row arriving
/ between the two cannot be dropped unscored; hand-written drain code that
/ recomputes its cutoff in the delete clause has exactly that race.
/ .
/ Lived in scripts/processes/torq_pipeline.q until the jobs moved into src/, where
/ nothing may call .qtorq. It belongs here anyway: buffering is the
/ job's own business, not TorQ's.
/ @param table_name the buffer table's fully-qualified name, e.g. `.qpipe.job.demo_markout.pending
/ @param mask a boolean vector over that table, as long as it is
/ @return the drained rows, in their original order
/ @eg `.qetl.job.stream.eg_buffer set ([] a:1 2 3); .qetl.job.stream.drain[`.qetl.job.stream.eg_buffer;101b]  ->  ([] a:1 3)
/ @see .qetl.job.stream.evict - use that instead when a failed publish should retry
/   the batch rather than lose it (drain is at-most-once, evict at-least-once)
drain:{[table_name;mask]
    buffer:get table_name;
    if[0=count buffer; :buffer];
    ready:buffer where mask;
    table_name set buffer where not mask;
    ready}

/ Remove every row matching mask from a buffer table, keeping the rest, and
/ return how many went - the eviction step of a read-then-confirm flow:
/ compute the mask once, read the batch with it, publish it, and only then
/ evict. Use this rather than `drain` whenever losing a batch matters,
/ because the runner's timer wrapper swallows a publish failure: with
/ `drain` the rows are already gone and the batch is lost (at-most-once);
/ with `evict` they are still buffered and the next tick retries them
/ (at-least-once). Pass the SAME mask to the read and to evict - a
/ recomputed cutoff between the two is the race both helpers prevent.
/ @param table_name the buffer table's fully-qualified name
/ @param mask the boolean vector already used to read the batch
/ @return the number of rows removed
/ @eg `.qetl.job.stream.eg_buffer set ([] a:1 2 3); .qetl.job.stream.evict[`.qetl.job.stream.eg_buffer;101b]  ->  2i
evict:{[table_name;mask]
    buffer:get table_name;
    if[0=count buffer; :0];
    table_name set buffer where not mask;
    sum mask}

/ The stub every job's `publish` starts as.
/ .
/ Throws rather than doing nothing: a job whose rows vanish because nothing
/ wired it looks exactly like a job with no rows to publish, and that is the
/ failure this tree keeps finding.
/ @param job the job's name, for the message
/ @return a function that throws when called
/ ------------------------------------------------------------- STARTING

/ 1b while a job is being replayed its day's log at start, 0b otherwise.
/ .
/ A job's on_batch reads it to tell a replayed batch from a live one, when
/ the difference matters to it - kafka_flow holds replayed rows until the
/ replay has shown it everything it already published. Most jobs need not
/ look: during a replay their publish is muted anyway.
replaying:0b

/ The log clock: the time a stream decision uses for each row of a batch (#994).
/ .
/ Live, that is the process clock - the row has just arrived, so `now` is when
/ it met the job. Replaying, the process clock is the restart time and every
/ logged row looks hours old, so the clock is the plant's receipt stamp `time`,
/ which the tickerplant wrote on the row when the live process first saw it.
/ A decision made on this clock comes out the same live and on replay, which is
/ what makes a restart a pure function of the log. A batch with no `time` has
/ no log clock: null per row, which no comparison satisfies, so a time-based
/ rule is simply not applied to it - say so where the rule is documented.
/ .
/ Routed through here: the bars kind's lateness. NOT routed, because the log
/ cannot say what time it was: a timer tick (bars' close_ready, horizon's
/ score_ready, fx_positions' throttle, superbook, alert_sink, cross) - a tick
/ is not a logged row, so its `now` is always the process clock and it never
/ runs during a replay.
/ @param now the process clock (a job passes its own `now`, so a test can move it)
/ @param x the batch, a table
/ @return a timestamp per row of x
/ @eg .qetl.job.stream.clock_of[2026.10.09D10:00:00;([] a:1 2)]  ->  2026.10.09D10:00:00 2026.10.09D10:00:00
clock_of:{[now;x]
    $[replaying; $[`time in cols x; x`time; count[x]#0Np]; count[x]#now]}

/ The log clock with the process clock for `now`.
/ @param x the batch, a table
/ @return a timestamp per row of x
/ @eg count .qetl.job.stream.clock ([] a:1 2)  ->  2
clock:{[x] clock_of[.z.p;x]}

/ What a runner must hand `start`: the transport, as functions.
/ .
/   connect             niladic; reach the plant.
/   publisher           niladic, called after connect; the function a job
/                       publishes through, (table; rows).
/   subscribe           [tables; handler; replay]: deliver batches of `tables`
/                       to handler[table; rows]. With replay 1b, deliver the
/                       day's log first, synchronously, before it returns.
/   timer               [name; period; f]: call niladic f every period.
/   check_publishable   optional, [tables]: refuse a table the plant lacks.
transport_keys:`connect`publisher`subscribe`timer

/ Every table a job subscribes to: its inputs, and those it reads only to
/ restore state. The second are not inputs - .qetl.dag reads subscribe_to
/ alone, so a job restoring from its own output does not draw a cycle.
/ @param job the job's name
/ @return the tables, as a symbol list
subscriptions:{[job]
    d:def[job];
    distinct (),(d`subscribe_to),$[`restore_from in key d; d`restore_from; `symbol$()],
        $[`carry in key d; enlist d[`carry]`table; `symbol$()]}

/ Private: the job's on_batch, counting every batch in .qetl.stream_health
/ and logging a failure with its table before re-raising it. Re-raised, the
/ error still reaches the caller; counted, a job that drops every batch is
/ a gap in `uqs gaps` and `failing` in `uqs summary` rather than a process
/ that looks healthy (#832).
/ @private
guarded:{[job;f;t;x]
    r:.[f;(t;x);{[job;t;e]
        .[.qetl.stream_health.record;(job;0b;e);::];
        .[{.qetl.log.err[x;y;z]};(job;"on_batch failed";`table`error!(t;e));::];
        'e}[job;t]];
    .[.qetl.stream_health.record;(job;1b;"");::];
    r}

/ Start a job on a transport: the one sequence every runner uses.
/ .
/ IN THIS ORDER, and the order is the point.
/   1. connect.
/   2. Wire publish - BEFORE subscribing, so a replay can reach it.
/   3. Subscribe, which installs the handler first. With `replay` 1b the
/      day's log is delivered before any live batch, with publish MUTED and
/      `replaying` set: a replay rebuilds state; it must not publish again
/      what was published before the restart.
/   4. on_replayed, with publish live again, for what the replay left owed.
/   5. Uptime: a session in .qetl.uptime, beaten on a timer - the record of
/      when the job was up and subscribed, which `uqs gaps` reads (#630).
/   6. Timers: the job's, and the configuration audit's.
/ .
/ Both runners used to do this themselves, differently (#data-platform
/ review): torq_stream.q subscribed with replay off and installed the job's
/ handler after subscribing, so turning replay on would have replayed into
/ TorQ's default upd; run_stream.q replayed every job before wiring
/ publish, so a job publishing per batch threw on recovery.
/ @param job the job's name
/ @param tr the transport, a dictionary of transport_keys
/ @return the job name
/ @throws error when the transport lacks a key, or any step fails
start:{[job;tr]
    missing:transport_keys where not transport_keys in key tr;
    if[count missing; '"start: the transport is missing ",", " sv string missing];
    d:def[job];
    replay:$[`replay in key d; d`replay; 0b];
    tbls:subscriptions[job];
    .[{.qetl.log.info[x;y;z]};(job;"starting streaming job";
        `subscribe_to`publishes`replay`timer!(tbls;d`publishes;replay;
            $[`period in key d; d`period; 0Nn]));::];
    tr[`connect][];
    if[count d`publishes;
        if[`check_publishable in key tr; tr[`check_publishable] d`publishes];
        wire[job;tr[`publisher][]]];
    if[count tbls;
        live:get pub:` sv (d`ns),`publish;
        if[replay; pub set {[t;x] count x}; `.qetl.job.stream.replaying set 1b];
        r:@[{[tr;tbls;h;replay] tr[`subscribe][tbls;h;replay]; (1b;::)}[tr;tbls;handler job];
            replay;{[e] (0b;e)}];
        pub set live;
        `.qetl.job.stream.replaying set 0b;
        replayed job;
        if[not first r; 'last r];
        if[`on_replayed in key d; (d`on_replayed)[]];
        / Protected: a record that cannot be written must never stop the job
        / it records. Logged, so a missing session is explained.
        .[{[tr;job] .qetl.uptime.begin job;
            tr[`timer][`$(string job),"_uptime";.qetl.uptime.period;{@[.qetl.uptime.beat;::;{[e] .qetl.log.warn[`uptime;"could not record a beat";enlist[`error]!enlist e];}]}]};
          (tr;job);
          {[job;e] .qetl.log.warn[job;"could not open an uptime session - its gaps will read as down";enlist[`error]!enlist e];}[job]]];
    if[`period in key d; tr[`timer][job;d`period;d`on_timer]];
    if[count .qetl.cfg.audit.watching job;
        `.qetl.cfg.audit.owner_here set job;
        tr[`timer][`$(string job),"_config";.qetl.cfg.audit.period;.qetl.cfg.audit.poll_and_publish]];
    `.qetl.job.stream.running set distinct .qetl.job.stream.running,job;
    .[{.qetl.log.info[x;y;z]};(job;"streaming job wired - running";
        `subscribe_to`publishes!(tbls;d`publishes));::];
    job}

/ The jobs this process has started, in the order it started them: whom
/ end_of_day tells.
running:`symbol$()

/ The plant's day has ended: call each running job's on_endofday with it.
/ .
/ Called by the runner - TorQ's root `endofday` (.qtorq.install_period_handlers),
/ or run_stream.q's own plant once it has rolled its log - AFTER the plant
/ has moved to the new day, so whatever a job publishes here opens the new
/ day's log, where a restart's replay finds it.
/ .
/ Each call is trapped and logged: one job's failure must not stop the
/ others' end of day, nor the process.
/ @param dt the date that ended
/ @return the jobs whose on_endofday ran without throwing
/ @eg .qetl.job.stream.end_of_day 2026.10.09
end_of_day:{[dt]
    / carried state first (#963): its snapshot opens the new day's log
    cs:running where {[j] `carry in key def j} each running;
    {[dt;j] @[snapshot;j;{[dt;j;e] .qetl.log.err[j;"carry snapshot failed";`date`error!(dt;e)]}[dt;j]]}[dt] each cs;
    js:running where {[j] `on_endofday in key def j} each running;
    ok:{[dt;j] @[{[dt;j] (def[j]`on_endofday) dt; 1b}[dt];j;
        {[dt;j;e] .qetl.log.err[j;"on_endofday failed";`date`error!(dt;e)]; 0b}[dt;j]]}[dt] each js;
    js where ok}

/ ------------------------------------------------------------ CARRY
/ .
/ State a job carries across days and restarts (#963): a position book, say.
/ The job declares it - carry:`state`table!(`book;`position_open) - and the
/ shell does the rest, so no job hand-builds recovery again (#960 was two
/ jobs getting that wrong together):
/ .
/   at end of day   end_of_day publishes the state onto `table`, after the
/                   plant has rolled its log: it opens the new day's log.
/   on replay       batches are applied as they come, and those within
/                   `window` of the log's start are also recorded. When the
/                   snapshot arrives, the state is SET from it and the recorded
/                   batches are applied again: the plant tells the job its day
/                   ended asynchronously, so a batch can be logged ahead of the
/                   snapshot that live it was applied after. Exact for any
/                   state, lot order included; bounded by `window`.
/   live            the snapshot is this job's own echo, and is ignored.

/ Every carry's recording during a replay: job -> (first time; done; batches).
carry_log:(`symbol$())!()

/ Private: refuse a malformed carry, naming it.
/ @private
check_carry:{[job;decl;replays]
    c:decl`carry;
    who:"define: ",string[job],"'s carry";
    if[not 99h=type c; 'who," must be a dict of state and table"];
    if[count `state`table except key c; 'who," needs state (the variable it carries) and table (where its snapshot goes)"];
    if[not -11h=type c`state; 'who,"'s state must be the name of a variable in the job's namespace"];
    if[not (c`table) in (),decl`publishes; 'who,"'s table ",string[c`table]," must be one the job publishes"];
    if[not replays; 'who," needs replay 1b - the snapshot is read back by a replay"];
    if[$[`window in key c; not -16h=type c`window; 0b]; 'who,"'s window must be a timespan"];
    }

/ The handler a job's batches go through: its guarded on_batch, behind its
/ carry when it declares one. What start subscribes with, and what a test
/ replays through.
/ @param job the job's name
/ @return a function of (table;rows)
/ @eg .qetl.job.stream.handler[`fx_positions]
handler:{[job]
    h:guarded[job;def[job]`on_batch];
    $[`carry in key def job; carried[job;h;;]; h]}

/ Private: route one batch for a job that carries state.
/ @private
carried:{[job;h;t;x]
    c:def[job]`carry;
    if[t=c`table;
        if[replaying; restore[job;h;x]];
        :()];
    h[t;x];
    if[replaying; record[job;t;x]];
    }

/ Private: how long after a job's first replayed batch its carry keeps
/ recording - its declared `window`, else a minute.
/ @private
carry_window:{[job] $[`window in key def[job]`carry; def[job][`carry]`window; 0D00:01]}

/ Private: keep a replayed batch, while it is near enough the log's start
/ that the snapshot may yet follow it.
/ @private
record:{[job;t;x]
    r:$[job in key carry_log; carry_log job; (0Np;0b;())];
    if[r 1; :()];
    t0:$[(`time in cols x) and count x; first x`time; 0Np];
    if[null r 0; r[0]:t0];
    if[(not null t0) and t0>r[0]+carry_window job; carry_log[job]:(r 0;1b;()); :()];
    r[2]:r[2],enlist (t;x);
    carry_log[job]:r;
    }

/ Private: set the carried state from its snapshot, then apply again what
/ the replay delivered ahead of it.
/ .
/ A recording `record` gave up on - done, with its first time still set -
/ means the snapshot came later than `window` after the log's first batch.
/ The batches logged before it are then not applied again, so the restored
/ state is missing them (#1015). That is logged as an error, never silent.
/ @private
restore:{[job;h;x]
    c:def[job]`carry;
    r:$[job in key carry_log; carry_log job; (0Np;0b;())];
    if[r[1] and not null r 0;
        .qetl.log.err[job;"carry snapshot arrived after its recording window closed - batches logged before it are not re-applied, so the restored state is missing them";
            `table`first_batch`snapshot`window!(c`table;r 0;$[(`time in cols x) and count x; first x`time; 0Np];carry_window job)]];
    v:` sv (def[job]`ns),c`state;
    cur:get v;
    rows:(cols 0!cur)#x;
    v set $[99h=type cur; (keys cur) xkey rows; rows];
    early:$[job in key carry_log; (carry_log job) 2; ()];
    carry_log[job]:(0Np;1b;());
    {[h;b] h . b}[h] each early;
    }

/ The replay of a job's log is over: forget what its carry recorded.
/ @param job the job's name
/ @return the job's name
/ @eg .qetl.job.stream.replayed[`fx_positions]  ->  `fx_positions
replayed:{[job] carry_log::(enlist job) _ carry_log; job}

/ Publish a job's carried state onto its snapshot table - what end_of_day
/ does for each running job that carries one. Nothing when the state is empty:
/ a restart with no snapshot starts flat, which is the same thing.
/ @param job the job's name
/ @return how many rows were published
/ @eg .qetl.job.stream.snapshot[`fx_positions]
snapshot:{[job]
    c:def[job]`carry;
    rows:0!get ` sv (def[job]`ns),c`state;
    if[count rows; (get ` sv (def[job]`ns),`publish)[c`table;rows]];
    count rows}

unwired:{[job]
    {[job;t;x] '"publish: ",string[job]," is not wired - the runner (or a test) must call .qetl.job.stream.wire first"}[job]}

\d .
