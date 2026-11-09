/ job_facts.q - the deployment facts every job declaration takes the same way,
/ whichever shell or kind declares it (#2115, #2116).
/ .
/ procname, the TorQ process that runs a job: a symbol defaulting to <job>1,
/ held by at most one declared job across the bounded and streaming registries,
/ checked at declaration. lateness, how late a row may arrive in an event-time
/ kind (bars, rolling, horizon): one name, one default rule.
/ .
/ Loaded before bounded_worker.q, which calls the procname functions at
/ define time; they read the two registries only when called, so neither
/ shell has to be loaded first.

\d .

/ The process a job runs in when its declaration does not name one: `<name>1.
/ Both shells default it, so the same declaration means the same in either
/ (#2115). Kinds that build a streaming declaration call this too, so they
/ forward the name define would have resolved.
/ @param name the job's name
/ @return the default procname, e.g. `demo_markout1
/ @eg .qetl.job.default_procname `demo_markout  ->  `demo_markout1
.qetl.job.default_procname:{[name] `$string[name],"1"}

/ The procname a declaration runs under: the one it names, else the default.
/ @param name the job's name
/ @param decl the declaration dict
/ @return the declared procname, or the default when none is declared
/ @eg .qetl.job.procname_of[`demo_markout;()!()]  ->  `demo_markout1
.qetl.job.procname_of:{[name;decl]
    $[`procname in key decl; decl`procname; .qetl.job.default_procname name]}

/ Resolve a job's procname and refuse it when another job already runs there.
/ One rule for both shells (#2115): a symbol, defaulting to `<name>1`, held by
/ at most one declared job ACROSS the bounded and streaming registries - the
/ uqs process registry merges them, so a clash between a worker and a feed
/ is the same mistake as one between two workers. Checked at declaration
/ rather than only when a process inits (claim_process stays as the runtime
/ guard). A bounded worker redeclared under its own name is not a clash with
/ itself; a streaming job never is redeclared, so `self` is null for it.
/ @param who the error prefix, e.g. "define: demo_markout"
/ @param name the job's name
/ @param decl the declaration dict
/ @param self the bounded worker allowed to already hold it, or ` for none
/ @return the resolved procname
/ @throws error when procname is not a symbol, or another job already runs there
/ @eg .qetl.job.claim_procname["define: demo_markout";`demo_markout;()!();`]  ->  `demo_markout1
.qetl.job.claim_procname:{[who;name;decl;self]
    proc:.qetl.job.procname_of[name;decl];
    if[not -11h=type proc;
        'who,"'s procname must be a symbol naming the process that runs it, e.g. `",string .qetl.job.default_procname name];
    live:@[value;`.qetl.job.stream.procnames;{[e] (`symbol$())!`symbol$()}];
    held:$[proc in key live; live proc; `];
    if[null held;
        other:exec name from .qetl.job.bounded.worker_cfg where procname=proc, not name=self;
        if[count other; held:first other]];
    if[not null held;
        'who," claims procname ",string[proc]," which ",string[held]," already runs - one process runs one job"];
    proc}

/ The lateness bound of an event-time job kind (bars, rolling, horizon), under
/ ONE name and ONE default rule (#2116). `lateness` is how late a row may arrive
/ and still count: rolling and horizon measure it from the row's event_time
/ (receipt - event_time), bars from its window's end (receipt >= bar_start +
/ width + lateness, because a bar is out when its window closes, not when one
/ of its rows ages). Only the key and its default are shared.
/ .
/ When event_time is the plant's own `time there is no distinct receipt stamp, so
/ no row can be late by the clock and the bound is 0D: inferred, since it loses
/ nothing. Each kind holds to that on `time by not judging a row by the job's
/ clock - rolling skips the test, and bars (#2423) refuses a row only when its
/ window is already out - never because the plant stamped it just before a
/ boundary. When
/ event_time is a source stamp the bound is a retention decision (a horizon job
/ keeps that much more reference history) that no default should make silently,
/ so it is REQUIRED - the kind's missing-key check refuses its absence. The old
/ horizon spelling max_lateness is refused with the new name.
/ @param who the error prefix, e.g. "define: bars job exec_bars"
/ @param decl the kind's declaration dict
/ @return decl with lateness 0D filled in when event_time is `time and none is declared
/ @throws error when the declaration still uses max_lateness
/ @eg (.qetl.job.stream.with_lateness["define: bars job x";()!()])`lateness  ->  0D
/ @private
.qetl.job.stream.with_lateness:{[who;decl]
    if[`max_lateness in key decl;
        'who," declares max_lateness, which is now lateness - one name for the bound in bars, rolling and horizon jobs"];
    et:$[`event_time in key decl; decl`event_time; `time];
    if[(not `lateness in key decl) and et~`time; decl:decl,enlist[`lateness]!enlist 0D];
    decl}
