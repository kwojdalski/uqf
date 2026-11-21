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

\d .

/ job -> its declaration, ENLISTED. Keyed by the job's own name, which is
/ also the segment of its namespace: `crypto_markout` is `.qpipe.job.crypto_markout`.
/ .
/ The enlist is load-bearing, and the reason is a q trap worth knowing: a
/ dictionary whose values are dictionaries with the SAME keys is a table, and
/ q makes it one silently. The four feeds register first and declare exactly
/ the same fields, so by the fifth registration `jobs` had become a keyed
/ table - and markout's declaration, which carries an on_batch the feeds do
/ not, was refused with a bare 'mismatch naming nothing. Enlisting each
/ declaration keeps the values a general list, so a job may declare whatever
/ its shape needs. `scripts/gates/check.py registry-enlist` holds every
/ registry under src/ to this (#1030).
.qetl.job.stream.jobs:(`symbol$())!();

/ procname -> the job that runs there. A second index rather than a scan:
/ the runner looks itself up by procname on every start, and a scan over
/ declarations to answer it would have to reach inside each one.
.qetl.job.stream.procnames:(`symbol$())!`symbol$();

/ job -> its declared state at define time, ENLISTED as `jobs` is (#967).
.qetl.job.stream.initial:(`symbol$())!();

