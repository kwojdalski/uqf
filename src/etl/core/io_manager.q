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
optional:`write_keyed`finish`flush`on_ready

/ `write_keyed` takes (target;batch;opts) and honours opts`on_conflict - what to
/ do when an incoming row's row_key is already in the target. See CONFLICTS.

/ `flush` takes a timestamp and finishes only what lies wholly before it - the
/ shell calls it after every window with the window's end, so a store can
/ make finished parts of a run usable before the run ends. It returns
/ `finished`pending: how many parts it finished, and how many are still open.
/ .
/ `on_ready` is the deployment's hook, not the store's: called after every
/ flush and finish with `finished`pending`final, it decides what to do now
/ that more is finished - a TorQ runner asks the HDB to reload. Kept off the
/ store so the store stays TorQ-free (scripts/gates/check_etl_layering.py).

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
    if[(`write_keyed in key mgr) and not callable mgr`write_keyed;
        '"require_manager: an io manager's write_keyed must be a function taking (target;batch;opts)"];
    if[(`flush in key mgr) and not callable mgr`flush;
        '"require_manager: an io manager's flush must be a function taking a timestamp"];
    if[(`on_ready in key mgr) and not callable mgr`on_ready;
        '"require_manager: an io manager's on_ready must be a function taking a dictionary"];
    1b}

/ ------------------------------------------------------------ CONFLICTS
/ .
/ WHAT TO DO WHEN A ROW IS ALREADY THERE. A source declares a row_key - the
/ column(s) naming a row - and until this section nothing used it: every
/ write appended. Coverage stops a same-version re-run fetching a window
/ twice, but a restatement under a new source_version fetches it again, and
/ appending then doubled every row in the HDB. So a write says how to treat
/ an incoming row whose key the target already holds:
/ .
/   upsert   the incoming row replaces it; new keys are added. The default:
/            a restatement corrects what was there.
/   replace  the target's rows inside this window's range go, then the batch
/            is written - for a source that can also REMOVE rows, which an
/            upsert never would.
/   ignore   the row already there stays; the incoming one is dropped.
/   append   no check - the old behaviour, for data with no real key.
/   fail     refuse, writing nothing, naming how many rows clash.
/ .
/ Keys are matched within what one write sees - for the HDB writer, one date
/ partition. Within one batch the LAST row of a key wins (the FIRST, for
/ ignore), so a batch that repeats itself writes each key once.

/ The strategies, and the one a worker gets when it declares none.
strategies:`upsert`replace`ignore`append`fail
default_strategy:`upsert

/ Refuse what is not a strategy, naming the ones that are.
/ @param strategy the candidate, a symbol
/ @return the strategy
/ @eg .qetl.io.require_strategy `upsert  ->  `upsert
require_strategy:{[strategy]
    if[not strategy in strategies;
        '"on_conflict must be one of ",(", " sv string asc strategies)," - not ",string strategy];
    strategy}

/ Private: true at the first occurrence of each row of `k`, a table of keys.
first_seen:{[k] (til count k)=k?k}

/ Private: true at the last occurrence of each row of `k`.
last_seen:{[k] n:count k; (til n)=(n-1)-(reverse k)?k}

/ Private: the attributes off every column, so a table built from pieces of
/ a p#sym partition can be written back - finish sorts it and puts p# back.
plain:{[t] c:cols t; a:c where not null attr each t c; $[count a; @[t;a;`#]; t]}

/ What `existing` becomes when `batch` is written into it under `strategy`.
/ .
/ Pure: no table is read or written here, so every strategy is tested on
/ two literals. The writers below call it once per target - per partition,
/ for the HDB.
/ @param strategy one of `strategies`
/ @param existing the rows the target holds, 0# when it holds none
/ @param batch the rows being written, the same columns as `existing`
/ @param opts row_key, target (for messages), and for replace time_column,
/   range_from and range_to - the window, end exclusive
/ @return the table the target should hold afterwards
/ @throws error when the columns differ, or under `fail when a key clashes
/ @eg .qetl.io.resolve[`upsert;([] id:1 2; v:10 20);([] id:2 3; v:21 30);`row_key`target!(`id;`t)]  ->  ([] id:1 2 3; v:10 21 30)
resolve:{[strategy;existing;batch;opts]
    require_strategy strategy;
    kc:(),opts`row_key;
    if[not (asc cols existing)~asc cols batch;
        '"on_conflict: ",string[opts`target]," holds ",(" " sv string cols existing),
         " but the batch has ",(" " sv string cols batch)];
    batch:(cols existing)#batch;
    / Same names, same types: joined, two types would make a mixed column,
    / and that is what would be written to disk. An empty batch adds no
    / values to mix, and its nested columns are untyped `()` (meta says " "),
    / so comparing it would refuse an empty window for nothing.
    if[(count existing) and count batch;
        if[not (exec t from meta existing)~exec t from meta batch;
            '"on_conflict: ",string[opts`target],"'s batch types differ from what it holds"]];
    if[`append=strategy; :existing,batch];
    if[`ignore=strategy;
        fresh:batch where first_seen kc#batch;
        :existing,fresh where not (kc#fresh) in kc#existing];
    fresh:batch where last_seen kc#batch;
    clash:(kc#fresh) in kc#existing;
    if[(`fail=strategy) and any clash;
        '"on_conflict fail: ",string[sum clash]," row(s) of ",string[opts`target],
         " already there by ",", " sv string kc];
    kept:existing where not (kc#existing) in kc#fresh;
    if[`replace=strategy;
        t:kept opts`time_column;
        kept:kept where not (t>=opts`range_from) and t<opts`range_to];
    kept,fresh}

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

/ Private: write a batch under opts`on_conflict - the table as resolve says.
write_memory_keyed:{[target;batch;opts]
    existing:$[target in tables `.; value target; 0#batch];
    / The range is the window's; a transformed batch may carry the plant's
    / `time` rather than the source's own time column, and then that is it.
    tc:$[`time in cols batch; `time; opts`time_column];
    target set resolve[opts`on_conflict;existing;batch;opts,`target`time_column!(target;tc)];
    count batch}

/ The default: an in-process table, which is exactly what every worker did
/ before this file existed. Named so a worker can declare it explicitly and
/ read the same as one that says nothing.
/ (enlist `write), not `write: a symbol ATOM keying a one-element list is a
/ type error in q, and the two forms look identical at a glance. Same trap
/ the shipped sources hit with row_key, and it fails loudly rather than
/ building a dictionary of the wrong shape.
memory:`write`write_keyed!(write_memory;write_memory_keyed)

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
discard:`write`write_keyed!(write_discard;{[target;batch;opts] count batch})

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
/ uqs command's bootstrap.
/ .
/ WHAT flush DOES, after every window. The same work as finish, for only the
/ partitions dated wholly before the window's end: windows run in order, so
/ nothing later in the run writes to them again, and finishing them now
/ rather than at the end is what lets a running HDB show a backfill day by
/ day. It reports how many partitions are still open, because a reload while
/ one is mid-append would map a half-written, unsorted partition.
/ .
/ Neither tells a running HDB to reload: that needs the stack, so the
/ manager's on_ready does it, set by scripts/processes/torq_backfill.q.
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
    `write`write_keyed`flush`finish!(write_hdb[root;partition_col;;];write_hdb_keyed[root;partition_col;;;];
        flush_hdb[root;];finish_hdb[root;])}

/ Private: append one window into its date partitions.
write_hdb:{[root;partition_col;target;batch]
    if[0=count batch; :0];
    data:.Q.en[root;hdb_rows[partition_col;target;batch]];
    days:`date$data`time;
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

/ Private: the batch with the plant's `time` column, refused when it cannot
/ be partitioned or would land in the tickerplant's dates.
hdb_rows:{[partition_col;target;batch]
    if[not any (`time;partition_col) in cols batch;
        '"hdb: ",string[target],"'s batch has neither time nor ",string[partition_col]," to partition by"];
    data:$[`time in cols batch; batch; `time xcols ![batch;();0b;enlist[`time]!enlist partition_col]];
    days:`date$data`time;
    if[any null days;
        '"hdb: ",string[target]," has rows with a null time - no partition to put them in"];
    / Names the REMEDY, not only the refusal. The rule is a boundary - today
    / is the plant's, yesterday and earlier are the backfill's, and
    / end-of-day is the handover - but an operator meeting it for the first
    / time meets it as a wall, after the fetch and the quality gate have both
    / passed, which is a confusing place to learn a policy.
    if[any days>=.z.d;
        '"hdb: ",string[target]," has rows dated ",string[max days],
         " - today and later belong to the tickerplant and end-of-day, not a backfill.",
         " Backfill a range ending on or before ",string[.z.d-1],
         ", or let today's rows arrive through the live path"];
    data}

/ Private: write a batch under opts`on_conflict, one date partition at a time.
/ .
/ Every partition the batch touches is read, resolved and rewritten whole -
/ which is why `append stays a separate, faster path. Under `replace, every
/ date the WINDOW covers is visited too, rows or not: a day the source has
/ emptied is exactly the day that must lose what it had. Days from today on
/ are never cleared - they are the tickerplant's.
/ .
/ Resolved for every date BEFORE any is written, so a `fail on the third
/ date leaves the first two untouched rather than half a window written.
write_hdb_keyed:{[root;partition_col;target;batch;opts]
    strategy:require_strategy opts`on_conflict;
    if[`append=strategy; :write_hdb[root;partition_col;target;batch]];
    / Shaped even when empty: a `replace of a day the source has emptied
    / still needs the target's columns to resolve against.
    data:.Q.en[root;hdb_rows[partition_col;target;batch]];
    days:`date$data`time;
    dates:distinct days;
    if[`replace=strategy;
        span:{[f;t] f+til 1+t-f}[`date$opts`range_from;`date$opts[`range_to]-1];
        dates:distinct dates,span where (span<.z.d) and
            {[root;target;d] not ()~key hsym `$(string .Q.par[root;d;target]),"/"}[root;target] each span];
    o:opts,`target`time_column!(target;`time);
    plan:{[root;target;data;days;strategy;o;d]
        part:hsym `$(string .Q.par[root;d;target]),"/";
        rows:data where days=d;
        / A COPY, not the mapped table: the partition is about to be
        / rewritten from the result, and a result that still pointed into
        / the files being overwritten would read them mid-write.
        / Copied by indexing every column, not by -9!-8!: on KDB-X that
        / round trip returns a mapped splayed table still (so `in` throws
        / 'splay), and turns an enumerated sym into plain symbols (so the
        / splayed `set` below throws 'type). Indexing keeps the enumeration.
        existing:$[()~key part; 0#rows; flip {x til count x} each flip select from get part];
        (part;d;plain resolve[strategy;existing;rows;o])
        }[root;target;data;days;strategy;o] each dates;
    {[root;target;step] (step 0) set step 2; `.qetl.io.touched upsert (root;step 1;target);}[root;target] each plan;
    count batch}

/ Private: sort and attribute these partitions of `root`, fill every
/ partition with every table, and forget them.
/ @param root the HDB directory
/ @param todo a table of dt and tbl
/ @return how many partitions were finished
finish_parts:{[root;todo]
    {[root;d;t]
        base:string .Q.par[root;d;t];
        part:hsym `$base,"/";
        c:get hsym `$base,"/.d";
        order:(`sym`time inter c);
        if[count order; order xasc part];
        if[`sym in c; @[part;`sym;`p#]];
        }[root]'[todo`dt;todo`tbl];
    if[count todo; .Q.chk root];
    `.qetl.io.touched set touched except ([] hdb_root:count[todo]#root),'todo;
    count todo}

/ Private: every partition this root was written to.
finish_hdb:{[root;ignored]
    finish_parts[root;distinct select dt, tbl from touched where hdb_root=root]}

/ Private: the partitions of this root dated wholly before `upto`, and how
/ many of its partitions are still open after them.
flush_hdb:{[root;upto]
    n:finish_parts[root;distinct select dt, tbl from touched where hdb_root=root, dt<`date$upto];
    `finished`pending!(n;count distinct select dt, tbl from touched where hdb_root=root)}

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

/ Write a batch under a conflict strategy (CONFLICTS above).
/ .
/ A manager without write_keyed can only append, so anything else is
/ refused for it rather than quietly appended - an upsert that silently
/ appended is the duplication this exists to stop.
/ @param mgr the manager
/ @param target the target table
/ @param batch the rows
/ @param opts on_conflict and row_key, and for `replace time_column, range_from
/   and range_to
/ @return the rows written
/ @eg .qetl.io.write_keyed[.qetl.io.discard;`t;([] id:1 2);`on_conflict`row_key!(`upsert;`id)]  ->  2
write_keyed:{[mgr;target;batch;opts]
    strategy:require_strategy opts`on_conflict;
    $[`write_keyed in key mgr; (mgr`write_keyed)[target;batch;opts];
      `append=strategy; (mgr`write)[target;batch];
      '"write_keyed: this io manager can only append - declare on_conflict `append, or give it a write_keyed"]}

/ Run a manager's end-of-run step, when it has one.
/ @param mgr the manager
/ @return the manager's finish result, or (::) when it has none
/ @eg .qetl.io.finish .qetl.io.memory
finish:{[mgr]
    if[not `finish in key mgr; :(::)];
    n:(mgr`finish)[];
    ready[mgr;`finished`pending`final!(n;0;1b)];
    n}

/ Finish what lies wholly before `upto`, for a manager that can.
/ .
/ A manager without flush finishes nothing early and reports nothing open.
/ @param mgr the manager
/ @param upto a timestamp - the end of the window just written
/ @return `finished`pending
/ @eg .qetl.io.flush[.qetl.io.memory;2026.01.03D00:00]  ->  `finished`pending!0 0
flush:{[mgr;upto]
    r:$[`flush in key mgr; (mgr`flush)[upto]; `finished`pending!0 0];
    ready[mgr;r,enlist[`final]!enlist 0b];
    r}

/ Should a deployment that exposes finished work - a TorQ HDB reload - do
/ it now? The decision an on_ready makes, kept here, pure and TorQ-free, so
/ it is tested without a stack; the deployment holds the state and acts.
/ .
/ Three rules, in order. Nothing finished since the last time: no. A part
/ still open: no - for the HDB writer that is a partition being appended to,
/ unsorted, which a reload would map half-written. Mid-run within `interval`
/ of the last time: no - an HDB reload re-maps the whole database, and a fast
/ run over many small days would otherwise ask once per day. The run's final
/ call ignores the interval, so nothing finished is left unshown.
/ @param state `dirty`last - finished-but-not-shown, and the last time it acted
/ @param status `finished`pending`final, as on_ready receives it
/ @param now the current time
/ @param interval the least time between two mid-run actions, a timespan
/ @return `act`state - whether to act, and the state to keep
/ @eg .qetl.io.due[`dirty`last!(0b;0Np);`finished`pending`final!(1;0;0b);2026.01.02D00:00;0D00:00:30]`act  ->  1b
due:{[state;status;now;interval]
    dirty:state[`dirty] or 0<status`finished;
    soon:(not status`final) and (not null state`last) and now<state[`last]+interval;
    act:dirty and (0=status`pending) and not soon;
    `act`state!(act;$[act; `dirty`last!(0b;now); `dirty`last!(dirty;state`last)])}

/ Private: tell the deployment what is finished. Its failure is logged and
/ swallowed: the rows are already written, and a reload that failed is no
/ reason to fail the run that wrote them.
ready:{[mgr;status]
    if[not `on_ready in key mgr; :(::)];
    @[mgr`on_ready;status;{[e] .[{.qetl.log.err[x;y;z]};(`qetl.io;"on_ready failed";enlist[`error]!enlist e);::]}]}

\d .
