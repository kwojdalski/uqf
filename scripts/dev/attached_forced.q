/ attached_forced.q - the --any-writer writer of an attached backfill (#1650, split out for #1866).
/ .
/ Loaded by scripts/dev/attached_backfill.q, and by its test, so the forced manager can be run without
/ running a backfill. NOTHING is checked on the writer's side, but the manager itself refuses a batch
/ with a row after the completed history (-max_date), with .qetl.io.scoped_check_date - the check the
/ scoped writer makes, which no waiver lifts - before any row is sent.

\d .

/ -force: the process at -writer is the stack's own (a wdb) and runs nothing of ours, so what it
/ does is sent with every call. These two lambdas are what it evaluates; they name only q and,
/ for an _own_ root, the TorQ wdb's .wdb.hdbdir. They stay in the root context so they serialise
/ as plain lambdas.

/ Evaluated BY THE WRITER: append one date's rows to <root>/<date>/<target>/ and return how many.
/ `p# comes off sym first, as io_hdb.q's append does; forced_sort puts it back.
/ @param root the HDB root, or ` for the writer's own .wdb.hdbdir
/ @param d the partition date
/ @param target the table
/ @param rows the date's rows, with a time column and no date column
/ @return the rows appended
.qdev.attached.forced_append:{[root;d;target;rows]
    db:$[null root; hsym .wdb.hdbdir; root];
    part:` sv .Q.par[db;d;target],`;
    data:.Q.en[db;rows];
    have:key part;
    if[`sym in have; @[{@[x;`sym;`#]};part;::]];
    / an interpreter that cannot upsert onto a splayed table on disk rewrites it whole
    $[()~have; part set data; .[upsert;(part;data);{[p;d;e] p set (get p),d}[part;data]]];
    count data}

/ Evaluated BY THE WRITER at the end: sort each date's partition by sym then time and put `p# on
/ sym (where the interpreter can), as a finished partition is left. Returns how many it sorted.
/ @param root as forced_append's
/ @param target the table
/ @param dates the dates written
/ @return the partitions sorted
.qdev.attached.forced_sort:{[root;target;dates]
    db:$[null root; hsym .wdb.hdbdir; root];
    sum {[db;target;d]
        base:.Q.par[db;d;target];
        part:` sv base,`;
        if[()~key part; :0];
        (`sym`time inter get ` sv base,`.d) xasc part;
        if[`sym in key part; @[{@[x;`sym;`p#]};part;::]];
        1}[db;target] each dates}

/ The client's manager: each window's rows go out a date at a time, through forced_append.
/ Refuses every strategy but append - anything else would need to read what the writer holds.
/ Refuses a batch with a row after bounds`max_date before sending any of it: .qetl.io.scoped_check_date.
/ @param call the writer, as a function of one message
/ @param root the HDB root, or ` for the writer's own
/ @param partition_col the column `time copies when the batch has none (the source's time column)
/ @param bounds dict with max_date (the last completed date) and destination (for the message)
/ @return a manager with write, write_keyed and finish
.qdev.attached.forced_manager:{[call;root;partition_col;bounds]
    write:{[call;root;bounds;pc;target;batch;opts]
        if[not `append~opts`on_conflict;
            '"attached_backfill: -force only appends - pass --on-conflict append (this run: ",string[opts`on_conflict],")"];
        if[0=count batch; :0];
        .qetl.io.scoped_check_date[bounds;pc;target;batch;0Np];
        data:$[`time in cols batch; batch; `time xcols ![batch;();0b;enlist[`time]!enlist pc]];
        data:(cols[data] except `date)#data;
        days:`date$data`time;
        `.qdev.attached.forced_dates set distinct .qdev.attached.forced_dates,distinct days;
        sum {[call;root;target;data;days;d] call (.qdev.attached.forced_append;root;d;target;data where days=d)}[call;root;target;data;days] each distinct days}[call;root;bounds;partition_col];
    `write`write_keyed`finish!(
        {[w;target;batch] w[target;batch;enlist[`on_conflict]!enlist `append]}[write];
        write;
        {[call;root;x] / noqa: QF016 niladic
            n:call (.qdev.attached.forced_sort;root;.qdev.attached.dataset;.qdev.attached.forced_dates);
            `.qdev.attached.forced_dates set `date$();
            n}[call;root])}

/ The completed-history bound a forced run was given.
/ @param opts the parsed command line
/ @return the date
/ @throws error when -max_date is not a date: a null one would refuse nothing
.qdev.attached.forced_max_date:{[opts]
    d:"D"$first opts`max_date;
    if[null d; '"attached_backfill: -max_date ",(first opts`max_date)," is not a date"];
    d}

/ The dates this run has sent, for the end-of-run sort.
.qdev.attached.forced_dates:`date$()

\d .
