/ bounded_worker.q - the generic bounded-worker shell (.qbw).
/ .
/ Issue #124. demo_deals_backfill.q was 238 lines of which FOUR were
/ worker-specific - worker_name, source_name, dataset, width - and the other
/ 234 were the same glue any bounded worker needs, because that is exactly
/ what .qbfstate / .qwrt / .qcov / .qsrc already define. A second worker
/ would have duplicated those 234 lines to change four, and then needed
/ keeping in step by hand: the framework moved four times in one day while
/ #46 was being built, and a duplicate would have fallen behind on two of
/ them.
/ .
/ So the glue lives here once, parameterised, and a worker is a declaration.
/ .
/ WHY STATE STILL LIVES IN THE WORKER'S OWN NAMESPACE
/ .
/ The obvious design is for this file to hold the state. It cannot: ETL-01's
/ contract, enforced by .qbfstate.require_contract, requires
/ source_version/range_from/range_to to be names in the WORKER's namespace,
/ so that "is this worker complete" stays a deterministic check rather than
/ a code-review question. Moving them here would make every worker pass the
/ contract vacuously - the check would be inspecting the shell, not the
/ worker.
/ .
/ So the shell reads and writes through the worker's namespace
/ (`get`/`put` below), and the worker declares the globals itself. The cost
/ is two indirection helpers; the benefit is that require_contract still
/ means something.
/ .
/ WHAT A WORKER MAY STILL OVERRIDE
/ .
/ Everything. The worker's own `fetch`, `publish` and so on are ordinary
/ names in its namespace that happen to delegate here, so a worker with a
/ genuinely different publish path defines its own and nothing in this file
/ objects. The shell is a default, not a framework that owns the worker.

\d .qbw

