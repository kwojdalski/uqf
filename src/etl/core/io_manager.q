/ io_manager.q - where a worker's output goes, as a declaration rather than a
/ hardcoded table insert (.qio).
/ .
/ Before this, .qbw.publish was four lines and they decided everything:
/ .
/     t:.qsrc.def[(.qbw.def worker)`source]`target;
/     if[not t in tables `.; t set 0#batch];
/     t insert batch;
/ .
/ Every worker wrote to an in-process table named by its source declaration,
/ and that was the only thing it could do. There was no way to capture a
/ worker's output in a test without touching a table, to write the same asset
/ somewhere else, or to change where an asset lands without editing the
/ shell. Compute and storage change for different reasons and at different
/ times, which is the whole argument for separating them.
/ .
/ WHAT A MANAGER IS. A dictionary carrying a `write` function - the same
/ shape .qsrc already uses for a source, and for the same reason: a dict of
/ functions is a first-class value in q, so a pluggable component needs no
/ new language mechanism, only a second instance of a pattern already
/ shipped here.
/ .
/ WHY THERE IS NO `read` OR `exists`. A general IO manager has both. Nothing
/ in this framework reads a target back through an abstraction - downstream
/ workers read the q table directly, in-process - so a `read` here would be a
/ capability reached from no live path. This repository has found four of
/ those in as many days (docs/man.q, .qmatz.require_schema, .qwcfg.set_layers,
/ .qdqc), and each read as protection it was not providing. They go in when
/ something calls them.

\d .qio

/ The keys a manager must carry. One, for now, and the list exists so that
/ adding a second is a change to this line rather than to every validator.
required:enlist `write

/ Refuse a manager that is not one, naming what is wrong.
/ .
/ Checked at DECLARATION rather than at first write, because a worker with a
/ malformed io manager should fail at define time - not halfway through a
/ backfill, having already fetched a window it is now unable to store.
/ @param mgr the candidate manager
/ @return 1b when acceptable
/ @throws error naming the missing key, or the key whose value is not a function
require_manager:{[mgr]
    if[not 99h=type mgr;
        '"require_manager: an io manager must be a dictionary carrying ",
         ", " sv string required];
    missing:required where not required in key mgr;
    if[count missing;
        '"require_manager: io manager is missing ",", " sv string missing];
    if[not 100h=type mgr`write;
        '"require_manager: an io manager's write must be a function taking (target;batch)"];
    1b}

/ ------------------------------------------------------------- MANAGERS

/ Private: append a batch to a root table, creating it from the batch's own
/ shape when absent.
/ .
/ Backtick form (`target set / insert), because inside \d .qio a bare name
/ resolves to .qio.<name> rather than the root table - the trap materialisation.q
/ documents at length, and the reason every write here is explicit.
write_memory:{[target;batch]
    if[not target in tables `.; target set 0#batch];
    target insert batch;
    count batch}

/ The default: an in-process table, which is exactly what every worker did
/ before this file existed. Named so a worker can declare it explicitly and
/ read the same as one that says nothing.
/ (enlist `write), not `write: a symbol ATOM keying a one-element list is a
/ type error in q, and the two forms look identical at a glance. Same trap
/ the shipped sources hit with row_key, and it fails loudly rather than
/ building a dictionary of the wrong shape.
memory:(enlist `write)!enlist write_memory

/ Private: count the rows and write nothing.
write_discard:{[target;batch] count batch}

/ Writes nothing, reports what it would have written.
/ .
/ Distinct from the dry-run gate, and the difference matters: dry run
/ suppresses publication, coverage AND the checkpoint together, so a dry run
/ makes no progress. This suppresses only the STORE, so a pipeline still
/ plans, fetches, checks, records coverage and advances its cursor. That is
/ what makes it useful for exercising a pipeline end to end, or for measuring
/ fetch cost without paying storage cost - neither of which a dry run can do.
/ .
/ Named `discard`, not `null` or `noop`: `null` is a q builtin, and a manager
/ called noop reads as "does nothing", which would be a fair description of
/ the dry-run gate and a wrong one for this.
discard:(enlist `write)!enlist write_discard

/ ---------------------------------------------------------------- USE

/ The manager a worker's declaration asks for, defaulting to `memory`.
/ .
/ Defaulting rather than requiring keeps every existing worker working
/ unchanged, which is what makes this seam safe to introduce: a declaration
/ that says nothing about io gets exactly the behaviour it had before.
/ @param cfg a worker's configuration dictionary
/ @return the manager dict
/ @throws error, via require_manager, when a declared manager is malformed
/ @eg .qio.for_cfg[`source`dataset`width!(`s;`d;1D)]  ->  .qio.memory
for_cfg:{[cfg]
    if[not `io in key cfg; :memory];
    m:cfg`io;
    if[(::)~m; :memory];
    require_manager m;
    m}

/ Write one batch through a manager.
/ @param mgr the manager
/ @param target the table symbol the source declaration names
/ @param batch the rows to store
/ @return the number of rows written, as the manager reports them
/ @eg .qio.write[.qio.memory;`demo_deals;.qfeed.demo_deals.fixture[]]
write:{[mgr;target;batch] (mgr`write)[target;batch]}

\d .
