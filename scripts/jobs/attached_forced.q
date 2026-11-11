/ attached_forced.q - the --any-writer writer of an attached backfill (#1650, split out for #1866).
/ .
/ Loaded by scripts/jobs/attached_backfill.q, and by its test, so the forced manager can be run without
/ running a backfill. NOTHING is checked on the writer's side, but the manager itself refuses a batch
/ with a row after the completed history (-max_date), with .qetl.io.scoped_check_date - the check the
/ scoped writer makes, which no waiver lifts - before any row is sent.

\d .

/ -force: the process at -writer is the stack's own (a wdb) and runs nothing of ours, so what it
/ does is sent with every call. These two lambdas are what it evaluates; they name only q and,
/ for an _own_ root, the TorQ wdb's .wdb.hdbdir. They stay in the root context so they serialise
/ as plain lambdas.

/ Evaluated BY THE WRITER, before anything is written: refuse rows whose columns or column types are not
/ the partition's own (#2194). upsert onto a splayed table appends column by column, so a batch with one
/ column of another type fails part-way, leaving ragged columns - and a fallback that rewrote the
/ table whole retyped the native rows too. The check reads the partition's column files and the batch,
/ so it runs before .Q.en (which would extend the sym file) and before the first column file is
/ touched. It reads types from the files themselves rather than `meta` of the directory, which not
/ every interpreter accepts, and treats an enumerated column as the symbol column it is.
/ @param base the partition's directory, without a trailing slash (.Q.par)
/ @param rows the date's rows
/ @return 1b when the partition is absent or agrees with the rows
/ @throws error listing every column with its native type and its batch type
.qdev.attached.forced_require_types:{[base;rows]
    if[()~key ` sv base,`.d; :1b];
    kind:{[v] t:type v; $[t within 20 76; 11h; t]};
    ncols:get ` sv base,`.d;
    native:ncols!{[b;kind;c] kind get ` sv b,c}[base;kind] each ncols;
    batch:(cols rows)!kind each value flip rows;
    if[(ncols~cols rows) and native~batch; :1b];
    names:distinct ncols,cols rows;
    show1:{[n;b;c] string[c],": native ",$[c in key n; string .Q.t abs n c; "(absent)"],", batch ",$[c in key b; string .Q.t abs b c; "(absent)"]};
    '"attached_forced: the batch does not match the native table ",string[base]," - nothing was written; ",
      "; " sv show1[native;batch] each names}

/ Evaluated BY THE WRITER: append one date's rows to <root>/<date>/<target>/ and return how many.
/ `p# comes off sym first, as io_hdb.q's append does; forced_sort puts it back. Nothing is written
/ unless forced_require_types accepts the batch, and there is no fallback after a failed upsert:
/ with matching types upsert cannot fail part-way, and any other failure leaves the error to the caller.
/ @param root the HDB root, or ` for the writer's own .wdb.hdbdir
/ @param d the partition date
/ @param target the table
/ @param rows the date's rows, with a time column and no date column
/ @return the rows appended
/ @throws error when the batch's columns or types differ from the partition's
.qdev.attached.forced_append:{[root;d;target;rows]
    db:$[null root; hsym .wdb.hdbdir; root];
    part:` sv .Q.par[db;d;target],`;
    .qdev.attached.forced_require_types[.Q.par[db;d;target];rows];
    data:.Q.en[db;rows];
    have:key part;
    if[`sym in have; @[{@[x;`sym;`#]};part;::]];
    $[()~have; part set data; part upsert data];
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
/ @param bounds dict with max_date (the last completed date) and destination (for the message), and optionally
/   calendar (the stack's, .qetl.io.use_calendar): rows are then sent to the partition the stack files them in
/ @return a manager with write, write_keyed and finish
.qdev.attached.forced_manager:{[call;root;partition_col;bounds]
    if[`calendar in key bounds; .qetl.io.use_calendar bounds`calendar];
    write:{[call;root;bounds;pc;target;batch;opts]
        if[not `append~opts`on_conflict;
            '"attached_backfill: -force only appends - pass --on-conflict append (this run: ",string[opts`on_conflict],")"];
        if[0=count batch; :0];
        .qetl.io.scoped_check_date[bounds;pc;target;batch;0Np];
        data:$[`time in cols batch; batch; `time xcols ![batch;();0b;enlist[`time]!enlist pc]];
        data:(cols[data] except `date)#data;
        days:.qetl.io.scoped_day_of data`time;
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