/ worker -> its configuration. `ns` is the worker's namespace symbol,
/ `source` its registered .qsrc source, `dataset` the name coverage is
/ recorded under, `width` its window size.
config:(`symbol$())!();

required_config:`ns`source`dataset`width

/ Declare a worker's configuration.
/ .
/ Validated here rather than at first use, because a declaration is one
/ literal with nothing to defer - the same reasoning .qsrc.register follows
/ and the opposite of .qbfstate.register, whose methods appear as a file
/ loads.
/ @param worker the worker's name
/ @param cfg dict of ns, source, dataset, width
/ @throws error naming every missing or malformed field at once
define:{[worker;cfg]
    missing:required_config where not required_config in key cfg;
    if[count missing;
        '"define: ",string[worker]," is missing ",", " sv string missing];
    if[not -11h=type cfg`ns;
        '"define: ",string[worker],"'s ns must be a namespace symbol such as `.qddbf"];
    if[not 16h=abs type cfg`width;
        '"define: ",string[worker],"'s width must be a timespan, e.g. 1D"];
    if[not (cfg`width)>0D00:00;
        '"define: ",string[worker],"'s width must be positive - a zero width plans infinitely many empty windows"];
    .qsrc.declaration cfg`source;

    / Refuse two workers filling one dataset (#60).
    / .
    / Coverage has no partition dimension: stage_completion records
    / (dataset; source_version; range; rows), so two workers writing the
    / same dataset produce coverage rows nothing can tell apart. If they
    / cover different RANGES that composes correctly and is the intended
    / design; if they cover different PARTITIONS of the same range - per
    / sym, per venue, per region - their coverage wrongly composes and a
    / range covered for one partition reads as covered for all.
    / .
    / Nothing distinguishes those two cases at registration, so the
    / conservative refusal is the right one: it forces the second case to be
    / a deliberate decision (add the partition dimension to .qcov, as a
    / REQUIRED parameter per ETL-09) rather than an accident nobody notices.
    clash:(key config) where (value config)[;`dataset]=cfg`dataset;
    clash:clash except worker;
    if[count clash;
        '"define: ",string[worker]," declares dataset ",string[cfg`dataset],
         ", already claimed by ",", " sv string clash,
         " - coverage has no partition dimension, so two workers writing one dataset produce rows nothing can tell apart"];

    config[worker]:cfg;
    worker}

/ One worker's configuration, or a refusal naming it.
/ .
/ Throws rather than returning a null for the same reason .qsrc.declaration
/ does: a caller handed an empty dict fails later and somewhere else.
/ @param worker the defined worker's name, as a symbol
/ @return the config dict (ns, source, dataset, width)
/ @throws error naming the worker when define was never called for it
/ @eg .qbw.declaration `demo_deals_backfill
declaration:{[worker]
    if[not worker in key config;
        '"declaration: ",string[worker]," has no configuration - call .qbw.define first"];
    config worker}

/ ------------------------------------------------------- WORKER STATE

/ Private: read and write a global in the WORKER's namespace. See the header
/ for why the state cannot live here.
/ .
/ Named read_state/write_state, not get/put: `get` is a q BUILTIN (the
/ counterpart of `set`), so defining it in a namespace throws `assign at
/ load time and aborts the rest of the file - leaving .qbw half-populated
/ while the enclosing script carries on. Seventh reserved-name collision in
/ this repository, after desc, tables, sv, load, var and save.
read_state:{[worker;nm] value ` sv ((declaration worker)`ns),nm}
write_state:{[worker;nm;v] (` sv ((declaration worker)`ns),nm) set v}

/ The worker's current run specification.
spec:{[worker] `source_version`range_from`range_to!read_state[worker] each `source_version`range_from`range_to}

/ ------------------------------------------------------------------ INIT

/ Resolve everything that can fail BEFORE any work happens (ETL-16).
/ .
/ Order is deliberate, cheapest and most-likely-misconfigured first:
/ configuration, then the contract, then the source and its fixture, then
/ the coverage ledger's shape, then the lock, then the connection. A worker
/ that takes a lock and then finds its configuration wrong has to release it
/ again; one that connects and then fails the contract has burned a
/ connection for nothing.
/ @param worker the worker's name
/ @param run_spec dict of source_version, range_from, range_to
/ @return the run specification, as stored
init:{[worker;run_spec]
    cfg:declaration worker;
    write_state[worker;`source_version;run_spec`source_version];
    write_state[worker;`range_from;run_spec`range_from];
    write_state[worker;`range_to;run_spec`range_to];

    if[null run_spec`source_version;
        '"init: source_version must be set - coverage under one source release says nothing about another (ETL-09)"];
    .qcov.require_interval[run_spec`range_from;run_spec`range_to];

    .qbfstate.register[worker;cfg`ns];
    .qbfstate.require_contract worker;

    .qsrc.validate_fixture cfg`source;

    / Validate the ledger's shape before trusting a read of it (#60). Only
    / bites when the ledger already existed, i.e. when another process
    / created it - which is exactly when its shape is evidence rather than
    / our own assumption.
    .qcov.attach[];

    / Same reasoning one table over (K-04): create it and verify its shape
    / here, in a live path, rather than leaving a checker that never fires.
    .qhb.attach[];
    .qhb.beat[worker;`starting];

    .qbfstate.acquire_lock worker;

    / Live only when a credential is configured. An absent credential is an
    / explicit statement that this is a demo, NOT a fallback for a failed
    / connection - falling back on failure would turn an outage into
    / silently synthetic data that coverage then records as complete.
    write_state[worker;`handle;$[.qsrc.has_credentials cfg`source; connect worker; 0Ni]];

    .qlog.register[];
    .qlog.info[worker;"initialised";
        `source_version`range_from`range_to`live!
        (run_spec`source_version;run_spec`range_from;run_spec`range_to;
         not null read_state[worker;`handle])];

    spec worker}

