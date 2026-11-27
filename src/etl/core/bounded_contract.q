/ bounded_contract.q - the bounded worker lifecycle contract (.qetl.job.bounded.state).
/ .
/ The distinction this file exists to enforce:
/   a BOUNDED worker takes an explicit [range_from;range_to) request, a
/   resumable run specification, a private state file, and has a terminal
/   completion path. That is an enforced contract, checked at init.
/   a CONTINUOUS worker is a long-running poll-and-cursor loop. That is an
/   established pattern with NO registry and no contract, and the
/   asymmetry is deliberate - see issue #62.
/ .
/ "A bounded worker must not silently become an unbounded tailer" is the
/ sentence the whole contract exists to make true.
/ .
/ Its own file because backfill_state.q - the same namespace's locks, durable state
/ files and checkpoints - is held to its line budget (q-module-size), and the
/ contract is a separate concern from them (#2986).

\d .

/ ---------------------------------------------------------------- CONTRACT

/ Every bounded worker must define these functions in its own namespace.
/ Chosen to match the lifecycle the framework actually drives: acquire a
/ request, turn a cursor into windows, fetch one window, publish it, then
/ record the cursor that acknowledges it.
.qetl.job.bounded.state.bounded_worker_methods:`init`plan`fetch`publish`checkpoint

/ ...and these globals. range_from/range_to make the bound explicit and
/ inspectable rather than buried in a call; source_version is mandatory
/ because coverage recorded under one source release says nothing about
/ another, so a run that cannot name its release cannot record
/ coverage either.
.qetl.job.bounded.state.bounded_worker_globals:`source_version`range_from`range_to

/ worker name -> the namespace symbol holding its implementation.
.qetl.job.bounded.state.bounded_workers:(`symbol$())!`symbol$()

/ Private: the names defined in a namespace, or an empty list if the
/ namespace does not exist. Wrapped because `key` on an absent namespace
/ throws rather than returning empty, and a missing namespace is a contract
/ violation we want reported by name rather than as a bare error.
/ @private
.qetl.job.bounded.state.ns_names:{[ns] @[{key x};ns;`symbol$()]}

/ Register a bounded worker. Does NOT validate - require_contract does that,
/ at init, when the namespace is actually populated. Registering early and
/ checking late is deliberate: a worker's methods are defined as its file
/ loads, so validating at registration would force declaration order.
/ @param worker the worker's name, e.g. `markout_backfill
/ @param ns the namespace symbol holding its implementation, e.g.
/   `.qpipe.job.markout_backfill - .qetl.job.bounded.define derives it, so only a worker
/   built without the shell passes one by hand
/ @return the worker name
/ @eg .qetl.job.bounded.state.register[`demo_deals_backfill;`.qpipe.job.demo_deals_backfill]
.qetl.job.bounded.state.register:{[worker;ns]
    .qetl.job.bounded.state.bounded_workers[worker]:ns;
    worker}

/ Every registered bounded worker, for introspection and for tests.
.qetl.job.bounded.state.registered:{[] key .qetl.job.bounded.state.bounded_workers}

/ Check that a registered worker implements the whole contract, and throw
/ naming everything missing if it does not.
/ .
/ Called during the worker's own initialisation, so an incomplete worker
/ fails at startup rather than part-way through a backfill against a live
/ source. It reports ALL missing names at once rather than the first,
/ because fixing them one error at a time is the slow way to find out a
/ worker was never wired up.
/ .
/ This is what makes "is this worker complete" a deterministic test rather
/ than a code-review question.
/ @param worker a name previously passed to register
/ @return the worker name, so it can be used inline in an init chain
/ @throws error if the worker is unregistered, its namespace is absent, or
/   any contract method or global is missing
/ @eg .qetl.job.bounded.state.require_contract[`demo_deals_backfill]
.qetl.job.bounded.state.require_contract:{[worker]
    if[not worker in key .qetl.job.bounded.state.bounded_workers;
        '"require_contract: ",string[worker]," is not registered - call .qetl.job.bounded.state.register first"];
    ns:.qetl.job.bounded.state.bounded_workers worker;
    names:.qetl.job.bounded.state.ns_names[ns];
    if[0=count names;
        '"require_contract: ",string[worker],"'s namespace ",string[ns]," is empty or absent"];
    missing_methods:.qetl.job.bounded.state.bounded_worker_methods where not .qetl.job.bounded.state.bounded_worker_methods in names;
    missing_globals:.qetl.job.bounded.state.bounded_worker_globals where not .qetl.job.bounded.state.bounded_worker_globals in names;
    if[count missing_methods,missing_globals;
        '"require_contract: ",string[worker]," is incomplete",
         $[count missing_methods; " - missing method(s): ",", " sv string missing_methods; ""],
         $[count missing_globals; " - missing global(s): ",", " sv string missing_globals; ""]];
    worker}
