/ worker_dependencies.q - what a bounded worker needs before it runs (.qetl.job.bounded.runtime).
/ Split from worker_runtime.q to keep that file under the 800-line budget
/ (#1867); loaded straight after it. Same namespace, unchanged.

\d .

/ ---------------------------------------------------------- DEPENDENCIES

/ worker -> the TorQ process types it needs a connection to.
.qetl.job.bounded.runtime.declared_dependencies:(`symbol$())!();

/ Declare what a worker needs before it can run. One process type may be given
/ as an atom: it is stored as a one-element list, because an atom stored first
/ would make the registry's values a symbol vector, and every later read and
/ declaration a 'type (#2402).
/ @param worker the worker's name
/ @param procs the TorQ process types it needs, a symbol or a symbol list
/ @return the process types, as a list
/ @throws error when procs is not a symbol or a symbol list
/ @eg .qetl.job.bounded.runtime.declare_dependencies[`markout_backfill;`tickerplant`hdb]
.qetl.job.bounded.runtime.declare_dependencies:{[worker;procs]
    if[not 11h=abs type procs;
        '"declare_dependencies: ",string[worker],"'s process types must be a symbol or a symbol list, got type ",string type procs];
    .qetl.job.bounded.runtime.declared_dependencies[worker]:(),procs;
    (),procs}

/ Private: which process types currently have a live connection.
/ .
/ Reads TorQ's .servers.SERVERS when it is loaded, and returns empty
/ otherwise. Wrapped because this file is unit-tested outside a TorQ process,
/ where .servers does not exist at all - and an unwrapped read would make
/ every test here depend on a running stack.
/ .
/ Overridable via connected_override so a test can present a fleet without
/ one.
/ @private
.qetl.job.bounded.runtime.connected_override:();

/ The proctypes currently reachable in the fleet.
/ .
/ Empty when TorQ is absent, which makes require_dependencies report every
/ declared dependency as missing and refuse to start - the direction a total
/ function has to pick, so that a broken probe stops a worker at init rather
/ than letting it run blind.
/ @return a symbol vector of proctypes, empty outside a TorQ process
/ @eg .qetl.job.bounded.runtime.connected[]
.qetl.job.bounded.runtime.connected:{[]
    if[count .qetl.job.bounded.runtime.connected_override; :.qetl.job.bounded.runtime.connected_override];
    @[{exec distinct proctype from .servers.SERVERS where not null w};::;{`symbol$()}]}

/ Fail at INITIALISATION when a declared dependency is unavailable.
/ .
/ At init, not at first use: a worker that starts, runs for twenty minutes
/ and then discovers the hdb was never reachable has already published a
/ partial window and burned the operator's time. Reports every missing
/ dependency at once, for the same reason require_contract does.
/ @throws error naming every unavailable dependency
.qetl.job.bounded.runtime.require_dependencies:{[worker]
    / undeclared is checked first: indexing a missing worker reads the dictionary's prototype
    if[not worker in key .qetl.job.bounded.runtime.declared_dependencies; :worker];
    needed:.qetl.job.bounded.runtime.declared_dependencies[worker];
    if[0=count needed; :worker];
    live:.qetl.job.bounded.runtime.connected[];
    missing:needed where not needed in live;
    .[{.qetl.log.ch.dbg[x;y;z]};(`ipc;"dependencies";`worker`needed`connected`missing!(worker;needed;live;missing));::];
    if[count missing;
        '"require_dependencies: ",string[worker]," cannot start - no connection to ",
         (", " sv string missing),
         " (declared dependencies must resolve through .servers at init)"];
    worker}

/ Fail at initialisation when an upstream dataset is not published for the
/ requested range (the "coverage precondition").
/ .
/ Distinct from needs_fetch above, which asks about THIS worker's own output.
/ This asks about its INPUT: a markout backfill over a range whose trades are
/ not yet published would compute markouts against missing trades and record
/ coverage saying it had done so.
/ .
/ The upstream's partition is a SEPARATE parameter from this worker's own,
/ and deliberately so: a per-symbol markout worker may depend on a trades
/ dataset that is unpartitioned, or partitioned differently. Assuming the two
/ share a partitioning would check the wrong slice and admit a run whose
/ input is missing.
/ @param upstream_part the upstream's partition, or ` when it has none
/ @throws error naming the missing upstream ranges
.qetl.job.bounded.runtime.require_upstream:{[upstream;upstream_part;version;as_of;from_ts;to_ts]
    .qetl.coverage.require_covered[upstream;upstream_part;version;as_of;from_ts;to_ts]}

\d .