/ Private: open the live connection. Separate from init so the failure is
/ attributable, and so a test can exercise init without one.
connect:{[worker]
    source:(declaration worker)`source;
    cred:.qsrc.require_credentials source;
    @[{hopen (hsym `$":",x;5000j)};cred;
        {[source;e] '"connect: cannot reach the ",string[source]," source (",e,") - refusing to start rather than falling back to the fixture, which would record synthetic data as covered"}[source]]}

/ ------------------------------------------------------------------ PLAN

empty_windows:{[] ([] range_from:`timestamp$(); range_to:`timestamp$())}

/ Turn a cursor into the windows still to do (ETL-13, ETL-18).
/ .
/ Two narrowings, in this order, and the order matters:
/ .
/   1. the CURSOR narrows the range to what this run has not yet reached.
/   2. COVERAGE narrows it further to what is not already published, at this
/      source_version. A retry after a partial run then redoes only the gaps.
/ .
/ Coverage first and cursor second would re-plan windows this run had already
/ passed, which is merely wasteful. Cursor only would re-fetch windows a
/ PREVIOUS run published, which is what ETL-13 exists to avoid.
plan:{[worker;cursor]
    cfg:declaration worker;
    s:spec worker;
    start:$[null cursor; s`range_from; cursor];
    if[not start<s`range_to; :empty_windows[]];
    todo:.qwrt.remaining[cfg`dataset;s`source_version;start;s`range_to];
    if[0=count todo; :empty_windows[]];
    / one set of windows per uncovered sub-range, then flattened - a gap in
    / the middle must not be bridged by a window spanning it.
    raze {[width;w] .qwrt.windows[w`range_from;w`range_to;width]}[cfg`width] each todo}

/ ------------------------------------------------------- FETCH / PUBLISH

/ Fetch one window under the retry policy (M-04).
/ .
/ Transport failures retry; data failures do not, because retrying a schema
/ mismatch produces the same mismatch more slowly. with_retry returns a dict
/ rather than throwing, so the caller decides what a terminal failure means -
/ here, per M-05, the window fails and the run moves on.
/ .
/ Every fetched window is validated against the source contract (ETL-12).
/ Not belt-and-braces: a source that dropped a column returns rows where the
/ missing column reads as a NULL in most q code, so without this the worker
/ publishes nulls and records the window as covered.
fetch:{[worker;from_ts;to_ts]
    cfg:declaration worker;
    h:read_state[worker;`handle];
    r:.qwrt.with_retry[.qwrt.policy[];
        {[source;h;from_ts;to_ts] last .qsrc.fetch_window[source;h;from_ts;to_ts]}[cfg`source;h;from_ts;to_ts]];
    if[`failed~r`state; :r];
    .qsrc.validate[cfg`source;r`result];
    r}

