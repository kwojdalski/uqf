/ stream_job.q - the contract a continuous (streaming) job declares, and the
/ seam its process runs it through (.qstream).
/ .
/ The bounded half of this tree has had a shell since #124: a worker declares
/ its source, dataset, width and transform, and .qbw runs it. The continuous
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

\d .qstream

/ job -> its declaration, ENLISTED. Keyed by the job's own name, which is
/ also the segment of its namespace: `markout` is `.qsub.markout`.
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
/ publisher is to bind the handle into one - `.qpipe.publish[h;;]` - and
/ that is a projection (104h), as is a test's recorder bound to a job name.
/ Refusing those would mean the seam only accepted the one shape nobody
/ writes. 100-112h covers lambdas, operators, projections, compositions and
/ q's own iterators.
is_callable:{[v] (type v) within 100 112h}

/ The namespace every job instance lives under, as .qsub.<job>.
/ .
/ Separate from this framework's own `.qstream` for the reason `.qwrk` is
/ separate from `.qbw`: a job called `jobs` or `define` nested inside the
/ framework would overwrite it.
job_root:`.qsub

/ The namespace a job's implementation lives in.
/ @param job the job's name
/ @return the namespace symbol, e.g. `.qsub.markout
/ @eg .qstream.namespace `markout  ->  `.qsub.markout
namespace:{[job] ` sv job_root,job}

/ Declare a streaming job. Called by the job's own file as it loads, so a
/ declaration and its implementation cannot drift - there is no way to have
/ one without the other.
/ @param job the job's name, e.g. `markout
/ @param decl dict of procname, subscribe_to, publishes, and then on_batch
/   (required when it subscribes), period and on_timer (a pair); optionally
/   start_with_all (a boolean, default 0b) and note (a string)
/ @return the job name
/ @throws error naming every missing or malformed field at once
define:{[job;decl]
    if[not 99h=type decl; '"define: ",string[job],"'s declaration must be a dictionary"];
    decl[`ns]:namespace job;
    missing:required_declarations where not required_declarations in key decl;
    if[count missing;
        '"define: ",string[job]," is missing ",", " sv string missing];
    if[not -11h=type decl`procname;
        '"define: ",string[job],"'s procname must be a symbol naming the TorQ process that runs it, e.g. `markout1"];
    if[(decl`procname) in key procnames;
        '"define: ",string[job]," claims procname ",string[decl`procname]," which ",(string procnames decl`procname)," already runs - one process runs one job"];
    if[not 11h=abs type decl`subscribe_to;
        '"define: ",string[job],"'s subscribe_to must be a symbol list of table names"];
    if[not 11h=abs type decl`publishes;
        '"define: ",string[job],"'s publishes must be a symbol list, empty for a job that keeps its output local"];
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
    jobs[job]:enlist decl;
    procnames[decl`procname]:job;
    .[{.qlog.dbg[x;y;z]};(job;"streaming job registered";
        `procname`subscribe_to`publishes`timer!(decl`procname;decl`subscribe_to;decl`publishes;
            $[has_period; decl`period; 0Nn]));::];
    job}

/ One job's declaration, or a refusal naming it.
/ @param job the job's name
/ @return the declaration dict
/ @throws error naming the job when register was never called for it
/ @eg .qstream.def[`markout]`subscribe_to  ->  `trades`quote
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
/ @param procname the TorQ process name, e.g. `markout1
/ @return the job's name
/ @throws error when no registered job claims that process
/ @eg .qstream.for_procname `markout1  ->  `markout
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
    .[{.qlog.dbg[x;y;z]};(job;"publish seam wired";enlist[`publishes]!enlist def[job]`publishes);::];
    job}

/ ------------------------------------------------------------ THE BUFFER

/ Take every row matching mask out of a buffer table and return it, leaving
/ the rest behind - the queue block's drain step. mask is evaluated ONCE by
/ the caller and used for both the read and the delete, so a row arriving
/ between the two cannot be dropped unscored; hand-written drain code that
/ recomputes its cutoff in the delete clause has exactly that race.
/ .
/ Lived in scripts/processes/torq_pipeline.q until the jobs moved into src/, where
/ nothing may call .qpipe. It belongs here anyway: buffering is the
/ job's own business, not TorQ's.
/ @param table_name the buffer table's fully-qualified name, e.g. `.qsub.markout.pending
/ @param mask a boolean vector over that table, as long as it is
/ @return the drained rows, in their original order
/ @eg `.qstream.eg_buffer set ([] a:1 2 3); .qstream.drain[`.qstream.eg_buffer;101b]  ->  ([] a:1 3)
/ @see .qstream.evict - use that instead when a failed publish should retry
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
/ @eg `.qstream.eg_buffer set ([] a:1 2 3); .qstream.evict[`.qstream.eg_buffer;101b]  ->  2i
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
unwired:{[job]
    {[job;t;x] '"publish: ",string[job]," is not wired - the runner (or a test) must call .qstream.wire first"}[job]}

\d .
