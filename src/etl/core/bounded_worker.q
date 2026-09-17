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
/ recorded under, `width` its window size, `transform` the registered .qxf
/ transform its rows go through between fetch and publish.
/ .
/ Named `worker_cfg`, not `cfg`: `cfg` is the LOCAL in define, init, plan,
/ fetch and publish, and a q local shadows a namespace global of the same
/ name - so `cfg[worker]:...` inside define would have written to the
/ parameter and left this registry silently empty. Not `cfgs` either, which
/ it was: a pluralised contraction is not a word.
worker_cfg:(`symbol$())!();

/ `transform` is REQUIRED, not optional like `check`. A check is a guard a
/ worker may honestly have no use for; a transform is the job itself. A
/ worker that publishes what it fetched says so by declaring a pass-through
/ transform, with examples - which makes "this job changes nothing" a tested
/ claim rather than an absence nobody decided.
required_cfg:`ns`source`dataset`width`transform

/ The partition every worker fills when it does not declare one.
/ .
/ ` is .qcov's "this dataset has no partition dimension" sentinel, so an
/ existing worker that names no partition keeps recording and reading exactly
/ the rows it always did. Declaring `partition` is what opts a dataset into
/ being filled by several workers at once (#185); not declaring it leaves the
/ old one-worker-per-dataset behaviour in place, refusal and all.
unpartitioned:`

/ The keys a worker MAY declare. Absent ones are filled with (::) at
/ registration, which is not tidiness - it is load-bearing.
/ .
/ `config` is a dictionary of dictionaries, and q coerces same-keyed dicts
/ into a TABLE. That coercion was happening by accident: with two workers
/ declaring different keys, `value config` became a table anyway and every
/ worker silently acquired every other worker's keys - demo_events_backfill
/ reported a `check` it had never declared. Worse, a worker declaring a key
/ no earlier worker had made registration fail outright with 'mismatch,
/ because a table cannot gain a column that way.
/ .
/ Normalising every config to the same key set makes the table shape
/ INTENDED rather than emergent: registration order stops mattering, a new
/ optional key is one entry here, and no worker is handed a key it did not
/ ask for with a value it did not choose.
/ `facts` is the worker's hook into materialisation metadata (gap 2.3): a
/ monadic function from the fetched batch to a dict of symbol labels, whose
/ result is attached to the window's materialisation. The framework records
/ what it can know without a schema - rows, source_version, dry_run - and
/ this is where anything needing domain knowledge goes: the min and max of
/ the time column, a null fraction on the column that matters, a checksum.
/ Optional, for the same reason `check` is: metadata written to satisfy a
/ requirement rather than to be read is worse than none.
optional_cfg:`check`io`facts`partition

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
    missing:required_cfg where not required_cfg in key cfg;
    if[count missing;
        '"define: ",string[worker]," is missing ",", " sv string missing];
    if[not -11h=type cfg`ns;
        '"define: ",string[worker],"'s ns must be a namespace symbol such as `.qddbf"];
    if[not 16h=abs type cfg`width;
        '"define: ",string[worker],"'s width must be a timespan, e.g. 1D"];
    if[not (cfg`width)>0D00:00;
        '"define: ",string[worker],"'s width must be positive - a zero width plans infinitely many empty windows"];
    / Validate the io manager HERE, not at first write. A worker with a
    / malformed manager should fail at declaration, not halfway through a
    / backfill having already fetched a window it is now unable to store.
    .qio.for_cfg cfg;
    .qsrc.declaration cfg`source;
    require_transform[worker;cfg];

    / Refuse two workers filling one dataset AND PARTITION (#60, #185).
    / .
    / This used to refuse on the dataset alone, because coverage had no
    / partition dimension: stage_completion recorded (dataset; version;
    / range; rows), so two workers writing one dataset produced rows nothing
    / could tell apart. That refusal was correct given the schema and it was
    / also the ceiling on parallelism - one worker per dataset, however large
    / the range.
    / .
    / Coverage now carries `partition`, so the pair is what has to be unique.
    / Two workers on one dataset filling `EURUSD and `USDJPY record rows that
    / no read composes, because every read filters partition= with equality.
    / Two workers on the SAME pair are still refused, and for the unchanged
    / reason: their coverage would compose and a gap-ridden range would read
    / as complete.
    / .
    / The unpartitioned sentinel is a partition like any other here, so two
    / workers that both decline to declare one still clash - which keeps every
    / existing worker's guarantee exactly as it was.
    part:$[`partition in key cfg; cfg`partition; unpartitioned];
    if[not -11h=type part;
        '"define: ",string[worker],"'s partition must be a symbol, or ` for a dataset with no partition dimension"];
    / Resolved before storage, so `worker_cfg` never holds (::) here and the clash
    / comparison below is symbol against symbol. Every other optional key can
    / be absent because nothing compares them; this one is compared.
    cfg[`partition]:part;
    / Mask over the WHOLE registry first, then drop this worker - filtering the key
    / list before applying the mask pairs a shortened list with a full-length
    / boolean, which q indexes without complaint and which reports the wrong
    / worker as the claimant.
    clash:(key worker_cfg) where ((value worker_cfg)[;`dataset]=cfg`dataset)
                           and (value worker_cfg)[;`partition]=part;
    clash:clash except worker;
    if[count clash;
        '"define: ",string[worker]," declares dataset ",string[cfg`dataset],
         / PARENTHESISED. q evaluates right to left, so
         / `", " sv string clash, " - two workers..."` makes `sv` join the
         / EXPLANATION character by character - the message came out as
         / "fx_rates_eurusd,  , -,  , t, w, o,  , w, o, r, k, e, r, s". The
         / test only checked that the claimant's name appeared, which it
         / did, immediately before the wreckage.
         "[",string[part],"], already claimed by ",(", " sv string clash),
         " - two workers on one dataset and partition produce coverage rows nothing can tell apart"];

    / Normalise to the full key set before storing - see optional_cfg.
    worker_cfg[worker]:normalised cfg;
    worker}

/ Private: the declared transform exists and reads exactly this worker's
/ source.
/ .
/ One input, and its schema must be the source contract's fields and types.
/ Checked at declaration so a transform written against a different shape
/ than the source delivers fails when the worker is defined, not on the
/ first window of a backfill. A transform taking as_of is refused: a window
/ has no single instant it is "as of", and choosing one here would be
/ guessing at what the transform means by it.
require_transform:{[worker;cfg]
    who:"define: ",string[worker];
    if[not -11h=type cfg`transform;
        'who,"'s transform must be the name of a .qxf transform"];
    d:.qxf.declaration cfg`transform;
    if[not 1=count d`inputs;
        'who,"'s transform ",string[cfg`transform]," must read exactly one input, the fetched batch"];
    if[d`as_of;
        'who,"'s transform ",string[cfg`transform]," takes as_of, which a bounded window cannot supply"];
    src:.qsrc.declaration cfg`source;
    contract:flip (src`fields)!{[c] $[c within "AZ"; (); c$()]} each src`types;
    p:.qxf.problems[contract;first value d`inputs;0b];
    if[count p;
        'who,"'s transform ",string[cfg`transform]," does not read source ",string[cfg`source],"'s contract: ","; " sv p];
    }

