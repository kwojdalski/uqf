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

/ Declare a streaming job. Called by the job's own file as it loads, so a
/ declaration and its implementation cannot drift - there is no way to have
/ one without the other.
/ @param job the job's name, e.g. `demo_markout
/ @param decl dict of procname, subscribe_to, publishes, and then on_batch
/   (required when it subscribes), period and on_timer (a pair) - or period
/   and poll, for a polling feed (see stream_poll.q); optionally
/   start_with_all (a boolean, default 0b) and note (a string)
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
    / Deployment facts, both optional. uqs derives its process registry
    / from these declarations, so this is where a job says whether it starts
    / with the stack (default: on demand) and why it is deployed as it is.
    if[(`start_with_all in key decl) and not -1h=type decl`start_with_all;
        '"define: ",string[job],"'s start_with_all must be a boolean, 1b to start with the stack"];
    if[(`note in key decl) and not 10h=type decl`note;
        '"define: ",string[job],"'s note must be a string"];
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
    if[`on_replayed in key decl;
        if[not is_callable decl`on_replayed;
            '"define: ",string[job],"'s on_replayed must be a niladic function"];
        if[not replays;
            '"define: ",string[job]," declares on_replayed without replay 1b - it would never be called"]];
    if[replays and not `on_batch in key decl;
        '"define: ",string[job]," declares replay 1b but no on_batch - a replay delivers batches"];
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
    (` sv (def[job]`ns),`publish) set publisher;
    .[{.qetl.log.dbg[x;y;z]};(job;"publish seam wired";enlist[`publishes]!enlist def[job]`publishes);::];
    job}

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
    distinct (),(d`subscribe_to),$[`restore_from in key d; d`restore_from; `symbol$()]}

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
        r:@[{[tr;tbls;h;replay] tr[`subscribe][tbls;h;replay]; (1b;::)}[tr;tbls;guarded[job;d`on_batch]];
            replay;{[e] (0b;e)}];
        pub set live;
        `.qetl.job.stream.replaying set 0b;
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
    .[{.qetl.log.info[x;y;z]};(job;"streaming job wired - running";
        `subscribe_to`publishes!(tbls;d`publishes));::];
    job}

unwired:{[job]
    {[job;t;x] '"publish: ",string[job]," is not wired - the runner (or a test) must call .qetl.job.stream.wire first"}[job]}

\d .
