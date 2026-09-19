/ config_audit.q - an audit trail of runtime configuration changes
/ (.qcfgaudit).
/ .
/ WHAT THIS CANNOT BE, and why the shape follows from it: q has no hook on
/ global assignment. There is no .z callback for
/ `.qsub.cross_arbitrage.notional:5000000`, and a view (x::expr) recomputes
/ lazily when READ rather than firing when its inputs change. So nothing
/ can observe a change as it happens. The only thing that cannot be
/ bypassed is to look, periodically, and compare - which is what this does.
/ .
/ A setter (.qcfg.set, logging as it writes) would be airtight for anything
/ that used it and useless for a plain assignment - and this stack is
/ operated by ad-hoc IPC, where a plain assignment is the common case. So a
/ setter would audit exactly the changes made by people who did not need
/ auditing.
/ .
/ THE OTHER HALF IS ALREADY ON DISK. TorQ's logusage.q records every
/ incoming IPC command per process with .z.u, .z.a, the handle and the
/ command text. That is the ACTOR. This is the VALUE. Joining a
/ config_change row to the usage log at the same timestamp is how "who
/ changed the notional" gets answered; neither half can answer it alone.
/ .
/ WATCHED, NOT SCANNED. Not everything in a namespace is configuration -
/ .qsub.cross_arbitrage.books is state, and large - and a snapshot of a
/ whole namespace every tick would be both wrong and expensive. Naming the
/ variables makes "what counts as configuration here" a fact in the tree
/ rather than a judgement each reader makes again.

\d .qcfgaudit

/ owner -> the fully-qualified globals watched on its behalf. Keyed by
/ OWNER, not flat, because src/etl/init.q loads every job into every
/ streaming process: a flat registry would make each process poll - and
/ report on - the config of fourteen jobs it is not running.
watched:(`symbol$())!()

/ fully-qualified name -> its last observed rendering. The whole memory of
/ this module: a change is a disagreement with this.
seen:(`symbol$())!()

/ A row per observed change, as its consumers see it. `old` is empty on the
/ FIRST observation of a name, which is deliberate - that row says what the
/ process started with, and a log that only records later edits cannot tell
/ you what they were edits from.
config_change:([] owner:`symbol$(); name:`symbol$(); old:(); new:(); as_of:`timestamp$())

/ The globals watched for one owner, empty when it declares none.
/ @param owner the job
/ @return the fully-qualified names
/ @eg .qcfgaudit.watching[`nothing_declares_this] -> `symbol$()
watching:{[owner] $[owner in key watched; watched owner; `symbol$()]}

/ Declare the configuration an owner wants audited.
/ .
/ Called at load time from the file that owns the variables, beside their
/ definitions, so the declaration cannot drift away from the thing it
/ describes.
/ @param owner the job (or other unit) these belong to
/ @param names fully-qualified globals, e.g. `.qsub.cross_arbitrage.notional
/ @return the names registered for that owner
/ @throws error if a name is not fully qualified, which would resolve
/   against whatever namespace happened to be current at poll time
/ @eg .qcfgaudit.watch[`demo;`.qcfgaudit.max_render] -> enlist `.qcfgaudit.max_render
watch:{[owner;names]
    names:(),names;
    bare:names where not (string names) like ".*";
    if[count bare;
        '"qcfgaudit.watch: ",(", " sv string bare)," must be fully qualified ",
            "(`.qsub.x.notional, not `notional) - a bare name resolves against ",
            "whatever namespace is current when the poll runs"];
    / Fully qualified on the LEFT. `watched[owner]:x` inside a lambda amends
    / a LOCAL named watched, silently, and the registry outside stays empty -
    / which would make every poll return nothing and look like "no changes".
    / `already`, not `prior`: `prior` is a q keyword, and assigning to one
    / inside a lambda fails at LOAD time - the same trap torq_pipeline.q
    / documents for `desc` and `tables`.
    already:watching owner;
    .qcfgaudit.watched[owner]:distinct already,names;
    watched owner}

/ How a value is recorded: its -3! rendering, truncated.
/ .
/ A STRING, not the value. One column has to hold a timespan, a float, a
/ symbol list and a dictionary, and -3! renders all of them unambiguously
/ and reversibly enough to read. Truncated because a watched name that
/ turns out to be a large table should make the log ugly, not enormous.
/ @param name a fully-qualified global
/ @return its rendering, or a marker when nothing is defined at that name
/ @eg .qcfgaudit.render[`.qcfgaudit.nothing.is.here] -> "(undefined)"
render:{[name]
    v:@[get;name;`undefined];
    $[v~`undefined; "(undefined)"; max_render sublist -3!v]}

/ The longest rendering recorded. A watched name is meant to be a scalar or
/ a short list; this bounds the damage when one is not.
max_render:200

/ Compare an owner's watched globals against what was last seen, and record
/ the differences.
/ .
/ STATEFUL BY DESIGN: it updates `seen` as it goes, so a change is reported
/ exactly once. Calling it twice in a row returns rows the first time and
/ nothing the second, which is the property that makes it safe on a timer.
/ @param owner the job whose config to check
/ @param as_of the observation timestamp
/ @return the change rows, empty when nothing moved
/ @eg count .qcfgaudit.poll[`nothing_declares_this;2026.09.19D12:00:00.0] -> 0
poll:{[owner;as_of]
    names:watching owner;
    if[0=count names; :0#config_change];
    rows:0#config_change;
    i:0;
    while[i<count names;
        name:names i;
        now:render name;
        was:$[name in key seen; seen name; ""];
        if[not now~was;
            rows:rows upsert (owner;name;was;now;as_of);
            / fully qualified, for watch's reason
            .qcfgaudit.seen[name]:now];
        i+:1];
    rows}

/ How often a watching process re-reads its configuration. A change made
/ over IPC shows up within this; a change made and reverted inside it does
/ not show up at all, which is the cost of having no assignment hook.
period:0D00:00:05.000

/ A niladic function that polls one owner and publishes what moved.
/ .
/ NILADIC because that is what a TorQ timer takes, and a PROJECTION over
/ the job name rather than a lookup at call time, so the timer cannot be
/ pointed at a job this process is not running.
/ .
/ It resolves the job's publish seam at CALL time, not here: the runner
/ wires that seam after the job is registered, and capturing it now would
/ capture the unwired stub.
/ @param job the streaming job whose config to audit
/ @return a niladic function
/ @eg .qcfgaudit.publisher[`nothing_declares_this][]
publisher:{[job]
    {[job]
        rows:poll[job;.z.p];
        if[0=count rows; :()];
        (get ` sv (.qstream.declaration[job]`ns),`publish)[`config_change;rows];
        }[job]}

/ Forget every observation, so the next poll reports each watched name as
/ new again. For tests and for a deliberate re-baseline; nothing in a
/ running process calls it.
/ @return nothing
/ @eg .qcfgaudit.forget[]
forget:{[] seen::(`symbol$())!(); }

\d .
