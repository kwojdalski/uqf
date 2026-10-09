/ retention.q - declared, dry-runnable removal of old data (.qetl.retention, #948).
/ .
/ Until this file, nothing in the tree deleted anything (pipeline-philosophy.md
/ section 7), and removing a partition was an operator's rm. That is fine at
/ this scale and wrong the day it is not: an rm leaves no record, and it
/ leaves etl_coverage claiming data that is gone, so a later backfill SKIPS
/ the window on the strength of a claim nothing backs.
/ .
/ A retention is DECLARED, one per dataset, with an explicit horizon:
/ .
/   .qetl.retention.define[`old_deals;`kind`root`table`dataset`horizon!
/       (`hdb_partitions;`:/data/hdb;`demo_deals;`demo_deals;90D)]
/ .
/ NOTHING IS PRUNED BY DEFAULT. There is no default horizon and no built-in
/ declaration: a dataset is touched only when some declaration names it. The
/ record of what was removed (etl_retention) is not a kind, so no declaration
/ can prune it.
/ .
/ THE RULES, each one a decision rather than a convenience:
/ .
/   1. DRY RUN IS THE DEFAULT. run only removes with opts`apply set; without
/      it, it returns exactly the table an applied run would act on.
/   2. NOTHING COVERAGE STILL CLAIMS IS REMOVED, unless the same run also
/      supersedes the claim (opts`supersede). The check is over the whole
/      plan BEFORE the first removal, so a refusal removes nothing.
/   3. SUPERSEDE FIRST, REMOVE SECOND, per item. An interruption between the
/      two leaves data that nothing claims - an under-claim, which only costs
/      a redundant refill. The other order would leave a claim over nothing,
/      and a backfill would skip the window for good.
/   4. THE RECORD IS WRITTEN AFTER THE REMOVAL, per item. A kill in between
/      loses the record of one item but never claims a removal that did not
/      happen. Coverage was superseded first, and that is itself on the ledger.
/   5. HDB PARTITIONS GO THROUGH THE WRITER'S STAGING (.qetl.io.retire), not a
/      raw delete, so a killed removal is put right by the same recovery that
/      puts right a killed write.
/ .
/ WHAT EACH KIND REMOVES
/ .
/   hdb_partitions  a date's partition of one table, once the whole day lies
/                   before the horizon. Never today or later (the tickerplant's).
/                   The coverage claims over that day are looked up by the
/                   declaration's `dataset`, which is not necessarily the
/                   table's name. A plain HDB only: a segmented root (par.txt)
/                   is refused, because enumerating its dates is not done here.
/   ledger_rows     rows of etl_coverage (only WITHDRAWN ones: a current claim
/                   is never a candidate, whatever its age) or etl_runs
/                   (only runs that have ended), older than the horizon.
/   uptime_sessions sessions of etl_stream_uptime whose last_seen is older than
/                   the horizon, except those this process still beats. After
/                   pruning, .qetl.uptime.gaps reports the pruned span as a
/                   gap, as it would for a job that was never up; ask it only
/                   within the horizon.
/ .
/ A declaration is a call, loaded by whoever deploys it. The tree ships none.

\d .qetl.retention

/ The kinds a declaration may name.
kinds:`hdb_partitions`ledger_rows`uptime_sessions

/ The ledgers a ledger_rows declaration may name, and the timestamp column each
/ is aged by. etl_retention is deliberately absent: the record is never pruned.
ledger_age:`etl_coverage`etl_runs!`superseded_at`ended_at

/ The record's columns, in order. One constant in one place, like
/ .qetl.coverage.schema.
/ .
/ WHY NO COLUMN FOR A SEGMENT OR A ROOT: one deployment prunes one root. What
/ would overturn that is two HDB roots declared at once; the `target` would
/ then have to carry the root as well.
record_schema:`retention_id`name`kind`target`item`range_from`range_to`rows,
    `claims_superseded`removed_at`run_id

/ The declarations, by name, each ENLISTED: a dictionary whose values are
/ same-keyed dictionaries is a table on KDB-X, so once two declarations of
/ one kind were stored, a third with other keys was refused 'mismatch
/ (stream_job.q's `jobs` records the same trap).
decls:(`symbol$())!()

/ The columns of a plan, and of what run returns.
plan_cols:`name`kind`target`item`range_from`range_to`rows`claims

/ Private: an empty plan.
/ @private
no_plan:{[] ([] name:`symbol$(); kind:`symbol$(); target:`symbol$(); item:`symbol$();
    range_from:`timestamp$(); range_to:`timestamp$(); rows:`long$(); claims:`long$())}

/ ------------------------------------------------------------ DECLARATION

/ Declare a retention.
/ @param name the retention's name; unique
/ @param decl a dict. Always `kind` (one of kinds) and `horizon` (a timespan
/   greater than zero - required, there is no default). By kind: hdb_partitions
/   takes `root` (the HDB, a file symbol), `table` and `dataset` (the name its
/   coverage is recorded under); ledger_rows takes `table` (etl_coverage or
/   etl_runs); uptime_sessions takes nothing more
/ @return the name
/ @throws error naming what is missing, unknown or duplicated
/ @eg .qetl.retention.define[`old_sessions;`kind`horizon!(`uptime_sessions;30D)]
define:{[name;decl]
    if[not -11h=type name; '"retention: a retention's name must be a symbol"];
    if[name in key decls; '"retention: ",string[name]," is already declared"];
    if[not 99h=type decl; '"retention: ",string[name]," needs a declaration dict"];
    if[not (`kind in key decl) and `horizon in key decl;
        '"retention: ",string[name]," must name its `kind and its `horizon - there is no default horizon"];
    if[not decl[`kind] in kinds;
        '"retention: ",string[name],": kind must be one of ",", " sv string kinds];
    h:decl`horizon;
    if[not $[-16h=type h; h>0D; 0b];
        '"retention: ",string[name],": horizon must be a timespan greater than zero"];
    need:`uptime_sessions`ledger_rows`hdb_partitions!(`symbol$();enlist `table;`root`table`dataset);
    if[count miss:need[decl`kind] where not need[decl`kind] in key decl;
        '"retention: ",string[name]," is missing ",", " sv string miss];
    if[(decl[`kind]=`ledger_rows) and not decl[`table] in key ledger_age;
        '"retention: ",string[name],": table must be one of ",(", " sv string key ledger_age),
         " - the retention record itself is never pruned"];
    if[(decl[`kind]=`hdb_partitions) and not (-11h=type decl`root) and ":"=first string decl`root;
        '"retention: ",string[name],": root must be a file symbol, e.g. `:/data/hdb"];
    `.qetl.retention.decls set decls,enlist[name]!enlist enlist decl;
    name}

/ The declared retentions' names.
/ @return a symbol list
/ @eg .qetl.retention.declared[]
declared:{[] key decls}

/ Private: a declaration, or a refusal naming the ones there are.
/ @private
decl_of:{[name]
    if[not name in key decls;
        '"retention: no retention named ",string[name]," is declared"];
    first decls name}

/ ------------------------------------------------------------------ PLAN

/ Private: one plan row.
/ @private
candidate:{[name;kind;target;item;from_ts;to_ts;rows;claims]
    enlist `name`kind`target`item`range_from`range_to`rows`claims!
        (name;kind;target;item;from_ts;to_ts;"j"$rows;"j"$claims)}

/ Private: the current coverage claims over [from_ts;to_ts) for a dataset.
/ @private
claims_over:{[ds;from_ts;to_ts]
    .qetl.coverage.init_ledger[];
    cur:.qetl.coverage.still_current;
    select partition, source_version from .qetl.coverage.ledger[]
        where dataset=ds, superseded_at=cur, range_from<to_ts, from_ts<range_to}

/ Private: the rows in one partition's table, without mapping the table.
/ @private
part_rows:{[root;d;t]
    base:.qetl.io.part_path[root;d;t];
    c:get hsym `$base,"/.d";
    $[count c; count get hsym `$base,"/",string first c; 0]}

/ Private: the HDB partitions older than the cutoff.
/ @private
plan_hdb:{[name;decl;cutoff]
    .qetl.coverage.attach[];
    root:decl`root;
    tbl:decl`table;
    if[not ()~key hsym `$(1_string root),"/par.txt";
        '"retention: ",string[name],": a segmented HDB (par.txt) is not supported"];
    dirs:key root;
    if[not 11h=type dirs; :no_plan[]];
    dts:"D"$string each dirs;
    dts:asc dts where not null dts;
    dts:dts where (`timestamp$dts+1)<=cutoff;
    dts:dts where dts<.z.d;
    dts:dts where {[root;tbl;d] not ()~key hsym `$.qetl.io.part_path[root;d;tbl]}[root;tbl] each dts;
    if[0=count dts; :no_plan[]];
    raze {[name;decl;root;tbl;d]
        f:`timestamp$d;
        t:`timestamp$d+1;
        candidate[name;`hdb_partitions;tbl;`$string d;f;t;part_rows[root;d;tbl];
            count claims_over[decl`dataset;f;t]]}[name;decl;root;tbl] each dts}

/ Private: the ledger rows older than the cutoff, as one plan row.
/ @private
plan_ledger:{[name;decl;cutoff]
    tbl:decl`table;
    $[tbl=`etl_coverage;
        [.qetl.coverage.attach[];
         cur:.qetl.coverage.still_current;
         old:select recorded_at from .qetl.coverage.ledger[] where superseded_at<cutoff, superseded_at<cur];
        [.qetl.run.attach[];
         old:select recorded_at:started_at from .qetl.run.runs[] where ended_at<cutoff]];
    $[0=count old; no_plan[]; candidate[name;`ledger_rows;tbl;`;min old`recorded_at;cutoff;count old;0]]}

/ Private: the uptime sessions older than the cutoff, as one plan row.
/ @private
plan_uptime:{[name;cutoff]
    .qetl.uptime.attach[];
    keep:.qetl.uptime.mine;
    old:select started_at from .qetl.uptime.sessions[] where last_seen<cutoff, not session in keep;
    $[0=count old; no_plan[];
        candidate[name;`uptime_sessions;`etl_stream_uptime;`;min old`started_at;cutoff;count old;0]]}

/ What a retention would remove as of an instant, and removes nothing.
/ @param name a declared retention
/ @param as_of the instant the horizon is measured back from
/ @return a table of name, kind, target, item, range_from, range_to, rows and
/   claims - how many current coverage claims still overlap the item
/ @throws error when name is not declared, or its HDB cannot be enumerated
/ @eg .qetl.retention.plan[`old_sessions;.z.p]
plan:{[name;as_of]
    decl:decl_of[name];
    cutoff:as_of-decl`horizon;
    k:decl`kind;
    $[k=`hdb_partitions; plan_hdb[name;decl;cutoff];
      k=`ledger_rows; plan_ledger[name;decl;cutoff];
      plan_uptime[name;cutoff]]}

/ ------------------------------------------------------------------- RUN

/ Private: an option, false when absent.
/ @private
flag:{[opts;k] $[k in key opts; opts k; 0b]}

/ Run a retention. A dry run unless opts`apply is true.
/ @param name a declared retention
/ @param as_of the instant the horizon is measured back from
/ @param opts a dict, any keys optional: `apply (remove, rather than report)
/   and `supersede (withdraw the coverage claims over what is removed, rather
/   than refuse). Both default to false
/ @return the plan - the same table whether it was applied or not
/ @throws error, before removing anything, when an applied run would remove
/   data coverage still claims and opts`supersede is not set
/ @eg .qetl.retention.run[`old_sessions;.z.p;()!()]
run:{[name;as_of;opts]
    p:plan[name;as_of];
    if[not flag[opts;`apply]; :p];
    decl:decl_of[name];
    if[(not flag[opts;`supersede]) and any p[`claims]>0;
        '"retention: ",string[name]," would remove ",string[sum 0<p`claims],
         " item(s) of ",string[decl`table]," that etl_coverage still claims for ",
         string[decl`dataset],", and the ledger must not claim data that is gone -",
         " pass `supersede to withdraw the claims in the same run"];
    id:.qetl.run.mint[];
    cutoff:as_of-decl`horizon;
    $[decl[`kind]=`hdb_partitions; apply_hdb[id;decl;p];
      decl[`kind]=`ledger_rows; apply_ledger[id;decl;cutoff;p];
      apply_uptime[id;cutoff;p]];
    p}

/ Private: remove the HDB partitions, one at a time: supersede, remove, record.
/ @private
apply_hdb:{[id;decl;p]
    {[id;decl;r]
        n:0;
        if[r[`claims]>0;
            cl:claims_over[decl`dataset;r`range_from;r`range_to];
            n:sum {[ds;r;c] .qetl.coverage.supersede[ds;c`partition;c`source_version;r`range_from;r`range_to]}[decl`dataset;r] each cl];
        .qetl.io.retire[decl`root;"D"$string r`item;r`target];
        record[id;enlist r;n]}[id;decl] each p;
    }

/ Private: remove the old ledger rows under that ledger's own lock.
/ @private
apply_ledger:{[id;decl;cutoff;p]
    if[0=count p; :()];
    n:$[decl[`table]=`etl_coverage;
        .qetl.coverage.with_lock[{[cutoff]
            .qetl.coverage.reload[];
            cur:.qetl.coverage.still_current;
            before:count .qetl.coverage.ledger[];
            `etl_coverage set delete from .qetl.coverage.ledger[] where superseded_at<cutoff, superseded_at<cur;
            .qetl.coverage.persist[];
            before-count .qetl.coverage.ledger[]};enlist cutoff];
        .qetl.run.under_lock[{[cutoff]
            .qetl.run.reload[];
            before:count .qetl.run.runs[];
            `etl_runs set delete from .qetl.run.runs[] where ended_at<cutoff;
            .qetl.run.persist[];
            before-count .qetl.run.runs[]};enlist cutoff]];
    record[id;update rows:n from p;0]}

/ Private: remove the old uptime sessions under their lock.
/ @private
apply_uptime:{[id;cutoff;p]
    if[0=count p; :()];
    n:.qetl.uptime.update_shared[{[cutoff;keep]
        before:count .qetl.uptime.sessions[];
        `etl_stream_uptime set delete from .qetl.uptime.sessions[] where last_seen<cutoff, not session in keep;
        before-count .qetl.uptime.sessions[]};(cutoff;.qetl.uptime.mine)];
    record[id;update rows:n from p;0]}

