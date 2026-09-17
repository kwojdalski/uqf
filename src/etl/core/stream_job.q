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
/ declaration below. One runner (scripts/torq_stream.q) runs any of them.
/ .
/ WHAT MAKES THAT POSSIBLE is the publish seam. A job never calls TorQ: it
/ calls `publish` in its OWN namespace, which is a stub that throws until
/ something wires it. The runner wires it to the tickerplant; a test wires it
/ to a recorder and gets the job's output as data. So the job file can be
/ loaded by src/etl/init.q in a plain q process with no TorQ present, which
/ is what keeps its functions in docs/man.q and under test - the reason the
/ computations were moved out to src/ in the first place (B-09: nothing in
/ src/etl/ may depend on TorQ).

\d .qstream

/ job -> its declaration. Keyed by the job's own name, which is also the
/ segment of its namespace: `markout` is `.qsub.markout`.
jobs:(`symbol$())!();

/ What every job must declare.
/ .
/ `subscribes` and `publishes` are the wiring the runner performs on the
/ job's behalf, and they are also what torq_orchestrator's pipeline_edges
/ checks the Python registry against - so the q file and the registry cannot
/ drift. `procname` is the TorQ process that runs this job, and is how the
/ runner knows which job it is: one generic process script, and the name it
/ was started under decides. `on_batch` is the job itself.
required_declarations:`ns`procname`subscribes`publishes`on_batch

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
/ separate from `.qbw`: a job called `jobs` or `register` nested inside the
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
/ @param decl dict of subscribes, publishes, on_batch, and optionally
/   timer_period and on_timer
/ @return the job name
/ @throws error naming every missing or malformed field at once
register:{[job;decl]
    if[not 99h=type decl; '"register: ",string[job],"'s declaration must be a dictionary"];
    decl[`ns]:namespace job;
    missing:required_declarations where not required_declarations in key decl;
    if[count missing;
        '"register: ",string[job]," is missing ",", " sv string missing];
    if[not -11h=type decl`procname;
        '"register: ",string[job],"'s procname must be a symbol naming the TorQ process that runs it, e.g. `markout1"];
    clash:(key jobs) where decl[`procname]=(jobs@/:key jobs)@\:`procname;
    if[count clash;
        '"register: ",string[job]," claims procname ",string[decl`procname]," which ",(string first clash)," already runs - one process runs one job"];
    if[not 11h=abs type decl`subscribes;
        '"register: ",string[job],"'s subscribes must be a symbol list of table names"];
    if[not 11h=abs type decl`publishes;
        '"register: ",string[job],"'s publishes must be a symbol list, empty for a job that keeps its output local"];
    if[not is_callable decl`on_batch;
        '"register: ",string[job],"'s on_batch must be a function taking (table name; batch)"];
    / A timer is optional, but half a timer is a job whose scoring never runs
    / while every test still passes - so the pair is checked together.
    has_period:`timer_period in key decl;
    has_body:`on_timer in key decl;
    if[has_period<>has_body;
        '"register: ",string[job]," declares ",$[has_period;"timer_period without on_timer";"on_timer without timer_period"]," - a timer is both or neither"];
    if[has_period;
        if[not 16h=abs type decl`timer_period;
            '"register: ",string[job],"'s timer_period must be a timespan, e.g. 0D00:00:01"];
        if[not (decl`timer_period)>0D00:00;
            '"register: ",string[job],"'s timer_period must be positive"];
        if[not is_callable decl`on_timer;
            '"register: ",string[job],"'s on_timer must be a niladic function"]];
    jobs[job]:decl;
    job}

/ One job's declaration, or a refusal naming it.
/ @param job the job's name
/ @return the declaration dict
/ @throws error naming the job when register was never called for it
/ @eg .qstream.declaration[`markout]`subscribes  ->  `trades`quote
declaration:{[job]
    if[not job in key jobs;
        '"declaration: ",string[job]," is not a registered streaming job - a job registers as its own file loads, so this is a wiring bug rather than a lookup miss"];
    jobs job}

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
    match:(key jobs) where procname=(jobs@/:key jobs)@\:`procname;
    if[0=count match;
        '"for_procname: no streaming job runs as ",string[procname]," - registered processes: ",", " sv string (jobs@/:key jobs)@\:`procname];
    first match}

/ Every registered job, for the runner and for tests.
/ @return symbol list of job names
registered:{[] key jobs}

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
    (` sv (declaration[job]`ns),`publish) set publisher;
    job}

/ ------------------------------------------------------------ THE BUFFER

/ Take every row matching mask out of a buffer table and return it, leaving
/ the rest behind - the queue block's drain step. mask is evaluated ONCE by
/ the caller and used for both the read and the delete, so a row arriving
/ between the two cannot be dropped unscored; hand-written drain code that
/ recomputes its cutoff in the delete clause has exactly that race.
/ .
/ Lived in scripts/torq_pipeline.q until the jobs moved into src/, where
/ nothing may call .qpipe (B-09). It belongs here anyway: buffering is the
/ job's own business, not TorQ's.
/ @param tblname the buffer table's fully-qualified name, e.g. `.qsub.markout.pending
/ @param mask a boolean vector over that table, as long as it is
/ @return the drained rows, in their original order
/ @eg `.qstream.eg_buffer set ([] a:1 2 3); .qstream.drain[`.qstream.eg_buffer;101b]  ->  ([] a:1 3)
/ @see .qstream.evict - use that instead when a failed publish should retry
/   the batch rather than lose it (drain is at-most-once, evict at-least-once)
drain:{[tblname;mask]
    buffer:get tblname;
    if[0=count buffer; :buffer];
    ready:buffer where mask;
    tblname set buffer where not mask;
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
/ @param tblname the buffer table's fully-qualified name
/ @param mask the boolean vector already used to read the batch
/ @return the number of rows removed
/ @eg `.qstream.eg_buffer set ([] a:1 2 3); .qstream.evict[`.qstream.eg_buffer;101b]  ->  2i
evict:{[tblname;mask]
    buffer:get tblname;
    if[0=count buffer; :0];
    tblname set buffer where not mask;
    sum mask}

/ The stub every job's `publish` starts as.
/ .
/ Throws rather than doing nothing: a job whose rows vanish because nothing
/ wired it looks exactly like a job with no rows to publish, and that is the
/ failure this tree keeps finding.
/ @param job the job's name, for the message
/ @return a function that throws when called
unwired:{[job]
    {[job;tbl;rows] '"publish: ",string[job]," is not wired - the runner (or a test) must call .qstream.wire first"}[job]}

\d .
