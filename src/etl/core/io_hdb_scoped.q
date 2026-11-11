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
/   lacks the table is not filled (`uqs data check --apply` does it, as for any
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

/ The native stack's calendar this process partitions by, as "off=<rolltimeoffset ns>,adj=<roll-zone adjustment ns>"
/ (it travels as a command-line value, which cannot begin with the minus of a negative offset),
/ or "" for UTC midnight (#2193). A TorQ stack names a row's partition .eodtime.getday of its UTC
/ instant: shifted by rolltimeoffset and rolltimezone, so a stack that rolls at 17:00 files 17:30 under
/ the NEXT day. Partitioning by the UTC date writes those rows into a finished native partition.
/ What would bring a data-timezone shift back: a stack whose datatimezone is not GMT stamps `time`
/ in that zone; the launcher refuses such a stack (backfill_coordination, native-calendar) because
/ this writer stamps no zone.
.qetl.io.calendar:""

/ How a calendar is put in force and asked: hooks the launcher scripts fill in, because TorQ's .eodtime is not
/ src/'s to read (check.py etl-layering). scripts/jobs/attached_lib.q loads TorQ's own eodtime.q over the
/ two adjustments and answers with .eodtime.getday; with no calendar loaded, adopting one is refused.
/ @param offset_ns the stack's rolltimeoffset, nanoseconds
/ @param adj_ns the roll zone's adjustment, nanoseconds
.qetl.io.calendar_adopter:{[offset_ns;adj_ns]
    '"calendar: no native calendar is loaded - load scripts/jobs/attached_lib.q"}

/ The hook's partner: the stack's getday over a list of UTC instants.
/ @param times a timestamp or a list of them
.qetl.io.native_day_fn:{[times] '"calendar: no native calendar is loaded"}

/ Adopt a native stack's calendar. What reads it is the stack's own .eodtime.getday (TorQ's eodtime.q,
/ through .qetl.io.calendar_adopter), not a copy of it.
/ @param text "off=<rolltimeoffset ns>,adj=<roll-zone adjustment ns>", or "" for UTC midnight
/ @return the calendar now in force
/ @throws error when the text is malformed, or no calendar loader is present
/ @eg .qetl.io.use_calendar ""  ->  ""
.qetl.io.use_calendar:{[text]
    parts:$[0=count text; (); ","vs text];
    shaped:$[0=count parts; 1b; (2=count parts) and ("off="~4#parts 0) and "adj="~4#parts 1];
    if[not shaped; '"calendar: expected \"off=<rolltimeoffset ns>,adj=<roll zone adjustment ns>\", got ",text];
    v:"J"$4_/:parts;
    if[any null v; '"calendar: ",text," is not two integers (nanoseconds)"];
    if[(0=count parts) or all 0=v; :.qetl.io.calendar:""];
    .qetl.io.calendar_adopter[v 0;v 1];
    .qetl.io.calendar:text}

/ The partition date of each instant, as the stack in force names it.
/ @param times a timestamp or a list of them (UTC)
/ @return the date or dates
/ @eg .qetl.io.scoped_day_of 2026.01.02D10:00:00.000000000  ->  2026.01.02
.qetl.io.scoped_day_of:{[times] $[0=count .qetl.io.calendar; `date$times; .qetl.io.native_day_fn[times]]}

/ The calendar the launcher passed (-calendar), as a bounds entry: absent when the stack files rows by UTC midnight.
/ @param opts the parsed command line (.Q.opt)
/ @return a dict of calendar, or an empty one
/ @eg .qetl.io.calendar_bound ()!()  ->  ()!()
.qetl.io.calendar_bound:{[opts]
    $[`calendar in key opts; enlist[`calendar]!enlist first opts`calendar; ()!()]}

/ Private: the partition date of every row of a batch, by `time or the partition column.
/ @private
.qetl.io.scoped_days:{[partition_col;batch]
    .qetl.io.scoped_day_of $[`time in cols batch; batch`time; batch partition_col]}

/ Refuse rows dated after the destination's completed history. The one bound no waiver lifts
/ (#1828, #1866): scoped_check applies it after the deadline, and the --any-writer path of
/ scripts/jobs/attached_backfill.q, which checks nothing else, applies this alone.
/ @param bounds dict with at least max_date (the last date that may be written) and destination (for messages)
/ @param partition_col the manager's partition column
/ @param target the table
/ @param batch the rows
/ @param window_to the window's exclusive end, or 0Np for an unkeyed write
/ @return the last date the write would touch
/ @throws error naming the bound that was crossed
/ @eg .qetl.io.scoped_check_date[`max_date`destination!(2026.01.31;`dest1);`time;`t;([] time:enlist 2026.01.30D12:00);0Np]
.qetl.io.scoped_check_date:{[bounds;partition_col;target;batch;window_to]
    days:.qetl.io.scoped_days[partition_col;batch];
    last_day:max days,$[null window_to; `date$(); enlist .qetl.io.scoped_day_of window_to-1];
    if[last_day>bounds`max_date;
        '"hdb_scoped: ",string[target]," would write ",string[last_day]," into ",string[bounds`destination],
         ", whose completed history ends ",string[bounds`max_date],
         " - later dates belong to the native writers and end of day"];
    last_day}

/ Private: refuse a write the bounds forbid, before anything is touched.
/ @param bounds dict of max_date, deadline and destination
/ @param partition_col the manager's partition column
/ @param target the table
/ @param batch the rows
/ @param window_to the window's exclusive end, or 0Np for an unkeyed write
/ @throws error naming the bound that was crossed
/ @private
.qetl.io.scoped_check:{[bounds;partition_col;target;batch;window_to]
    .qetl.io.scoped_fence[bounds;target];
    .qetl.io.scoped_check_date[bounds;partition_col;target;batch;window_to]}

/ Private: refuse a write once the end-of-day fence has begun.
/ @param bounds dict of deadline and destination
/ @param target the table
/ @throws error naming the deadline
/ @private
.qetl.io.scoped_fence:{[bounds;target]
    if[.z.p>bounds`deadline;
        '"hdb_scoped: ",string[target]," - the end-of-day fence began at ",string[bounds`deadline],
         " for ",string[bounds`destination],"; no further writes"];}

/ A manager for an explicit, existing HDB root, fenced by `bounds`.
/ @param root the HDB directory, as a file symbol; it must exist
/ @param partition_col the timestamp column that becomes `time`, as .qetl.io.hdb takes it
/ @param bounds dict: max_date (the last date that may be written), deadline
/   (a timestamp; no write after it) and destination (an identity, for messages); optionally
/   calendar, the native stack's (.qetl.io.use_calendar), so rows are filed as the stack files them
/ @return a manager with write, write_keyed, flush, finish and recover
/ @throws error when the root is not an existing directory, or bounds is incomplete
/ @eg .qetl.io.hdb_scoped[`:/tmp;`deal_time;`max_date`deadline`destination!(2026.01.31;2026.02.01D00:00;`dest1)]
.qetl.io.hdb_scoped:{[root;partition_col;bounds]
    if[not all `max_date`deadline`destination in key bounds;
        '"hdb_scoped: bounds needs max_date, deadline and destination"];
    if[()~key root; '"hdb_scoped: ",string[root]," does not exist - nothing creates a destination"];
    .qetl.io.scoped_roots,:enlist `$string root;
    if[`calendar in key bounds; .qetl.io.use_calendar[bounds`calendar]];
    m:.qetl.io.hdb[root;partition_col];
    / Two named lambdas rather than one with a joined argument list: the check first, then the
    / underlying write, with every argument named. The deadline is checked again once the root's
    / lock is held (#2331): a write can wait for it up to .qetl.io.hdb_lock_wait, and one that
    / got it after the fence began would otherwise still write.
    m,`write`write_keyed!(
        {[r;b;pc;t;rows]
            .qetl.io.scoped_check[b;pc;t;rows;0Np];
            .qetl.io.locked[r;{[b;r;pc;t;rows] .qetl.io.scoped_fence[b;t]; .qetl.io.write_hdb[r;pc;t;rows]};(b;r;pc;t;rows)]
            }[root;bounds;partition_col];
        {[r;b;pc;t;rows;o]
            .qetl.io.scoped_check[b;pc;t;rows;$[`replace=o`on_conflict; o`range_to; 0Np]];
            .qetl.io.locked[r;{[b;r;pc;t;rows;o] .qetl.io.scoped_fence[b;t]; .qetl.io.write_hdb_keyed[r;pc;t;rows;o]};(b;r;pc;t;rows;o)]
            }[root;bounds;partition_col])}

\d .