/ ---------------------------------------------------------------- RECORD

/ Private: where the record lives, beside the other ledgers.
/ @private
record_path:{[] (.qetl.job.bounded.state.lock_dir[]),"/etl_retention"}

/ Private: create the record if absent.
/ @private
init_record:{[]
    if[not `etl_retention in tables `.;
        `etl_retention set ([] retention_id:0#0Ng; name:`symbol$(); kind:`symbol$();
            target:`symbol$(); item:`symbol$(); range_from:`timestamp$();
            range_to:`timestamp$(); rows:`long$(); claims_superseded:`long$();
            removed_at:`timestamp$(); run_id:0#0Ng)];
    `etl_retention}

/ Private: append plan rows to the record, durably, under the record's lock.
/ @private
record:{[id;p;superseded]
    init_record[];
    now:.z.p;
    run:.qetl.coverage.current_run[];
    rows:update retention_id:id, claims_superseded:"j"$superseded, removed_at:now, run_id:run from p;
    .qetl.job.bounded.state.with_file_lock[`etl_retention;
        {[rows]
            if[not ()~key hsym `$record_path[]; `etl_retention set .qetl.job.bounded.state.durable_get record_path[]];
            `etl_retention insert record_schema#rows;
            .qetl.job.bounded.state.durable_set[record_path[];value `etl_retention]};enlist rows];
    count rows}

/ Everything retention has removed, from every process that has written to
/ this status directory. Never pruned by any declaration.
/ @return the record, in the order it was written
/ @eg .qetl.retention.history[]
history:{[]
    init_record[];
    if[not ()~key hsym `$record_path[]; `etl_retention set .qetl.job.bounded.state.durable_get record_path[]];
    value `etl_retention}

\d .
