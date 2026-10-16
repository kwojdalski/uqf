// attached_plan.q - what a bounded worker WOULD do over a range, as one JSON
// line, for `uqs job backfill --attach NAME --mode plan` (#1454).
//
//   q scripts/dev/attached_plan.q -worker demo_deals_backfill -version v1 \
//       -from 2026.09.11D00:00 -to 2026.09.13D00:00 [-on_conflict replace] -q
//
// UQF_STATUS_DIR must name the attachment's own state directory (the one
// uqs.attach.backfill derives from the destination's identity), because that
// is where the coverage ledger and the checkpoint this plan reads live. It
// need not exist: a first plan has nothing to read, and this script creates
// nothing.
//
// WHAT IT DOES NOT DO, which is the point of it. It runs no worker lifecycle:
// no init, no lock, no run ledger row, no status file, no io manager and no
// HDB root - nothing here is given a destination to write to. It is
// .qetl.job.bounded.plan_only (the same function `--mode plan` runs on the
// fleet path) plus the facts an admission decision needs and plan_only does
// not return: whether the worker's io is the default, which windows are
// covered, and which UTC dates the windows it would fetch fall on.
//
// Output: one line of JSON on stdout, or {"error": "..."} and exit 1. Every
// timestamp is a q literal string; the reader parses it.

if[not `worker in key .Q.opt .z.x;
    -1 "attached_plan: -worker is required";
    exit 2];

\l src/init.q
.qetl.load.only:`$first (.Q.opt .z.x)`worker
\l src/etl/init.q

\d .

/ Each window's decision: fetch, covered, or deferred (clipped by the plan - a
/ source not complete that far, a resume cursor).
/ @param cfg the worker's declaration
/ @param version the source_version the run records coverage under
/ @param planned the table plan_only returned: the windows it would fetch
/ @param every the table of every window in the range
/ @return a list of strings, one per window
.qdev.attachedplan.decisions:{[cfg;version;planned;every]
    part:.qetl.job.bounded.partition_of cfg`worker;
    as_of:.z.p;
    f:{[planned;w] any (planned`range_from)=w`range_from}[planned];
    {[cfg;part;version;as_of;f;w]
        $[f w; "fetch";
          .qetl.coverage.is_covered[cfg`dataset;part;version;as_of;w`range_from;w`range_to]; "covered";
          "deferred"]}[cfg;part;version;as_of;f] each every}

/ The UTC dates the given windows fall on.
/ @param ws a table of range_from, range_to
/ @return a sorted list of dates, empty for no windows
.qdev.attachedplan.dates:{[ws]
    if[0=count ws; :`date$()];
    asc distinct raze {[w] d0:`date$w`range_from; d0+til 1+(`date$(w`range_to)-1)-d0} each ws}

/ The whole plan, as a dictionary ready for .j.j.
/ @param opts the parsed command line, as .Q.opt returns it
/ @return dictionary of the facts the admission plan reports
/ @throws error for any refusal plan_only or a flag raises
.qdev.attachedplan.facts:{[opts]
    s:.qetl.job.bounded.spec_from_flags opts;
    worker:s`worker;
    .qetl.job.bounded.runtime.use_on_conflict["attached_plan";opts];
    .qetl.cfg.set_override[`mode;"plan"];
    r:.qetl.job.bounded.plan_only[worker;s`spec];
    cfg:`worker _ .qetl.job.bounded.def worker;
    every:.qetl.job.bounded.runtime.windows[r`range_from;r`range_to;cfg`width];
    decisions:.qdev.attachedplan.decisions[cfg,enlist[`worker]!enlist worker;r`source_version;r`plan;every];
    wins:([] range_from:string every`range_from; range_to:string every`range_to; decision:decisions);
    `worker`dataset`source`source_version`live`custom_io`on_conflict`width`partition`time_column`windows`planned`cursor`dates!(
        string worker;string cfg`dataset;string cfg`source;string r`source_version;r`live;
        not (::)~cfg`io;string r`on_conflict;string cfg`width;string .qetl.job.bounded.partition_of worker;
        string (.qetl.source.def cfg`source)`time_column;
        wins;r`planned;string r`cursor;
        string each .qdev.attachedplan.dates every where decisions~\:"fetch")}

\d .

out:.Q.trp[.qdev.attachedplan.facts;.Q.opt .z.x;
    {[e;bt] `error`trace!(e;.Q.sbt bt)}];
-1 .j.j out;
exit $[`error in key out; 1; 0];
