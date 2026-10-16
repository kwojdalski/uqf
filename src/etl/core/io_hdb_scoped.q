/ io_hdb_scoped.q - the HDB writer for a NATIVE stack's HDB (#1454): io_hdb.q's
/ writer, handed its destination explicitly and fenced so it can only do what
/ a backfill into a database it does not own may do.
/ .
/ WHAT IS DIFFERENT FROM .qetl.io.hdb, which writes the HDB of a stack uqf runs.
/ .
/   The root is an argument and must already exist. Nothing is created, and there
/   is no default to fall back to: a missing destination is an error, not an
/   empty database.
/ .
/   Every write is checked against bounds the launcher computed from what the
/   native writers reported (uqs.attach.backfill_coordination): no row on or after
/   max_date + 1, which is the date a native writer is filling; no new write
/   after the deadline, which is where the end-of-day fence begins. A write
/   that crosses either throws before it touches the root, so the window fails
/   and is not covered. flush, finish and recover are not fenced: they only
/   finish partitions THIS run wrote, and refusing them would leave those
/   unsorted.
/ .
/   finish never runs .Q.chk over the root. That call fills every partition with
/   every table the latest partition holds - right for a database uqf owns, a
/   rewrite of dates and tables nobody asked about in one it does not. The
/   cost, stated in docs/guides/attach-existing-torq.md: an older partition that
/   lacks the table is not filled (`uqs data hdb-check --fix` does it, as for any
/   table added to a running stack).
/ .
/ It is .qetl.io.hdb underneath, so the lock, the staging area beside the
/ root, the stage-and-swap and the recovery are exactly those: a staged table
/ is never inside the root a reader maps.

\d .

/ Roots whose finish must not fill every partition (see above), as strings.
.qetl.io.scoped_roots:`symbol$()

/ Is this root one a scoped manager was made for?
/ @param root the HDB root, a file symbol
/ @return 1b when finish must not run .Q.chk over it
/ @eg .qetl.io.is_scoped `:/no/such/root  ->  0b
.qetl.io.is_scoped:{[root] (`$string root) in .qetl.io.scoped_roots}

/ Private: the date of every row of a batch, by `time or the partition column.
/ @private
.qetl.io.scoped_days:{[partition_col;batch]
    `date$$[`time in cols batch; batch`time; batch partition_col]}

/ Private: refuse a write the bounds forbid, before anything is touched.
/ @param bounds dict of max_date, deadline and destination
/ @param partition_col the manager's partition column
/ @param target the table
/ @param batch the rows
/ @param window_to the window's exclusive end, or 0Np for an unkeyed write
/ @throws error naming the bound that was crossed
/ @private
.qetl.io.scoped_check:{[bounds;partition_col;target;batch;window_to]
    if[.z.p>bounds`deadline;
        '"hdb_scoped: ",string[target]," - the end-of-day fence began at ",string[bounds`deadline],
         " for ",string[bounds`destination],"; no further writes"];
    days:.qetl.io.scoped_days[partition_col;batch];
    last_day:max days,$[null window_to; `date$(); enlist `date$window_to-1];
    if[last_day>bounds`max_date;
        '"hdb_scoped: ",string[target]," would write ",string[last_day]," into ",string[bounds`destination],
         ", whose completed history ends ",string[bounds`max_date],
         " - later dates belong to the native writers and end of day"]}

/ A manager for an explicit, existing HDB root, fenced by `bounds`.
/ @param root the HDB directory, as a file symbol; it must exist
/ @param partition_col the timestamp column that becomes `time`, as .qetl.io.hdb takes it
/ @param bounds dict: max_date (the last date that may be written), deadline
/   (a timestamp; no write after it) and destination (an identity, for messages)
/ @return a manager with write, write_keyed, flush, finish and recover
/ @throws error when the root is not an existing directory, or bounds is incomplete
/ @eg .qetl.io.hdb_scoped[`:/tmp;`deal_time;`max_date`deadline`destination!(2026.01.31;2026.02.01D00:00;`dest1)]
.qetl.io.hdb_scoped:{[root;partition_col;bounds]
    if[not all `max_date`deadline`destination in key bounds;
        '"hdb_scoped: bounds needs max_date, deadline and destination"];
    if[()~key root; '"hdb_scoped: ",string[root]," does not exist - nothing creates a destination"];
    .qetl.io.scoped_roots,:enlist `$string root;
    m:.qetl.io.hdb[root;partition_col];
    guarded:{[bounds;pc;f;target;batch;wto;rest] .qetl.io.scoped_check[bounds;pc;target;batch;wto]; f . (target;batch),rest};
    m,(`write`write_keyed!(
        {[g;f;t;b] g[f;t;b;0Np;()]}[guarded[bounds;partition_col];m`write];
        {[g;f;t;b;o] g[f;t;b;$[`replace=o`on_conflict; o`range_to; 0Np];enlist o]}[guarded[bounds;partition_col];m`write_keyed]))}

\d .
