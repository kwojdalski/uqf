/ demo_deals_backfill.q - the first real bounded worker (.qddbf).
/ .
/ This is the file every piece of src/etl/core/ was built for. It satisfies
/ the E-01 contract, reads through the E-12 source contract, windows the
/ range per E-18, retries per M-04, honours dry-run per E-14, and records
/ coverage per E-07 - and it does all of that over a synthetic source,
/ because A-04 forbids publishing the real one.
/ .
/ WHAT IT IS AND IS NOT
/ .
/ It is a real worker with a synthetic source, not a demonstration of a
/ worker. The lifecycle, coverage and retry behaviour are the production
/ ones; only the rows are invented. That distinction matters because it
/ decides what its tests prove: they prove the framework works, and they say
/ nothing about the real source's schema - which only
/ `.qsrc.validate_live` on the work machine can settle.
/ .
/ THE ONE ORDERING THAT IS LOAD-BEARING
/ .
/ publish, then coverage, then checkpoint (E-05, E-07). Every interruption
/ point then leaves an UNDER-claim: a re-run redoes work, which retry-safe
/ publication tolerates, rather than an over-claim where the ledger believes
/ work was done that was not. .qwrt.finish_window owns that order, so this
/ file does not re-implement it - it would be the obvious place to get it
/ subtly wrong.

\d .qddbf

worker_name:`demo_deals_backfill
source_name:`demo_deals
dataset:`demo_deals

/ --- the contract's required globals (E-02) ------------------------------

/ Explicit and inspectable rather than buried in a call, which is what E-02
/ asks for: a bounded worker must make its bound visible.
source_version:`;
range_from:0Np;
range_to:0Np;

/ Window width. A worker's own business rather than part of the contract,
/ but it is what makes the bound observable window by window.
width:1D;

/ The live handle, or 0Ni when running on the fixture. Resolved at init.
handle:0Ni;

/ --- the contract's required methods ------------------------------------

