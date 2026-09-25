/ io_manager.q - where a worker's output goes, as a declaration rather than a
/ hardcoded table insert (.qetl.io).
/ .
/ Before this, .qetl.job.bounded.publish was four lines and they decided everything:
/ .
/     t:.qetl.source.def[(.qetl.job.bounded.def worker)`source]`target;
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
/ shape .qetl.source already uses for a source, and for the same reason: a dict of
/ functions is a first-class value in q, so a pluggable component needs no
/ new language mechanism, only a second instance of a pattern already
/ shipped here.
/ .
/ WHY THERE IS NO `read` OR `exists`. A general IO manager has both. Nothing
/ in this framework reads a target back through an abstraction - downstream
/ workers read the q table directly, in-process - so a `read` here would be a
/ capability reached from no live path. This repository has found four of
/ those in as many days (docs/man.q, .qetl.coverage.require_schema, .qetl.cfg.set_layers,
/ .qdqc), and each read as protection it was not providing. They go in when
/ something calls them.

\d .qetl.io

/ The keys a manager must carry. One, for now, and the list exists so that
/ adding a second is a change to this line rather than to every validator.
required:enlist `write

/ A manager MAY also carry `finish`, a niladic function the bounded-worker
/ shell calls once, after a run's last window. It exists for a store whose
/ appended windows are not finished data until something runs over all of
/ them - the HDB writer below sorts and applies attributes there. A manager
/ without one needs nothing at the end, which is every in-memory one.
optional:enlist `finish

/ Private: can this value be called? A lambda, or a projection over one -
/ the HDB writer's functions are projections over its root and column.
callable:{[v] (type v) within 100 112h}

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
    if[not callable mgr`write;
        '"require_manager: an io manager's write must be a function taking (target;batch)"];
    if[(`finish in key mgr) and not callable mgr`finish;
        '"require_manager: an io manager's finish must be a niladic function"];
    1b}

/ ------------------------------------------------------------- MANAGERS

/ Private: append a batch to a root table, creating it from the batch's own
/ shape when absent.
/ .
/ Backtick form (`target set / insert), because inside \d .qetl.io a bare name
/ resolves to .qetl.io.<name> rather than the root table - the trap materialisation.q
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

/ ---------------------------------------------------------------- HDB

/ Writes each window straight into the HDB partition for its rows' own date,
/ bypassing the tickerplant.
/ .
/ WHY NOT THE TICKERPLANT, for a backfill. The plant stamps `time` on
/ receipt, the RDB holds today, and end-of-day writes everything into
/ TODAY's partition - so a September deal published now lands in today's
/ date, where a query for its own day never finds it. And every subscriber
/ downstream would treat old rows as live ones. History belongs in the
/ partition of the day it happened, written directly.
/ .
/ WHAT write DOES, per window. Refuses rows dated today or later: that
/ partition belongs to the tickerplant and end-of-day, and two writers there
/ collide. Partitions by `time` when the batch has one - every stack table
/ leads with it (scripts/processes/uqs_tables.q), and databento_book's
/ transform already fills it with the event time - and otherwise adds `time`
/ as a copy of `partition_col` (demo_deals and duckdb_deals carry deal_time),
/ so the partition gets the plant's shape and a query on time finds the row
/ in its own date. Enumerates symbols against the HDB's sym file, then appends to
/ <root>/<date>/<target>/, creating it on first write.
/ .
/ WHAT finish DOES, once per run. Every partition this run wrote to is
/ sorted by sym then time and given `p#sym` - appending window by window
/ leaves neither - and .Q.chk fills in tables a partition lacks, so a query
/ across dates does not fail on one the backfill created with a single table
/ in it. .Q.chk takes its table list from the most recent partition, which
/ in a running stack is end-of-day's and holds every table; the full,
/ schema-driven repair (scripts/gates/fill_hdb_partitions.q) runs on every
/ uqs command's bootstrap. finish does NOT tell a running HDB to reload:
/ that needs the stack, so scripts/processes/torq_backfill.q does it through
/ .qtorq.
/ .
/ Not safe to run beside end-of-day: both append to the HDB's sym file.