/ Publish a window's rows into the source's declared target.
/ .
/ Returns the row count, which finish_window records as rows_published. Zero
/ is legal and meaningful (ETL-07): an empty window is positive evidence the
/ range was examined and held nothing.
publish:{[worker;batch]
    t:.qsrc.declaration[(declaration worker)`source]`target;
    if[not t in tables `.; t set 0#batch];
    t insert batch;
    count batch}

/ Save the cursor. Present because the contract requires it (ETL-01); the
/ write goes through .qbfstate so ETL-06's spec-binding is not
/ re-implemented.
checkpoint:{[worker;cursor] .qbfstate.save_checkpoint[worker;spec worker;cursor]}

/ ------------------------------------------------------------------- RUN

/ Run one bounded pass to completion.
/ .
/ Per M-05 a window that exhausts its retries is TERMINAL for that window and
/ the run continues: failing the whole pass would throw away the windows that
/ did succeed, and their coverage is what makes the retry cheap. Failed
/ windows stay uncovered, so the next run plans them again.
run:{[worker]
    cursor:.qbfstate.load_checkpoint[worker;spec worker];
    windows:plan[worker;cursor];
    if[0=count windows;
        / "ran, found no work" is a SUCCESS, not a failure (C-07). An
        / orchestrator that cannot tell them apart retries a successful
        / no-op forever.
        .qhb.beat[worker;`idle];
        :`state`windows_completed`windows_failed`rows_published`cursor!
            (`idle;0;0;0;cursor)];
    write_state[worker;`progress;`windows_completed`windows_failed`rows_published`cursor!(0;0;0;cursor)];
    .qhb.beat[worker;`running];
    do_window[worker] each windows;
    p:read_state[worker;`progress];
    result:`state`windows_completed`windows_failed`rows_published`cursor!
        ($[p[`windows_failed]>0;`partial;`completed];
         p`windows_completed;p`windows_failed;p`rows_published;p`cursor);
    / one summary line per run at INF - the aggregate a fleet view wants,
    / without the per-window noise that stays at DBG.
    .qlog.info[worker;"run finished";result];
    / The terminal beat, so a finished worker does not read as wedged. The
    / per-window beat inside do_window is the one that catches a worker
    / stuck mid-window, which is the case a status file cannot show - it
    / says `running` and keeps saying it.
    .qhb.beat[worker;result`state];
    result}

/ Private: one window, end to end. Accumulates into the worker's own
/ `progress` rather than returning, because a q lambda does not close over an
/ enclosing local and `each` over windows needs somewhere to put the totals.
do_window:{[worker;w]
    cfg:declaration worker;
    .qlog.dbg[worker;"window start";`range_from`range_to!(w`range_from;w`range_to)];
    f:fetch[worker;w`range_from;w`range_to];
    if[`failed~f`state;
        / ERR, not a throw: per M-05 a failed window is terminal for that
        / window and the run continues. Recording it with the window and the
        / classified kind is what makes "which windows failed and why"
        / answerable from the log rather than from a debugger.
        .qlog.err[worker;"window failed";
            `range_from`range_to`kind`attempts`error!
            (w`range_from;w`range_to;f`kind;f`attempts;f`error)];
        write_state[worker;`progress;@[read_state[worker;`progress];`windows_failed;+;1]];
        :0b];
    write_state[worker;`last_batch;f`result];
    / The publish function is NILADIC by finish_window's contract, and a
    / fully-applied projection in q is a CALL rather than a deferred one -
    / so the batch goes through the worker's own `last_batch` global and the
    / niladic reads it. Building the argument any other way would publish
    / before the dry-run gate could suppress it.
    r:.qwrt.finish_window[worker;cfg`dataset;spec worker;w`range_from;w`range_to;
        publish_pending[worker]];
    .qlog.dbg[worker;"window published";
        `range_from`range_to`rows`dry_run!(w`range_from;w`range_to;r`rows_published;r`dry_run)];
    write_state[worker;`progress;
        @[@[@[read_state[worker;`progress];`windows_completed;+;1];`rows_published;+;r`rows_published];
          `cursor;advanced_to[worker];w`range_to]];
    .qhb.beat_window[worker];
    1b}

/ Private: the new cursor, refusing any move that is not strictly forward.
/ .
/ D-09 asked whether backfill is strictly oldest-first, and whether the order
/ matters to correctness or only to observability. It is oldest-first by
/ construction - windows[] builds starts as from_ts+width*til n, and remaining
/ hands back ascending sub-ranges - and the order matters to CORRECTNESS,
/ because plan[] uses this cursor as its LOWER bound. A window processed out
/ of order would push the cursor past windows that are still uncovered, and
/ the next run would plan from there and never come back for them. Coverage
/ would still show them as gaps, so nothing gets wrongly reported complete;
/ they simply never get filled while that checkpoint stands.
/ .
/ So the ordering was load-bearing and unenforced. .qcont.advance already
/ refuses a non-strictly-forward continuous cursor for a closely related
/ reason; this is the same invariant on the bounded path, which had a plain
/ assignment. One comparison, and the asymmetry is gone.
/ @throws error when the cursor would stand still or move backwards
advanced_to:{[worker;current;next_cursor]
    if[(not null current) and not next_cursor>current;
        '"cursor for ",string[worker]," would move from ",string[current],
         " to ",string[next_cursor]," - plan uses it as a lower bound, so a ",
         "cursor that is not strictly forward skips windows that are still uncovered"];
    next_cursor}

/ Private: a NILADIC publisher for one worker's pending batch. A projection
/ with its last argument still missing, so finish_window's dry-run gate can
/ choose not to call it at all.
/ `unused`, not `_`: an underscore parameter makes the application throw
/ `'match`, because `_` is q's DROP/CUT operator rather than an ordinary
/ name. It parses and even projects without complaint, then fails only when
/ applied - so the first draft of this file loaded cleanly, planned windows
/ correctly, and failed on the first publish with a bare `'match` naming
/ nothing. Eighth reserved-name class here, and the first that is
/ punctuation rather than a word.
publish_pending:{[worker] publish_last_batch[worker;]}

publish_last_batch:{[worker;unused] publish[worker;read_state[worker;`last_batch]]}

/ Release everything the worker holds. Safe on the failure branch too, since
/ release_lock is a no-op when not held.
cleanup:{[worker]
    h:read_state[worker;`handle];
    if[not null h; @[hclose;h;::]; write_state[worker;`handle;0Ni]];
    .qbfstate.release_lock worker}

\d .