spec:{[] `source_version`range_from`range_to!(source_version;range_from;range_to)}

/ Resolve everything that can fail BEFORE any work happens (E-16).
/ .
/ Order here is deliberate, cheapest and most-likely-misconfigured first:
/ configuration, then the contract, then the source declaration and its
/ fixture, then the lock, then the connection. A worker that takes a lock and
/ then discovers its configuration is wrong has to release it again; a worker
/ that connects and then fails the contract has burned a connection for
/ nothing.
/ .
/ The fixture is validated at init even on the live path. It costs nothing and
/ it means a broken test double is found at startup rather than in whichever
/ test happens to run first.
init:{[run_spec]
    source_version::run_spec`source_version;
    range_from::run_spec`range_from;
    range_to::run_spec`range_to;

    if[null source_version;
        '"init: source_version must be set - coverage under one source release says nothing about another (E-09)"];
    .qcov.require_interval[range_from;range_to];

    .qbfstate.register[worker_name;`.qddbf];
    .qbfstate.require_contract worker_name;

    .qsrc.validate_fixture source_name;

    / Validate the coverage ledger's shape before trusting a single read of
    / it (#60). Only bites when the ledger already exists, i.e. when some
    / other process created it - which is exactly the case where its shape
    / is evidence rather than our own assumption.
    / .
    / This call is the point of .qcov.require_schema. Without it the
    / function was defined, tested and never invoked on any live path: a
    / guard that cannot fire protects nothing, and worse, its existence
    / reads as protection to anyone auditing this.
    .qcov.attach[];

    .qbfstate.acquire_lock worker_name;

    / K-01: structured, levelled, and the same field names as every other
    / worker. One INF line per init is the right volume; per-window detail
    / is DBG and off unless someone asks.
    .qlog.register[];
    .qlog.info[worker_name;"initialised";
        `source_version`range_from`range_to`live!
        (source_version;range_from;range_to;not null handle)];

    / Live only when a credential is configured. An absent credential is an
    / explicit statement that this is a demo, NOT a fallback for a failed
    / connection - falling back on failure would turn an outage into
    / silently synthetic data that coverage then records as complete.
    handle::$[.qsrc.has_credentials source_name; connect[]; 0Ni];

    spec[]}

/ Private: open the live connection. Separate from init so the failure is
/ attributable, and so a test can exercise init without one.
connect:{[]
    cred:.qsrc.require_credentials source_name;
    @[{hopen (hsym `$":",x;5000j)};cred;
        {'"connect: cannot reach the ",string[source_name]," source (",x,") - refusing to start rather than falling back to the fixture, which would record synthetic data as covered"}]}

/ Turn a cursor into the windows still to do (E-13, E-18).
/ .
/ Two narrowings, in this order, and the order matters:
/ .
/   1. the CURSOR narrows the range to what this run has not yet reached.
/   2. COVERAGE narrows it further to what is not already published, at this
/      source_version. A retry after a partial run then redoes only the gaps.
/ .
/ Doing coverage first and the cursor second would re-plan windows the
/ current run had already passed, which is merely wasteful. Doing only the
/ cursor would re-fetch windows a PREVIOUS run published, which is what E-13
/ exists to avoid.
plan:{[cursor]
    start:$[null cursor; range_from; cursor];
    if[not start<range_to; :empty_windows[]];
    todo:.qwrt.remaining[dataset;source_version;start;range_to];
    if[0=count todo; :empty_windows[]];
    / one set of windows per uncovered sub-range, then flattened - a gap in
    / the middle must not be bridged by a window spanning it.
    raze {[w] .qwrt.windows[w`range_from;w`range_to;width]} each todo}

empty_windows:{[] ([] range_from:`timestamp$(); range_to:`timestamp$())}

/ Fetch one window, under the retry policy (M-04).
/ .
/ Transport failures retry; data failures do not, because retrying a schema
/ mismatch produces the same mismatch more slowly. .qwrt.with_retry returns a
/ dict rather than throwing, so the caller decides what a terminal failure
/ means - here, per M-05, the window fails and the run moves on.
/ .
/ Every fetched window is validated against the source contract (E-12). That
/ is not belt-and-braces: a source that has dropped a column returns rows
/ where the missing column reads as a NULL in most q code, so without this
/ the worker would publish nulls and record the window as covered.
fetch:{[from_ts;to_ts]
    r:.qwrt.with_retry[.qwrt.policy[];
        {[from_ts;to_ts] last .qsrc.fetch_window[source_name;handle;from_ts;to_ts]}[from_ts;to_ts]];
    if[`failed~r`state; :r];
    .qsrc.validate[source_name;r`result];
    r}

/ Publish a window's rows into the target table.
/ .
/ Returns the row count, which .qwrt.finish_window records as the window's
/ rows_published. Zero is legal and meaningful (E-07): an empty window is
/ positive evidence the range was examined and held nothing.
publish:{[batch]
    t:.qsrc.declaration[source_name]`target;
    if[not t in tables `.; t set 0#batch];
    t insert batch;
    count batch}

/ Save the cursor. Present because the contract requires it (E-01); the
/ actual write goes through .qbfstate so the spec-binding in E-06 is not
/ re-implemented here.
checkpoint:{[cursor] .qbfstate.save_checkpoint[worker_name;spec[];cursor]}

/ --- the shell ----------------------------------------------------------

/ Run one bounded pass to completion.
/ .
/ Per M-05 a window that exhausts its retries is TERMINAL for that window and
/ the run continues: the alternative - failing the whole pass - throws away
/ the windows that did succeed, and their coverage is exactly what makes the
/ retry cheap. The failed windows simply stay uncovered, so the next run
/ plans them again.
/ .
/ Returns a progress dict rather than a status, because writing status is the
/ shell's job (E-04) and .qbfstate.run_pass owns it.
run:{[]
    cursor:.qbfstate.load_checkpoint[worker_name;spec[]];
    windows:plan cursor;
    if[0=count windows;
        / "ran, found no work" is a SUCCESS, not a failure (C-07). An
        / orchestrator that cannot tell them apart retries a successful no-op
        / forever.
        :`state`windows_completed`windows_failed`rows_published`cursor!
            (`idle;0;0;0;cursor)];
    `.qddbf.progress set `windows_completed`windows_failed`rows_published`cursor!(0;0;0;cursor);
    do_window each windows;
    p:progress;
    result:`state`windows_completed`windows_failed`rows_published`cursor!
        ($[p[`windows_failed]>0;`partial;`completed];
         p`windows_completed;p`windows_failed;p`rows_published;p`cursor);
    / one summary line per run at INF - the aggregate a fleet view wants,
    / without the per-window noise that stays at DBG.
    .qlog.info[worker_name;"run finished";result];
    result}

/ Private: one window, end to end. Accumulates into .qddbf.progress rather
/ than returning, because a q lambda does not close over an enclosing local
/ and `each` over windows needs somewhere to put the running totals.
do_window:{[w]
    .qlog.dbg[worker_name;"window start";`range_from`range_to!(w`range_from;w`range_to)];
    f:fetch[w`range_from;w`range_to];
    if[`failed~f`state;
        / ERR, not a throw: per M-05 a failed window is terminal for that
        / window and the run continues. Recording it here, with the window
        / and the classified kind, is what makes "which windows failed and
        / why" answerable from the log rather than from a debugger.
        .qlog.err[worker_name;"window failed";
            `range_from`range_to`kind`attempts`error!
            (w`range_from;w`range_to;f`kind;f`attempts;f`error)];
        `.qddbf.progress set @[progress;`windows_failed;+;1];
        :0b];
    `.qddbf.last_batch set f`result;
    r:.qwrt.finish_window[worker_name;dataset;spec[];w`range_from;w`range_to;
        {.qddbf.publish .qddbf.last_batch}];
    .qlog.dbg[worker_name;"window published";
        `range_from`range_to`rows`dry_run!(w`range_from;w`range_to;r`rows_published;r`dry_run)];
    `.qddbf.progress set
        @[@[@[progress;`windows_completed;+;1];`rows_published;+;r`rows_published];
          `cursor;:;w`range_to];
    1b}

progress:`windows_completed`windows_failed`rows_published`cursor!(0;0;0;0Np);
last_batch:();

/ Release everything this worker holds. Safe on the failure branch too, since
/ release_lock is a no-op when not held.
cleanup:{[]
    if[not null handle; @[hclose;handle;::]; handle::0Ni];
    .qbfstate.release_lock worker_name}

\d .