/ Partitions written and not yet finished: root, date, table.
touched:([] hdb_root:`symbol$(); dt:`date$(); tbl:`symbol$())

/ An HDB writer for one root and partition column.
/ @param root the HDB directory, as a file symbol, e.g. `:/data/hdb
/ @param partition_col the timestamp column that becomes `time`, and so picks
/   the partition, for a batch that has no `time` of its own, e.g. `deal_time
/ @return a manager carrying write and finish
/ @throws error when root is not a file symbol or partition_col not a symbol
/ @eg .qetl.io.hdb[`:/tmp/qio_eg_hdb;`deal_time]
hdb:{[root;partition_col]
    if[not (-11h=type root) and ":"=first string root;
        '"hdb: root must be a file symbol, e.g. `:/data/hdb"];
    if[not -11h=type partition_col;
        '"hdb: partition_col must be a symbol naming a timestamp column"];
    / finish_hdb takes a second, ignored argument so that finish_hdb[root;] is a
    / PROJECTION: on a one-argument function, finish_hdb[root] would be a call.
    `write`finish!(write_hdb[root;partition_col;;];finish_hdb[root;])}

/ Private: append one window into its date partitions.
write_hdb:{[root;partition_col;target;batch]
    if[0=count batch; :0];
    if[not any (`time;partition_col) in cols batch;
        '"hdb: ",string[target],"'s batch has neither time nor ",string[partition_col]," to partition by"];
    data:$[`time in cols batch; batch; `time xcols ![batch;();0b;enlist[`time]!enlist partition_col]];
    days:`date$data`time;
    if[any null days;
        '"hdb: ",string[target]," has rows with a null time - no partition to put them in"];
    if[any days>=.z.d;
        '"hdb: ",string[target]," has rows dated ",string[max days],
         " - today and later belong to the tickerplant and end-of-day, not a backfill"];
    data:.Q.en[root;data];
    {[root;target;data;days;d]
        part:hsym `$(string .Q.par[root;d;target]),"/";
        rows:data where days=d;
        existing:key part;
        / A partition a previous run finished carries p#sym; appending out of
        / order to it is not safe, so the attribute comes off here and
        / finish sorts and puts it back.
        if[`sym in existing; @[part;`sym;`#]];
        $[()~existing; part set rows; part upsert rows];
        `.qetl.io.touched upsert (root;d;target);
        }[root;target;data;days] each distinct days;
    count batch}

/ Private: sort and attribute every partition this root was written to,
/ fill every partition with every table, and forget them.
finish_hdb:{[root;ignored]
    todo:distinct select dt, tbl from touched where hdb_root=root;
    {[root;d;t]
        base:string .Q.par[root;d;t];
        part:hsym `$base,"/";
        c:get hsym `$base,"/.d";
        order:(`sym`time inter c);
        if[count order; order xasc part];
        if[`sym in c; @[part;`sym;`p#]];
        }[root]'[todo`dt;todo`tbl];
    if[count todo; .Q.chk root];
    `.qetl.io.touched set select from touched where not hdb_root=root;
    count todo}

/ ---------------------------------------------------------------- USE

/ The manager used when a worker's declaration names none.
/ .
/ `memory` unless something that knows the deployment says otherwise:
/ scripts/processes/torq_backfill.q sets it to an HDB writer, because where a
/ backfill's rows belong is a fact about the stack it runs in, not about the
/ worker - the same split as a streaming job's publish, which the runner
/ wires. Tests, and anything that loads the tree in plain q, keep memory.
default:memory

/ The manager a worker's declaration asks for, else `default`.
/ .
/ Defaulting rather than requiring keeps every existing worker working
/ unchanged, which is what makes this seam safe to introduce: a declaration
/ that says nothing about io gets exactly the behaviour it had before.
/ @param cfg a worker's configuration dictionary
/ @return the manager dict
/ @throws error, via require_manager, when a declared manager is malformed
/ @eg .qetl.io.for_cfg[`source`dataset`width!(`s;`d;1D)]  ->  .qetl.io.memory
for_cfg:{[cfg]
    if[not `io in key cfg; :default];
    m:cfg`io;
    if[(::)~m; :default];
    require_manager m;
    m}

/ Write one batch through a manager.
/ @param mgr the manager
/ @param target the table symbol the source declaration names
/ @param batch the rows to store
/ @return the number of rows written, as the manager reports them
/ @eg .qetl.io.write[.qetl.io.memory;`demo_deals;.qpipe.source.demo_deals.fixture[]]
write:{[mgr;target;batch] (mgr`write)[target;batch]}

/ Run a manager's end-of-run step, when it has one.
/ @param mgr the manager
/ @return the manager's finish result, or (::) when it has none
/ @eg .qetl.io.finish .qetl.io.memory
finish:{[mgr] $[`finish in key mgr; (mgr`finish)[]; (::)]}

\d .