/ Private: a config carrying every optional key, absent ones as (::).
normalised:{[cfg]
    missing:optional_cfg where not optional_cfg in key cfg;
    if[0=count missing; :cfg];
    cfg,missing!count[missing]#enlist (::)}

/ One worker's configuration, or a refusal naming it.
/ .
/ Throws rather than returning a null for the same reason .qsrc.declaration
/ does: a caller handed an empty dict fails later and somewhere else.
/ @param worker the defined worker's name, as a symbol
/ @return the config dict (ns, source, dataset, width)
/ @throws error naming the worker when define was never called for it
/ @eg .qbw.declaration `demo_deals_backfill
declaration:{[worker]
    if[not worker in key worker_cfg;
        '"declaration: ",string[worker]," has no configuration - call .qbw.define first"];
    worker_cfg worker}

/ One worker's partition, resolved.
/ .
/ Exists so no call site repeats `$[`partition in key cfg;...;`]`. define
/ stores the resolved value, so this is a plain lookup - but going through a
/ named function means a worker's partition has exactly one spelling wherever
/ it is needed, which is what the coverage reads depend on.
/ @param worker the defined worker's name
/ @return its partition symbol, ` when it declared none
/ @eg .qbw.partition_of `demo_deals_backfill
partition_of:{[worker] (declaration worker)`partition}

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
    / ONE as_of for the whole plan, captured here rather than read per call
    / (D-11). Calling .z.p inside each coverage read would plan against a
    / ledger that could be superseded midway, so a range could be reported
    / both covered and uncovered within a single planning pass - and the
    / resulting window list would correspond to no coherent belief about the
    / data at any instant.
    as_of:.z.p;
    todo:.qwrt.remaining[cfg`dataset;cfg`partition;s`source_version;as_of;start;s`range_to];
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
    cfg:declaration worker;
    t:.qsrc.declaration[cfg`source]`target;
    .qio.write[.qio.for_cfg cfg;t;batch]}

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
    / One run identity for the whole execution (gap 2.3), so every window
    / this run materialises is attributable to it and to each other. Begun
    / before the first window and closed with the run's own outcome, so an
    / execution that dies mid-flight leaves a row reading `running` rather
    / than leaving no trace - see .qrun's header.
    begin_run[worker];
    cursor:.qbfstate.load_checkpoint[worker;spec worker];
    windows:plan[worker;cursor];
    if[0=count windows;
        / "ran, found no work" is a SUCCESS, not a failure (C-07). An
        / orchestrator that cannot tell them apart retries a successful
        / no-op forever.
        .qhb.beat[worker;`idle];
        end_run[`idle];
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
    end_run[result`state];
    result}

/ Private: open this execution's run, tolerating an absent .qrun.
/ .
/ Wrapped for the same reason .qcov.current_run is: run.q is not a load-time
/ dependency of this file, and a worker loaded by one of the minimal test
/ loaders should still run. Attribution is an addition to what a run records,
/ never a precondition for running one.
begin_run:{[worker] @[{.qrun.begin x};worker;{[e] (::)}]}

/ Private: close this execution's run with its outcome.
/ .
/ The run's state is the worker's own result state - `completed, `partial or
/ `idle - rather than a separate vocabulary, so a reader of etl_runs and a
/ reader of the worker's log see the same word for the same outcome.
end_run:{[state] @[{.qrun.finish x};state;{[e] (::)}]}

/ Private: one window, end to end. Accumulates into the worker's own
/ `progress` rather than returning, because a q lambda does not close over an
/ enclosing local and `each` over windows needs somewhere to put the totals.
/ Private: run a worker's declared data-quality check over one batch.
/ .
/ Returns the FAILURES, so an empty result means the batch passed - the same
/ convention .qdqc.summarize_checks already uses, and reusing it means a
/ worker can hand that function's output straight back with no adapter.
/ .
/ A worker that declares no check returns no failures. That is deliberate:
/ the framework does not force a check, because a check written to satisfy a
/ requirement rather than to catch something is worse than none - it reads as
/ protection while asserting nothing. What the framework does guarantee is
/ that a check, once declared, is unskippable.
/ .
/ The check runs in DRY RUN too. It reads the batch and publishes nothing, so
/ suppressing it would only hide the one signal a dry run could give about
/ the data - and "would this have published garbage" is exactly what a dry
/ run is for.
/ @param worker the worker's name
/ @param batch the fetched rows, before publication
/ @return a table of failures, empty when the batch is acceptable
/ @throws error when a declared check is not callable, or returns a
/   non-table, naming the worker
run_check:{[worker;batch]
    cfg:declaration worker;
    if[not `check in key cfg; :no_failures[]];
    c:cfg`check;
    if[(::)~c; :no_failures[]];
    if[not 100h=type c;
        '"run_check: ",string[worker],"'s check must be a function taking the batch"];
    r:c batch;
    / A check that returns something other than a table is refused rather
    / than truthiness-tested: `if[count r]` on a stray atom would pass or
    / fail on the value's LENGTH, which is a plausible wrong answer.
    if[not .Q.qt r;
        '"run_check: ",string[worker],"'s check must return a table of failures - an empty one means the batch passed"];
    r}

/ Private: the empty failure table, so every path returns one shape.
no_failures:{[] ([] check:`symbol$(); status:`symbol$(); detail:())}

/ Private: run the worker's transform over one fetched batch.
/ .
/ Narrowed to the contract's declared fields first. .qsrc.validate accepts a
/ source returning MORE columns than it declares, and the transform declares
/ exactly the contract - so the extra columns are dropped here, where the
/ contract says what the job reads, rather than refused.
/ @return the transformed batch
/ @throws whatever the transform throws, or a schema refusal from .qxf
transform_batch:{[worker;batch]
    cfg:declaration worker;
    fields:(.qsrc.declaration cfg`source)`fields;
    nm:cfg`transform;
    .qxf.apply[nm;(.qxf.input_names nm)!enlist fields#batch]}

/ Private: one window, end to end - fetch, transform, check, publish, record.
/ .
/ Accumulates into the worker's own `progress` rather than returning, because
/ a q lambda does not close over an enclosing local and `each` over windows
/ needs somewhere to put the totals.
/ @param worker the worker's name
/ @param w a row carrying range_from and range_to
/ @return 1b when the window completed, 0b when it failed and the run
/   should continue with the next one (M-05)
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
    / TRANSFORM, between fetch and the quality gate, so the gate judges the
    / rows that will actually be published. A throwing transform takes the
    / same terminal-window path as a failed fetch (M-05): nothing published,
    / no coverage staged, the window planned again next run.
    out:@[transform_batch[worker;];f`result;{[e] (`transform_failed;e)}];
    if[(0h=type out) and `transform_failed~first out;
        .qlog.err[worker;"window failed transform";
            `range_from`range_to`transform`error!
            (w`range_from;w`range_to;cfg`transform;last out)];
        write_state[worker;`progress;@[read_state[worker;`progress];`windows_failed;+;1]];
        :0b];
    / DATA QUALITY GATE, between transform and publish.
    / .
    / Before this existed the sequence was fetch, publish, record coverage as
    / complete - so a window of nulls, or one with every price at zero, was
    / recorded as covered and read as published forever. The ledger could
    / record a lie, and nothing anywhere would say so.
    / .
    / A failed check takes the SAME terminal-window path as a failed fetch
    / (M-05): the window is not published, no coverage is staged, the run
    / continues, and the next run plans the window again because coverage
    / never claimed it. That is the behaviour that makes a check safe to add
    / to an existing worker - the worst case is work redone, never data lost
    / and never a gap silently marked complete.
    bad:run_check[worker;out];
    if[count bad;
        .qlog.err[worker;"window failed data quality";
            `range_from`range_to`failures`detail!
            (w`range_from;w`range_to;count bad;.Q.s1 bad)];
        write_state[worker;`progress;@[read_state[worker;`progress];`windows_failed;+;1]];
        :0b];
    write_state[worker;`last_batch;out];
    / The publish function is NILADIC by finish_window's contract, and a
    / fully-applied projection in q is a CALL rather than a deferred one -
    / so the batch goes through the worker's own `last_batch` global and the
    / niladic reads it. Building the argument any other way would publish
    / before the dry-run gate could suppress it.
    r:.qwrt.finish_window[worker;cfg`dataset;cfg`partition;spec worker;
        w`range_from;w`range_to;publish_pending[worker]];
    .qlog.dbg[worker;"window published";
        `range_from`range_to`rows`dry_run!(w`range_from;w`range_to;r`rows_published;r`dry_run)];
    / Materialisation metadata (gap 2.3), recorded HERE rather than in
    / finish_window because this is the only place the batch itself is in
    / hand - finish_window receives a niladic publisher, not rows.
    record_facts[worker;cfg;w;out;r];
    write_state[worker;`progress;
        @[@[@[read_state[worker;`progress];`windows_completed;+;1];`rows_published;+;r`rows_published];
          `cursor;advanced_to[worker];w`range_to]];
    .qhb.beat_window[worker];
    1b}

/ Private: attach this window's metadata to the materialisation.
/ .
/ Two sources, deliberately separated:
/ .
/   framework facts   rows, source_version, dry_run - true of every
/                     materialisation, knowable without reading a single
/                     column, so no worker has to remember to record them.
/   declared facts    whatever the worker's optional `facts` function
/                     returns for this batch. Anything needing to know what
/                     a column MEANS lives here, because the framework
/                     cannot know it.
/ .
/ A failure in a worker's own facts function must not fail the window. The
/ rows are already published and the coverage already staged at this point,
/ so throwing here would turn a successful materialisation into a failed one
/ over a metadata bug - exactly backwards. The failure is logged instead, so
/ it is visible without being fatal.
/ .
/ The whole call is protected for the same reason begin_run is: run.q may not
/ be loaded under a minimal loader, and metadata is an addition rather than a
/ precondition.
record_facts:{[worker;cfg;w;batch;r]
    framework:`rows`source_version`dry_run!
        (r`rows_published;(spec worker)`source_version;r`dry_run);
    declared:$[(::)~cfg`facts;
        ()!();
        @[{[f;b] f b}[cfg`facts;];batch;
          {[worker;w;e]
            .qlog.err[worker;"facts function failed";
                `range_from`range_to`error!(w`range_from;w`range_to;e)];
            ()!()}[worker;w]]];
    if[not 99h=type declared;
        .qlog.err[worker;"facts function returned a non-dictionary";
            `range_from`range_to!(w`range_from;w`range_to)];
        declared:()!()];
    @[{[a] .qrun.record . a};
      (cfg`dataset;w`range_from;w`range_to;framework,declared);
      {[e] (::)}]}

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