/ What every job must declare.
/ .
/ `on_batch` is required of a job that SUBSCRIBES and `on_timer` of one that
/ only produces - a feed subscribes to nothing and publishes on a timer, and
/ demanding a batch handler of it would mean writing an empty one. A job
/ with neither is declared but does nothing, which is refused.
/ .
/ `subscribe_to` and `outputs` are the wiring the runner performs on the
/ job's behalf, and what uqs's pipeline_edges checks the Python registry
/ against - so the q file and the registry cannot drift. `procname` (default `<job>1, as
/ for a worker, #2115) is the TorQ process that runs the job: the name a generic
/ process script was started under decides which job it is. `on_batch` is the job itself.
.qetl.job.stream.required_declarations:`subscribe_to`outputs

/ The options of `define` that every job KIND (bars, horizon, rolling,
/ normalizer) accepts and forwards unchanged: job-level facts independent of
/ what the kind computes. `define` validates them once, at declaration time;
/ a kind neither re-validates nor hand-lists them. This is the one list: a
/ kind's optional keys are its own plus these, so a new `define` option is
/ added here and every kind has it (#1232; `check` (#944) once reached only
/ the normalizer and `on_start` (#1112) no kind). tests/q/test_stream_job.q
/ iterates kinds x this list. A kind that must refuse one says so, with the
/ reason, in that test. `ephemeral` is not here: only horizon, which keeps
/ state it does not replay, has a use for it.
.qetl.job.stream.passthrough_keys:`start_with_all`note`on_start`check`on_fail`q

/ The keys `define` reads besides required_declarations, and so the only others
/ it takes (#2112). A key outside both is refused by name: `start_with_al`,
/ `on_endofdya` and `transfrom` once registered without a word and were never
/ read - the job missed the stack, never rolled at end of day, declared no
/ shared transform. `ns` is in neither: define derives it. This is the one list;
/ a key a reader starts to read is added here, and nowhere else.
.qetl.job.stream.optional_declarations:`procname`on_batch`on_timer`period`poll`transform`on_endofday`replay`restore_from`carry`on_replayed`state`ephemeral,.qetl.job.stream.passthrough_keys

/ The passthrough options a declaration carries, to forward to `define`.
/ @param decl a kind instance's declaration dict
/ @return the sub-dictionary of decl on `passthrough_keys`
/ @eg .qetl.job.stream.passthrough `a`note!(1;"n")  ->  enlist[`note]!enlist "n"
.qetl.job.stream.passthrough:{[decl] (.qetl.job.stream.passthrough_keys inter key decl)#decl}

/ The namespace every job instance lives under, as .qpipe.job.<job>.
/ .
/ Separate from this framework's own `.qetl.job.stream` for the reason `.qpipe.job` is
/ separate from `.qetl.job.bounded`: a job called `jobs` or `define` nested inside the
/ framework would overwrite it.
.qetl.job.stream.job_root:`.qpipe.job

/ The namespace a job's implementation lives in.
/ @param job the job's name
/ @return the namespace symbol, e.g. `.qpipe.job.crypto_markout
/ @eg .qetl.job.stream.namespace `crypto_markout  ->  `.qpipe.job.crypto_markout
.qetl.job.stream.namespace:{[job] ` sv .qetl.job.stream.job_root,job}

/ Private: refuse a lambda that takes the wrong number of parameters (#2856).
/ One parameter too many and q hands back a projection instead of running the
/ body - an on_batch that never ran, counted as a batch handled; one too few
/ throws a bare 'rank on every call. q reads the parameter list of a lambda
/ only, so a projection or composition is taken on trust (declared.q); a
/ niladic lambda's list is one null name, so it counts as one.
/ @param job the job's name
/ @param k the key, e.g. `on_batch
/ @param f the declared value, already known to be callable
/ @param n the parameters it must take
/ @param shape what it must be, for the message
/ @return nothing - it throws naming the job, the key and the count
/ @private
.qetl.job.stream.require_arity:{[job;k;f;n;shape]
    if[not .qetl.declared.lambda f; :()];
    if[n=got:count (value f) 1; :()];
    '"define: ",string[job],"'s ",string[k]," takes ",string[got]," parameter(s) - it must be ",shape}

/ Private: refuse an event-time column that is not a timestamp (#2855). A
/ kind (bars, rolling, horizon) cuts its windows or measures its horizon on
/ it, and checked only that the column was there: a float column passed define
/ and then threw on every batch, or dropped or scored every row at once.
/ @param who the error prefix, e.g. "define: bars job exec_bars"
/ @param k the key naming the column, e.g. `event_time
/ @param col the column, known to be in schema
/ @param schema the empty table carrying it (a transform input's schema)
/ @param what what schema is, for the message
/ @return nothing - it throws naming the column and its type
/ @private
.qetl.job.stream.require_timestamp:{[who;k;col;schema;what]
    if["p"=ty:(exec c!t from 0!meta schema) col; :()];
    'who,"'s ",string[k]," ",string[col]," is not a timestamp column of ",what," - it is of type ",$[null ty;"general";enlist ty]}

/ Private: refuse a malformed optional key - deployment facts, transform,
/ check and on_fail. Split out of `define`, whose single body was too big
/ for q to compile once the coverage tool instrumented it ('limit).
/ @param job the job's name
/ @param decl its declaration
/ @return nothing - it throws naming the job and the key
/ @private
.qetl.job.stream.check_optional:{[job;decl]
    / Deployment facts, both optional. uqs derives its process registry
    / from these declarations, so this is where a job says whether it starts
    / with the stack (default: on demand) and why it is deployed as it is.
    if[(`start_with_all in key decl) and not -1h=type decl`start_with_all;
        '"define: ",string[job],"'s start_with_all must be a boolean, 1b to start with the stack"];
    if[(`note in key decl) and not 10h=type decl`note;
        '"define: ",string[job],"'s note must be a string"]; if[`q in key decl; .qetl.declared.require_q["define: ",string job;decl`q]];
    / What the job does when the plant's day ends (#943): roll, snapshot or
    / reset whatever state it carries. Called with the date that ended.
    if[(`on_endofday in key decl) and not .qetl.declared.callable decl`on_endofday;
        '"define: ",string[job],"'s on_endofday must be a function of the date that ended"];
    if[`on_endofday in key decl; .qetl.job.stream.require_arity[job;`on_endofday;decl`on_endofday;1;"a function of the date that ended"]];
    / What the job loads before it runs (#1112): configuration a running
    / process needs and a declaration cannot hold, such as a limits file.
    if[(`on_start in key decl) and not .qetl.declared.callable decl`on_start;
        '"define: ",string[job],"'s on_start must be a niladic function"];
    if[`on_start in key decl; .qetl.job.stream.require_arity[job;`on_start;decl`on_start;1;"a niladic function"]];
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
        if[not .qetl.declared.callable decl`check;
            '"define: ",string[job],"'s check must be a function of the rows about to be published, returning the offending rows"];
        .qetl.job.stream.require_arity[job;`check;decl`check;1;"a function of the rows about to be published"];
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
.qetl.job.stream.check_replay:{[job;decl]
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
    if[`carry in key decl; .qetl.job.stream.check_carry[job;decl;replays]];
    if[`on_replayed in key decl;
        if[not .qetl.declared.callable decl`on_replayed;
            '"define: ",string[job],"'s on_replayed must be a niladic function"];
        .qetl.job.stream.require_arity[job;`on_replayed;decl`on_replayed;1;"a niladic function"];
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
        .qetl.job.stream.initial[job]:enlist (decl`state)!get each ` sv'decl[`ns],/:decl`state;
        / State says how it survives a restart (#1073): rebuilt by replay, or
        / declared ephemeral with the reason losing it is acceptable. Neither
        / was the default - a markout's pending fills, a superbook's books -
        / and nothing asked a new job to choose.
        if[$[`ephemeral in key decl; not (10h=type decl`ephemeral) and 0<count decl`ephemeral; 0b];
            '"define: ",string[job],"'s ephemeral must be a string: why losing its state on a restart is acceptable"];
        if[replays and `ephemeral in key decl;
            '"define: ",string[job]," declares replay 1b and ephemeral - its state is rebuilt, so it is not ephemeral"];
        if[not replays or `ephemeral in key decl;
            '"define: ",string[job]," declares state but not how it recovers - declare replay 1b to rebuild it from the day's log, or ephemeral \"<why losing it on a restart is acceptable>\""]];
    }

/ Private: refuse a name another job already holds, and a procname another job
/ runs. Both execution modes share .qpipe.job, so a name cannot belong to both;
/ and a name declared twice (two job files that picked it) would leave q the
/ last declaration silently, the first job's procname now running the second
/ (#2851). A kind (bars, rolling, horizon) calls this before it writes anything
/ in the job's namespace, so a refused re-declaration of a live job leaves its
/ buffers and its wired publish as they were (#2858).
/ @param who the error prefix, e.g. "define: bars job exec_bars"
/ @param job the job's name
/ @param decl its declaration, a dictionary
/ @return the procname the declaration would run under
/ @throws error naming the job that already holds the name or the procname
/ @private
.qetl.job.stream.refuse_taken:{[who;job;decl]
    if[job in .qetl.job.bounded.defined[];
        'who," is already a bounded job - job names must be unique across execution modes"];
    if[job in key .qetl.job.stream.jobs;
        'who," is already a streaming job, run as ",string[.qetl.job.stream.def[job]`procname],
            " - a job name is declared once: a second declaration would replace the first, and both procnames would run the last"];
    .qetl.job.claim_procname[who;job;decl;`]}

/ Declare a streaming job. Called by the job's own file as it loads, so a
/ declaration and its implementation cannot drift - there is no way to have
/ one without the other.
/ @param job the job's name, e.g. `crypto_markout
/ @param decl dict of procname, subscribe_to, outputs (the tables it publishes onto the tickerplant, empty for none), and then on_batch
/   (required when it subscribes), period and on_timer (a pair) - or period
/   and poll, for a polling feed (see stream_poll.q); optionally
/   start_with_all (a boolean, default 0b), note (a string), transform
/   (the registered transform it applies, which its backfill twin applies
/   too - #884), on_endofday (a function of the date that ended, #943),
/   on_start (a niladic function start calls first, #1112), q (declared.q), and
/   check with on_fail (a quality gate on what is published, #944), and
/   state (a symbol list of the job's private variables, #967: their values
/   as the job's file leaves them are what `reset` restores)
/ @return the job name
/ @throws error naming every missing or malformed field at once
.qetl.job.stream.define:{[job;decl]
    if[not 99h=type decl; '"define: ",string[job],"'s declaration must be a dictionary"];
    .qetl.job.stream.refuse_taken["define: ",string job;job;decl];
    .qetl.job.stream.refuse_publishes["define: ",string job;decl];
    / A supplied ns is refused, not overwritten, as .qetl.job.bounded.define does (#2115)
    if[`ns in key decl;
        '"define: ",string[job],"'s namespace is derived - ",string[.qetl.job.stream.namespace[job]]," - not configured; drop the ns key"];
    .qetl.declared.refuse_unknown["define: ",string job;"a streaming job";decl;
        (.qetl.job.stream.required_declarations),.qetl.job.stream.optional_declarations];
    decl[`ns]:.qetl.job.stream.namespace[job];
    decl[`procname]:.qetl.job.claim_procname["define: ",string job;job;decl;`];
    missing:.qetl.job.stream.required_declarations where not .qetl.job.stream.required_declarations in key decl;
    if[count missing;
        '"define: ",string[job]," is missing ",", " sv string missing];
    if[not 11h=abs type decl`subscribe_to;
        '"define: ",string[job],"'s subscribe_to must be a symbol list of table names"];
    if[not 11h=abs type decl`outputs;
        '"define: ",string[job],"'s outputs must be a symbol list, empty for a job that keeps its output local"];
    / A polling feed declares its steps rather than its timer, and gets the
    / timer built from them (stream_poll.q) - before the checks below, which
    / then see an ordinary feed.
    if[`poll in key decl; decl:.qetl.job.stream.poll_declared[job;decl]];
    if[(`on_batch in key decl) and not .qetl.declared.callable decl`on_batch;
        '"define: ",string[job],"'s on_batch must be a function taking (table name; rows), [t;x] as in TorQ's upd"];
    if[`on_batch in key decl; .qetl.job.stream.require_arity[job;`on_batch;decl`on_batch;2;"a function taking (table name; rows), [t;x] as in TorQ's upd"]];
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
        if[not .qetl.declared.callable decl`on_timer;
            '"define: ",string[job],"'s on_timer must be a niladic function"];
        .qetl.job.stream.require_arity[job;`on_timer;decl`on_timer;1;"a niladic function (or one taking the tick)"]];
    .qetl.job.stream.check_optional[job;decl];
    .qetl.job.stream.check_replay[job;decl]; .qetl.twin.check_live[job;decl];
    / The TorQ plant sends end of day only to the handles that subscribed
    / (pubsub.q's allsubhandles), so a job subscribing to nothing - a feed, a
    / timer-only job - would never have its hook called there, though
    / run_stream calls it in-process (#2898).
    if[(`on_endofday in key decl) and 0=count distinct (),(decl`subscribe_to),$[`restore_from in key decl; decl`restore_from; `symbol$()],$[`carry in key decl; enlist decl[`carry]`table; `symbol$()];
        '"define: ",string[job]," declares on_endofday but subscribes to nothing - the TorQ plant sends end of day only to its subscribers, so the hook would never be called there; do the work on the job's timer instead"];
    decl:.qetl.job.stream.with_defaults decl;  / after the checks above, which tell declared from absent (#2657)
    .qetl.job.stream.jobs[job]:enlist decl;
    .qetl.job.stream.procnames[decl`procname]:job;
    .[{.qetl.log.ch.dbg[`config;y;(enlist[`job]!enlist x),z]};(job;"streaming job registered";
        `procname`subscribe_to`outputs`timer!(decl`procname;decl`subscribe_to;decl`outputs;
            $[has_period; decl`period; 0Nn]));::];
    job}

/ One job's declaration, or a refusal naming it.
/ @param job the job's name
/ @return the declaration dict
/ @throws error naming the job when register was never called for it
/ @eg .qetl.job.stream.def[`crypto_markout]`subscribe_to  ->  `executions`market_data
.qetl.job.stream.def:{[job]
    if[not job in key .qetl.job.stream.jobs;
        '"def: ",string[job]," is not a registered streaming job - a job registers as its own file loads, so this is a wiring bug rather than a lookup miss"];
    first .qetl.job.stream.jobs job}

/ The job a TorQ process runs, by the name the process was started under.
/ .
/ The runner is generic: one script serves every streaming job, and this is
/ how a started process finds out which one it is. A procname nothing claims
/ is an error naming it rather than a process that comes up subscribed to
/ nothing and reports healthy.
/ @param procname the TorQ process name, e.g. `crypto_markout1
/ @return the job's name
/ @throws error when no registered job claims that process
/ @eg .qetl.job.stream.for_procname `crypto_markout1  ->  `crypto_markout
.qetl.job.stream.for_procname:{[procname]
    if[not procname in key .qetl.job.stream.procnames;
        '"for_procname: no streaming job runs as ",string[procname]," - registered processes: ",", " sv string key .qetl.job.stream.procnames];
    .qetl.job.stream.procnames procname}

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
.qetl.job.stream.reset:{[job]
    .qetl.job.stream.def job;
    if[job in key .qetl.job.stream.initial;
        s:first .qetl.job.stream.initial job;
        {[ns;k;v] (` sv ns,k) set v}[.qetl.job.stream.namespace[job]]'[key s;value s]];
    / and what its carry knows of that state (#1015)
    .qetl.job.stream.carried_to::(enlist job) _ .qetl.job.stream.carried_to;
    .qetl.job.stream.carry_log::(enlist job) _ .qetl.job.stream.carry_log;
    .qetl.job.stream.caught_up::(enlist job) _ .qetl.job.stream.caught_up;
    job}

/ Every registered job, for the runner and for tests.
/ @return symbol list of job names
.qetl.job.stream.defined:{[] key .qetl.job.stream.jobs}

/ Point a job's `publish` at something that can actually publish.
/ .
/ The one call that turns a declaration into a running job. The runner passes
/ a tickerplant publisher; a test passes a recorder and then reads what the
/ job would have published. Until this is called the job's own stub throws,
/ so "nobody wired it" is an error rather than rows quietly going nowhere.
/ @param job the job's name
/ @param publisher a function of (table name; rows)
/ @return the job name
.qetl.job.stream.wire:{[job;publisher]
    if[not .qetl.declared.callable publisher;
        '"wire: ",string[job],"'s publisher must be callable as (table; rows) - a lambda or a projection over one"];
    d:.qetl.job.stream.def[job];
    / A declared check sits in the seam itself, so every route a row takes out
    / of the job - batch handler, timer, poll, on_replayed - passes through it.
    / The plant's contract sits under the check, at the transport (#1074).
    p:.qetl.job.stream.contracted[job;(),(d`outputs) inter .qetl.plant.names[];publisher];
    (` sv (d`ns),`publish) set $[`check in key d; .qetl.job.stream.checked[job;d`check;d`on_fail;p]; p];
    .[{.qetl.log.ch.dbg[`config;y;(enlist[`job]!enlist x),z]};(job;"publish seam wired";enlist[`outputs]!enlist d`outputs);::];
    job}

/ Private: a publisher that holds every batch for a plant table to that
/ table's contract (#1074) - .qetl.plant.problems, its columns' names, order
/ and types and every nested value, the check a polling feed's page already
/ passes. A tickerplant takes column vectors POSITIONALLY, so a batch with
/ two same-typed columns swapped, or a string where a symbol list belongs, is
/ stored wrong and never refused: this refuses it before it is sent. A
/ refused batch is withheld whole - logged, and counted in stream_health -
/ like one a job's own check fails. A table the plant does not carry is the
/ job's own business and passes as it is.
/ @private
.qetl.job.stream.contracted:{[job;plant;publisher;t;x]
    if[not t in plant; :publisher[t;x]];
    if[count f:.qetl.job.stream.contract_problems[t;x];
        :.qetl.job.stream.withhold[job;t;x;"plant contract: ","; " sv f;();.qetl.job.stream.row_count[t;x]]];
    publisher[t;x]}

/ Private: what is wrong with `x` as a batch for plant table `t`: a table,
/ a dict - one row of atoms, or column -> vector - or a list of column
/ vectors in the plant's order, the shapes a publisher takes. A list is held
/ to the types its positions name, which is all it can be held to.
/ @private
.qetl.job.stream.contract_problems:{[t;x]
    c:cols .qetl.plant.published t;
    if[(0h=type x) and not count[x]=count c;
        :enlist string[t]," was given ",string[count x]," column vector(s) - the plant takes ",string count c];
    if[not (.Q.qt x) or type[x] in 0 99h;
        :enlist string[t],"'s rows must be a table, a dict or a list of column vectors"];
    / Trapped: a ragged list or dict cannot be made a table, and says so. A
    / `time` is dropped, as every publisher drops it: the plant stamps its own.
    @[{[t;c;x] r:.qetl.job.stream.as_rows[c;x];
            .qetl.plant.problems[t] (cols[r] except `time)#r}[t;c];x;{[e] enlist "could not be checked: ",e}]}

/ Private: a batch in any shape a publisher takes, as a table (#1164): a table
/ as it is, a dict of atoms as one row, a dict of vectors flipped to N rows, a
/ list of column vectors flipped under the names in `c`. The one reading of
/ "the rows of this batch"; throws on a ragged dict or list.
/ @private
.qetl.job.stream.as_rows:{[c;x] 0!$[.Q.qt x; x; 99h=type x; $[all 0>type each value x; enlist x; flip x]; flip c!x]}

/ Private: how many rows a batch holds, whatever its shape (#1164). A batch
/ that cannot be made a table (ragged) counts as 1: it is withheld whole.
/ @private
.qetl.job.stream.row_count:{[t;x]
    c:$[t in .qetl.plant.names[]; cols .qetl.plant.published t; `$"c",/:string til count x];
    @[{[c;x] count .qetl.job.stream.as_rows[c;x]}[c];x;{[e] 1}]}

/ Private: a publisher that runs the job's declared check first (#944).
/ .
/ The check gets the rows about to be published and returns a table of
/ offending rows, the bounded worker's failure shape (check, status, detail -
/ .qetl.job.bounded.no_failures) with one optional column: `row`, the index in
/ the batch of the row that offends. Under `drop` the rows named by `row` are
/ withheld and the rest published; a failure that names no row or a row
/ outside the batch, and every failure under `hold`, withholds the whole
/ batch - never publishing a row that might be the bad one.
/ .
/ A check that THROWS is a failure of the whole batch under either choice:
/ rows nobody could vouch for are not published. Every withheld batch is
/ logged with its failures and counted in .qetl.stream_health, which is what
/ shows the job as failing in `uqs status`.
/ @private
.qetl.job.stream.checked:{[job;chk;mode;publisher;t;x]
    r:@[{[chk;x] (1b;chk x)}[chk];x;{[e] (0b;e)}];
    if[not first r;
        :.qetl.job.stream.withhold[job;t;x;"check threw: ",last r;();.qetl.job.stream.row_count[t;x]]];
    f:last r;
    if[not .Q.qt f;
        :.qetl.job.stream.withhold[job;t;x;"check returned something other than a table of failures";();.qetl.job.stream.row_count[t;x]]];
    if[0=count f; :publisher[t;x]];
    / Only a table has rows a check can name by index and a publisher can be
    / handed a subset of. A dict row or a list of column vectors that fails is
    / withheld whole, under drop as under hold: no clean subset is rebuilt in
    / a shape the check never indexed.
    tbl:98h=type x;
    n:.qetl.job.stream.row_count[t;x];
    / `row is read only once it is known to be there: `and evaluates both
    / sides, and indexing a missing column throws on PeachQ
    named:$[tbl and (`drop=mode) and `row in cols f; not any null f`row; 0b];
    rows:$[named; distinct "j"$f`row; til n];
    / a row outside the batch is no row of it: the check meant one that cannot be
    / told, so the whole batch is withheld, as for a failure naming none (#2860)
    outside:$[named; any not rows within (0;n-1); 0b];
    if[outside; rows:til n];
    clean:$[tbl; x where not (til n) in rows; ()];
    .qetl.job.stream.withhold[job;t;x;"check failed: ",string[count f]," failure(s)",$[outside;", naming a row outside the batch - withheld whole";""];f;count rows];
    $[tbl and count clean; publisher[t;clean]; ::]}

/ Private: log and count a batch the check withheld, in whole or part.
/ @private
.qetl.job.stream.withhold:{[job;t;x;msg;f;n]
    .[.qetl.stream_health.record;(job;0b;msg);::];
    .[{.qetl.log.ch.err[`transform;y;(enlist[`job]!enlist x),z]};(job;"publish check withheld rows";
        `table`rows`withheld`failures!(t;.qetl.job.stream.row_count[t;x];n;$[.Q.qt f;300 sublist .Q.s1 5 sublist f;""]));::];
    .qetl.job.stream.withheld_n::.qetl.job.stream.withheld_n+1;
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
/ @param table_name the buffer table's fully-qualified name, e.g. `.qpipe.job.crypto_markout.pending
/ @param mask a boolean vector over that table, as long as it is
/ @return the drained rows, in their original order
/ @eg `.qetl.job.stream.eg_buffer set ([] a:1 2 3); .qetl.job.stream.drain[`.qetl.job.stream.eg_buffer;101b]  ->  ([] a:1 3)
/ @see .qetl.job.stream.evict - use that instead when a failed publish should retry
/   the batch rather than lose it (drain is at-most-once, evict at-least-once)
.qetl.job.stream.drain:{[table_name;mask]
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
.qetl.job.stream.evict:{[table_name;mask]
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
.qetl.job.stream.replaying:0b

/ The log clock: the time a stream decision uses for each row of a batch (#994).
/ .
/ It is the plant's receipt stamp `time`, which the tickerplant wrote on the row
/ when it first saw it - live and on replay alike, because it is the one fact
/ the log records (#2459). Live, the job's own clock when it dequeues the row is
/ later whenever it is behind its queue or the plant publishes on a timer, and
/ replay cannot know it: judging a row on it dropped rows live that a restart
/ then accepted. A decision made on this clock comes out the same live and on
/ replay, which is what makes a restart a pure function of the log. A batch
/ with no `time` uses the process clock live, and on replay has no log clock:
/ null per row, which no comparison satisfies, so a time-based rule is simply
/ not applied to it - say so where the rule is documented.
/ .
/ Routed through here: the bars kind's lateness and the rolling kind's. NOT routed, because the log
/ cannot say what time it was: a timer tick (bars' close_ready, horizon's
/ score_ready, fx_positions' throttle, superbook, alert_sink, cross) - a tick
/ is not a logged row, so its `now` is always the process clock and it never
/ runs during a replay.
/ @param now the process clock (a job passes its own `now`, so a test can move it)
/ @param x the batch, a table
/ @return a timestamp per row of x
/ @eg .qetl.job.stream.clock_of[2026.10.09D10:00:00;([] a:1 2)]  ->  2026.10.09D10:00:00 2026.10.09D10:00:00
/ @eg .qetl.job.stream.clock_of[2026.10.09D10:00:09;([] time:2026.10.09D10:00:01 2026.10.09D10:00:02)]  ->  2026.10.09D10:00:01 2026.10.09D10:00:02
.qetl.job.stream.clock_of:{[now;x]
    $[`time in cols x; x`time; .qetl.job.stream.replaying; count[x]#0Np; count[x]#now]}

/ The log clock with the process clock for `now`.
/ @param x the batch, a table
/ @return a timestamp per row of x
/ @eg count .qetl.job.stream.clock ([] a:1 2)  ->  2
.qetl.job.stream.clock:{[x] .qetl.job.stream.clock_of[.z.p;x]}

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
/ .
/ A carrying job's recovery (stream_carry_recovery.q) uses three more keys,
/ the recovery_keys: earlier_days[tables; first; last], plant_day[] (the date
/ of the log subscribe replays - the plant's day, never the job's clock,
/ #1190) and has_log[day] (whether that day's log exists, #1189). They are a
/ contract (#1220), judged by `start` once connected, for a job with carry and
/ replay: all three, or the transport states `recovery` as 0b to say it
/ cannot recover; some but not all is refused, and none is an ERROR.
.qetl.job.stream.transport_keys:`connect`publisher`subscribe`timer

/ Every table a job subscribes to: its inputs, and those it reads only to
/ restore state. The second are not inputs - .qetl.dag reads subscribe_to
/ alone, so a job restoring from its own output does not draw a cycle.
/ @param job the job's name
/ @return the tables, as a symbol list
.qetl.job.stream.subscriptions:{[job]
    d:.qetl.job.stream.def[job];
    distinct (),(d`subscribe_to),(d`restore_from),
        $[`carry in key d; enlist d[`carry]`table; `symbol$()]}

/ Private: the job's on_batch, counting every batch in .qetl.stream_health
/ and logging a failure with its table before re-raising it. Re-raised, the
/ error still reaches the caller; counted, a job that drops every batch is
/ a gap in `uqs job gaps` and `failing` in `uqs status` rather than a process
/ that looks healthy (#832).
/ @private
.qetl.job.stream.guarded:{[job;f;t;x]
    r:.[f;(t;x);{[job;t;e]
        .[.qetl.stream_health.record;(job;0b;e);::];
        .[{.qetl.log.ch.err[`lifecycle;y;(enlist[`job]!enlist x),z]};(job;"on_batch failed";`table`error!(t;e));::];
        'e}[job;t]];
    .[.qetl.stream_health.record;(job;1b;"");::];
    r}

/ Start a job on a transport: the one sequence every runner uses.
/ .
/ IN THIS ORDER, and the order is the point.
/   0. on_start, before anything is opened: a job that cannot load what it
/      needs (a malformed limits file) fails to start rather than run blind.
/   1. connect.
/   2. Wire publish - BEFORE subscribing, so a replay can reach it.
/   3. Subscribe, which installs the handler first. With `replay` 1b the
/      day's log is delivered before any live batch, with publish MUTED and
/      `replaying` set: a replay rebuilds state; it must not publish again
/      what was published before the restart.
/   4. on_replayed, with publish live again, for what the replay left owed.
/   5. Uptime: a session in .qetl.uptime, beaten on a timer - the record of
/      when the job was up and subscribed, which `uqs job gaps` reads (#630).
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
.qetl.job.stream.start:{[job;tr]
    missing:.qetl.job.stream.transport_keys where not .qetl.job.stream.transport_keys in key tr;
    if[count missing; '"start: the transport is missing ",", " sv string missing];
    d:.qetl.job.stream.def[job];
    replay:d`replay;
    tbls:.qetl.job.stream.subscriptions[job];
    .[{.qetl.log.ch.info[x;y;z]};(`lifecycle;"starting streaming job";
        `job`subscribe_to`outputs`replay`timer!(job;tbls;d`outputs;replay;
            $[`period in key d; d`period; 0Nn]));::];
    if[`on_start in key d; (d`on_start)[]];
    tr[`connect][];
    .qetl.job.stream.rolling.roll_hooks[job]:enlist $[`plant_roll in key tr; tr`plant_roll; {[] 0Np}];
    .qetl.job.stream.require_recovery[job;tr];
    if[count d`outputs;
        if[`check_publishable in key tr; tr[`check_publishable] d`outputs];
        .qetl.job.stream.wire[job;tr[`publisher][]]];
    if[count tbls;
        / muted only where there is one: a job that keeps its output local (no
        / outputs) need not define a publish at all (#2899)
        mute:replay and `publish in key d`ns;
        if[mute; live:get pub:` sv (d`ns),`publish; pub set {[t;x] count x}]; / noqa: QF016 publish signature, replay discards
        / a batch that fails to apply while replaying is quarantined, not fatal (#2195)
        if[replay; `.qetl.job.stream.replaying set 1b; .qetl.job.stream.catching_up[job;1b]];
        today:.qetl.job.stream.plant_today tr;
        r:@[{[job;tr;tbls;h;replay]
                if[replay and `carry in key .qetl.job.stream.def job; .qetl.job.stream.catch_up[job;tr;tbls;h]];
                tr[`subscribe][tbls;h;replay]; (1b;::)}[job;tr;tbls;.qetl.job.stream.tolerant[job;.qetl.job.stream.handler[job]]];
            replay;{[e] (0b;e)}];
        if[mute; pub set live]; `.qetl.job.stream.replaying set 0b; .qetl.job.stream.catching_up[job;0b];
        if[replay and `carry in key d; .qetl.job.stream.missed_end_of_day[job;today]];
        .qetl.job.stream.replayed[job];
        if[not first r; 'last r];
        if[`on_replayed in key d; (d`on_replayed)[]];
        / Protected: a record that cannot be written must never stop the job
        / it records. Logged, so a missing session is explained.
        .[{[tr;job] .qetl.uptime.begin job;
            tr[`timer][`$(string job),"_uptime";.qetl.uptime.period;{[job;tr;unused] .qetl.job.stream.watch_subscription[job;tr]; @[.qetl.uptime.beat;::;{[e] .qetl.log.ch.warn[`lifecycle;"could not record a beat";enlist[`error]!enlist e];}]}[job;tr]]};
          (tr;job);
          {[job;e] .qetl.log.ch.warn[`lifecycle;"could not open an uptime session - its gaps will read as down";(enlist[`job]!enlist job),enlist[`error]!enlist e];}[job]]];
    if[`period in key d; tr[`timer][job;d`period;d`on_timer]];
    if[count .qetl.cfg.audit.watching job;
        `.qetl.cfg.audit.owner_here set job;
        tr[`timer][`$(string job),"_config";.qetl.cfg.audit.period;.qetl.cfg.audit.poll_and_publish]];
    `.qetl.job.stream.running set distinct .qetl.job.stream.running,job;
    .[{.qetl.log.ch.info[x;y;z]};(`lifecycle;"streaming job wired - running";
        `job`subscribe_to`outputs!(job;tbls;d`outputs));::];
    job}

/ The jobs this process has started, in the order it started them: whom
/ end_of_day tells.
.qetl.job.stream.running:`symbol$()

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
.qetl.job.stream.end_of_day:{[dt]
    / carried state first (#963): its snapshot opens the new day's log
    cs:.qetl.job.stream.running where {[j] `carry in key .qetl.job.stream.def j} each .qetl.job.stream.running;
    / and, only for a snapshot that was published, the day it saw end (#1072, #1307)
    .qetl.job.stream.carry_end_of_day[dt] each cs;
    js:.qetl.job.stream.running where {[j] `on_endofday in key .qetl.job.stream.def j} each .qetl.job.stream.running;
    ok:{[dt;j] @[{[dt;j] (.qetl.job.stream.def[j]`on_endofday) dt; 1b}[dt];j;
        {[dt;j;e] .qetl.log.ch.err[`lifecycle;"on_endofday failed";(enlist[`job]!enlist j),`date`error!(dt;e)]; 0b}[dt;j]]}[dt] each js;
    js where ok}

.qetl.job.stream.unwired:{[job]
    {[job;t;x] '"publish: ",string[job]," is not wired - the runner (or a test) must call .qetl.job.stream.wire first"}[job]} / noqa: QF016 publish signature, refuses

\d .
