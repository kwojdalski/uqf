/ bounded_worker.q - the generic bounded-worker shell (.qbw).
/ .
/ Issue #124. demo_deals_backfill.q was 238 lines of which FOUR were
/ worker-specific - worker_name, source_name, dataset, width - and the other
/ 234 were the same glue any bounded worker needs, because that is exactly
/ what .qbfstate / .qwrt / .qmatz / .qsrc already define. A second worker
/ would have duplicated those 234 lines to change four, and then needed
/ keeping in step by hand: the framework moved four times in one day while
/ #46 was being built, and a duplicate would have fallen behind on two of
/ them.
/ .
/ So the glue lives here once, parameterised, and a worker is a declaration.
/ .
/ WHY STATE STILL LIVES IN THE WORKER'S OWN NAMESPACE
/ .
/ The obvious design is for this file to hold the state. It cannot: the contract's
/ contract, enforced by .qbfstate.require_contract, requires
/ source_version/range_from/range_to to be names in the WORKER's namespace,
/ so that "is this worker complete" stays a deterministic check rather than
/ a code-review question. Moving them here would make every worker pass the
/ contract vacuously - the check would be inspecting the shell, not the
/ worker.
/ .
/ So the shell reads and writes through the worker's namespace
/ (read_state/write_state below), and the names live there. The cost is two
/ indirection helpers; the benefit is that require_contract still means
/ something.
/ .
/ HOW A WORKER INHERITS THEM (#227)
/ .
/ define STAMPS them. Until #227 every worker file carried the same twenty
/ lines by hand - six globals and eight one-line delegators - which was #124's
/ duplication again at a smaller scale: a fourth worker pasted the block a
/ fourth time, and the delegator list was a second copy of
/ .qbfstate.bounded_worker_methods that nothing kept in step. Now define
/ writes every inherited name the worker has not defined itself into the
/ worker's namespace, from the shell's own signatures (see inherit), and a
/ worker file is its transform, its optional check and facts, and one define.
/ .
/ WHAT A WORKER MAY STILL OVERRIDE
/ .
/ Everything, and the override is honoured. A name the worker defines BEFORE
/ its define call is left alone, and run and do_window reach plan, fetch and
/ publish through the worker's namespace (see `own`) rather than calling the
/ shell's directly - which is what the first version of this file promised
/ and did not do: its run loop called .qbw.fetch whatever the worker had
/ defined, so an override was reachable from the prompt and from nowhere
/ else. A worker with a genuinely different publish path defines its own,
/ and the shell is a default rather than a framework that owns the worker.

\d .qbw

/ worker -> its configuration. `source` is the worker's registered .qsrc
/ source, `dataset` the name coverage is recorded under, `width` its window
/ size, `transform` the registered .qxf transform its rows go through
/ between fetch and publish, and `ns` its namespace - DERIVED by define,
/ never supplied: see worker_root.
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
required_cfg:`source`dataset`width`transform

/ Every worker instance lives under this one namespace, as
/ .qwrk.<worker>: .qwrk.demo_deals_backfill, .qwrk.upstream_trades_backfill.
/ .
/ The library's own modules are flat by convention (one file, one
/ `\d .q<abbrev>`), and workers used to follow suit - .qddbf, .qevbf,
/ .qupbf, .qdbnbf - which put four instances of one shape beside .qbw,
/ .qmatz and .qsrc as if they were four more frameworks, and made each
/ instance's namespace a second name to invent, spell and keep in step with
/ the worker's registered name. Nesting them under one root separates
/ "the framework" from "what runs on it", lets `key `.qwrk` list every
/ loaded worker, and means a worker has exactly ONE name: define derives
/ the namespace from it, so there is nothing to keep in step.
worker_root:`.qwrk

/ The namespace a worker's implementation lives in. The single spelling of
/ the `.qwrk.<worker>` rule; every caller that needs the namespace goes
/ through here or through the `ns` key define stores from it.
/ @param worker the worker's name
/ @return the namespace symbol, e.g. `.qwrk.demo_deals_backfill
/ @eg .qbw.namespace `demo_deals_backfill  ->  `.qwrk.demo_deals_backfill
namespace:{[worker] ` sv worker_root,worker}

/ ------------------------------------------------------- INHERITANCE

/ The globals every worker starts with. The first three are the contract's
/ contract; `handle` is the live connection or 0Ni on the fixture; `progress`
/ and `last_batch` are the run accumulators, one set PER WORKER because a q
/ lambda does not close over an enclosing local, so `each` over windows needs
/ a named place for the running totals - and two workers in one process must
/ not share it.
initial_state:`source_version`range_from`range_to`handle`progress`last_batch!
    (`;0Np;0Np;0Ni;`windows_completed`windows_failed`rows_published`cursor!(0;0;0;0Np);())

/ The methods every worker gets: the contract's five, taken from the
/ contract itself so the two cannot drift, plus the three the launcher and
/ the tests call through the worker's namespace.
inherited_methods:.qbfstate.bounded_worker_methods,`spec`run`cleanup

/ Private: the delegating lambda for one method, built from the shell
/ function's own parameter list so its signature is the shell's minus
/ `worker`. For `fetch it is
/   {[from_ts;to_ts] .qbw.fetch[`demo_deals_backfill;from_ts;to_ts]}
/ which is what the hand-written one used to say, and what `.qwrk.x.fetch`
/ at the prompt still shows. A real lambda rather than a projection for
/ that readability, and because the niladic ones - run[], cleanup[] - have
/ no projection form: a projection with every argument supplied is a call.
delegate:{[worker;nm]
    / value of the NAME is the function; value of the function is its
    / parse tree, whose second element is the parameter list.
    args:1_(value value ` sv `.qbw,nm)[1];
    value "{[",(";" sv string args),"] .qbw.",string[nm],"[",.Q.s1[worker],
        $[count args; ";",";" sv string args; ""],"]}"}

/ Private: give a worker namespace every inherited name it has not defined.
/ .
/ Only the ABSENT names, which is the whole override mechanism: a worker
/ that defined its own `publish` before calling define keeps it. It is also
/ what makes a reload safe - a worker file re-runs its own define, and the
/ state its previous run left behind is not reset under it.
inherit:{[worker;ns]
    have:.qbfstate.ns_names ns;
    globals:(key initial_state) except have;
    {[ns;nm;v] (` sv ns,nm) set v}[ns;;]'[globals;initial_state globals];
    methods:inherited_methods except have;
    {[ns;worker;nm] (` sv ns,nm) set delegate[worker;nm]}[ns;worker;] each methods;
    }

/ The partition every worker fills when it does not declare one.
/ .
/ ` is .qmatz's "this dataset has no partition dimension" sentinel, so an
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
/ `facts` is the worker's hook into materialisation metadata: a
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
/ literal with nothing to defer - the same reasoning .qsrc.define follows
/ and the opposite of .qbfstate.register, whose methods appear as a file
/ loads.
/ @param worker the worker's name
/ @param decl dict of source, dataset, width, transform, and optionally
/   check, facts, partition, io, procname (default `<worker>1) and note
/ @throws error naming every missing or malformed field at once
define:{[worker;decl]
    missing:required_cfg where not required_cfg in key decl;
    if[count missing;
        '"define: ",string[worker]," is missing ",", " sv string missing];
    / The namespace is not configurable, and a supplied one is refused rather
    / than overwritten: a worker declared with `ns`.qddbf would be looked
    / for under .qwrk.demo_deals_backfill regardless, and the author would
    / learn that from a contract failure at init naming twelve missing
    / methods rather than from define naming the key.
    if[`ns in key decl;
        '"define: ",string[worker],"'s namespace is derived - .qwrk.",string[worker]," - not configured; drop the ns key"];
    decl[`ns]:namespace worker;
    if[not 16h=abs type decl`width;
        '"define: ",string[worker],"'s width must be a timespan, e.g. 1D"];
    if[not (decl`width)>0D00:00;
        '"define: ",string[worker],"'s width must be positive - a zero width plans infinitely many empty windows"];
    / Validate the io manager HERE, not at first write. A worker with a
    / malformed manager should fail at declaration, not halfway through a
    / backfill having already fetched a window it is now unable to store.
    .qio.for_cfg decl;
    .qsrc.def decl`source;
    require_transform[worker;decl];

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
    part:$[`partition in key decl; decl`partition; unpartitioned];
    if[not -11h=type part;
        '"define: ",string[worker],"'s partition must be a symbol, or ` for a dataset with no partition dimension"];
    / Resolved before storage, so `worker_cfg` never holds (::) here and the clash
    / comparison below is symbol against symbol. Every other optional key can
    / be absent because nothing compares them; this one is compared.
    decl[`partition]:part;
    / The process that runs this worker, and a line on why it is deployed the
    / way it is. Both feed the uqs process registry, which is DERIVED
    / from these declarations rather than kept as a second list. Resolved
    / before storage for the reason partition is: worker_cfg's rows share one
    / shape, and a column that is a symbol on one row and (::) on the next
    / stops the next assignment fitting.
    proc:$[`procname in key decl; decl`procname; `$string[worker],"1"];
    if[not -11h=type proc;
        '"define: ",string[worker],"'s procname must be a symbol naming the process that runs it, e.g. `",string[worker],"1"];
    decl[`procname]:proc;
    note:$[`note in key decl; decl`note; ""];
    if[not 10h=type note;
        '"define: ",string[worker],"'s note must be a string"];
    decl[`note]:note;
    / Mask over the WHOLE registry first, then drop this worker - filtering the key
    / list before applying the mask pairs a shortened list with a full-length
    / boolean, which q indexes without complaint and which reports the wrong
    / worker as the claimant.
    clash:(key worker_cfg) where ((value worker_cfg)[;`dataset]=decl`dataset)
                           and (value worker_cfg)[;`partition]=part;
    clash:clash except worker;
    if[count clash;
        '"define: ",string[worker]," declares dataset ",string[decl`dataset],
         / PARENTHESISED. q evaluates right to left, so
         / `", " sv string clash, " - two workers..."` makes `sv` join the
         / EXPLANATION character by character - the message came out as
         / "fx_rates_eurusd,  , -,  , t, w, o,  , w, o, r, k, e, r, s". The
         / test only checked that the claimant's name appeared, which it
         / did, immediately before the wreckage.
         "[",string[part],"], already claimed by ",(", " sv string clash),
         " - two workers on one dataset and partition produce coverage rows nothing can tell apart"];

    / Normalise to the full key set before storing - see optional_cfg.
    worker_cfg[worker]:normalised decl;
    / Last, so a refused declaration leaves no half-stamped namespace behind.
    inherit[worker;decl`ns];
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
    d:.qxf.def cfg`transform;
    if[not 1=count d`inputs;
        'who,"'s transform ",string[cfg`transform]," must read exactly one input, the fetched batch"];
    if[d`as_of;
        'who,"'s transform ",string[cfg`transform]," takes as_of, which a bounded window cannot supply"];
    src:.qsrc.def cfg`source;
    contract:flip (src`columns)!{[c] $[c within "AZ"; (); c$()]} each src`types;
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
/ Throws rather than returning a null for the same reason .qsrc.def
/ does: a caller handed an empty dict fails later and somewhere else.
/ @param worker the defined worker's name, as a symbol
/ @return the config dict (source, dataset, width, transform, partition,
/   and the derived ns)
/ @throws error naming the worker when define was never called for it
/ @eg .qbw.def `demo_deals_backfill
def:{[worker]
    if[not worker in key worker_cfg;
        '"def: ",string[worker]," has no configuration - call .qbw.define first"];
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
partition_of:{[worker] (def worker)`partition}

/ ------------------------------------------------------- WORKER STATE

/ Private: read and write a global in the WORKER's namespace. See the header
/ for why the state cannot live here.
/ .
/ Named read_state/write_state, not get/put: `get` is a q BUILTIN (the
/ counterpart of `set`), so defining it in a namespace throws `assign at
/ load time and aborts the rest of the file - leaving .qbw half-populated
/ while the enclosing script carries on. Seventh reserved-name collision in
/ this repository, after desc, tables, sv, load, var and save.
read_state:{[worker;nm] value ` sv ((def worker)`ns),nm}
write_state:{[worker;nm;v] (` sv ((def worker)`ns),nm) set v}

/ Private: one of the worker's own methods - the inherited delegate, or the
/ worker's override. run and do_window go through here rather than calling
/ the shell's plan, fetch and publish directly; that is what makes an
/ override take effect, and the delegate calls back into the shell's
/ function by its full name, so there is no loop.
own:{[worker;nm] read_state[worker;nm]}

/ The worker's current run specification.
spec:{[worker] `source_version`range_from`range_to!read_state[worker] each `source_version`range_from`range_to}

/ ------------------------------------------------------------------ INIT

/ Resolve everything that can fail BEFORE any work happens.
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
    cfg:def worker;
    write_state[worker;`source_version;run_spec`source_version];
    write_state[worker;`range_from;run_spec`range_from];
    write_state[worker;`range_to;run_spec`range_to];

    if[null run_spec`source_version;
        '"init: source_version must be set - coverage under one source release says nothing about another"];
    .qmatz.require_interval[run_spec`range_from;run_spec`range_to];

    .qbfstate.register[worker;cfg`ns];
    .qbfstate.require_contract worker;
    .qlog.dbg[worker;"init: worker contract satisfied";enlist[`ns]!enlist cfg`ns];

    .qsrc.validate_fixture cfg`source;
    .qlog.dbg[worker;"init: source fixture satisfies the contract";enlist[`source]!enlist cfg`source];

    / Validate the ledger's shape before trusting a read of it (#60). Only
    / bites when the ledger already existed, i.e. when another process
    / created it - which is exactly when its shape is evidence rather than
    / our own assumption.
    .qmatz.attach[];

    / Same reasoning one table over: create it and verify its shape
    / here, in a live path, rather than leaving a checker that never fires.
    .qhb.attach[];
    .qhb.beat[worker;`starting];

    .qbfstate.acquire_lock worker;

    / Live only when a credential is configured. An absent credential is an
    / explicit statement that this is a demo, NOT a fallback for a failed
    / connection - falling back on failure would turn an outage into
    / silently synthetic data that coverage then records as complete.
    / A WARN that says how to fix it, not just that it happened: the variable
    / to set, what its value looks like for this source's transport (an ODBC
    / connection string, or host:port for kdb+ IPC), an example, and where it
    / has to be exported - the shell `uqs backfill` runs in, whose environment
    / the process inherits. Not an error: no credential is the declared way
    / to run on the fixture, and a demo stack runs like that on purpose.
    / `odbc`, not `var`: var is a q builtin (variance).
    live:.qsrc.has_credentials cfg`source;
    if[not live;
        odbc:`odbc~(.qsrc.def cfg`source)`transport;
        .qlog.warn[worker;"no credential - running on the source's fixture, not live data. To go live: export the variable below in the shell you run `uqs backfill` from, then run it again. It is read from the environment only - no flag, file or vault, so the secret stays off the command line";
            `variable`expects`example!(
                .qsrc.credential_var cfg`source;
                $[odbc; "an ODBC connection string"; "host:port, or host:port:user:password"];
                $[odbc;
                    "DRIVER=SingleStore ODBC Driver;SERVER=<host>;PORT=3306;DATABASE=<db>;UID=<user>;PWD=<password>";
                    "localhost:5010"])]];
    write_state[worker;`handle;$[live; connect worker; 0Ni]];

    .qlog.register[];
    .qlog.info[worker;"initialised";
        `source_version`range_from`range_to`live!
        (run_spec`source_version;run_spec`range_from;run_spec`range_to;
         not null read_state[worker;`handle])];

    spec worker}

/ Private: open the live connection. Separate from init so the failure is
/ attributable, and so a test can exercise init without one.
/ .
/ The source's transport picks the opener: an ipc credential is host:port,
/ an odbc credential is a connection string.
connect:{[worker]
    source:(def worker)`source;
    cred:.qsrc.require_credentials source;
    transport:(.qsrc.def source)`transport;
    .qlog.dbg[worker;"connecting to the source";`source`transport!(source;transport)];
    opener:$[`odbc~transport;
        {.qodbc.open x};
        {hopen (hsym `$":",x;5000j)}];
    @[opener;cred;
        {[source;e] '"connect: cannot reach the ",string[source]," source (",e,") - refusing to start rather than falling back to the fixture, which would record synthetic data as covered"}[source]]}

/ ------------------------------------------------------------------ PLAN

empty_windows:{[] ([] range_from:`timestamp$(); range_to:`timestamp$())}

/ The windows still to do.
/ .
/ ONE narrowing: coverage. Every gap in the range is planned, at this
/ source_version, whatever the cursor says - the body below explains what
/ went wrong when the cursor was a hard lower bound, and `cursor` is now an
/ unread parameter kept for the signature its callers already pass.
/ .
/ This header used to describe TWO narrowings, cursor first and coverage
/ second, which is what the function did before a restatement could withdraw
/ coverage the cursor had already passed. The body changed and the header did
/ not, so the two disagreed about the thing the function is for.
plan:{[worker;cursor]
    cfg:def worker;
    s:spec worker;
    / ONE as_of for the whole plan, captured here rather than read per call
    / Calling .z.p inside each coverage read would plan against a
    / ledger that could be superseded midway, so a range could be reported
    / both covered and uncovered within a single planning pass - and the
    / resulting window list would correspond to no coherent belief about the
    / data at any instant.
    as_of:.z.p;
    / COVERAGE DECIDES WHAT IS LEFT. The cursor is a resumption hint and
    / nothing more.
    / .
    / This used to plan from the cursor as a hard lower bound, and the
    / cursor of a finished run is range_to. So after a restatement withdrew
    / a window's coverage, a re-run with the same spec found its
    / cursor at the end, consulted coverage for nothing, and reported idle
    / over a range the ledger itself said was missing - supersede worked and
    / nothing would ever refill what it withdrew. advanced_to's own comment
    / had even described the shape ("they simply never get filled while
    / that checkpoint stands") and accepted it. Found by
    / tests/q/run_two_instances.q, the first place a supersede was followed
    / by a same-spec re-run.
    / .
    / Now every gap in the whole range is planned, whatever the cursor says.
    / The cursor still does the one thing it is for: when the range BEHIND it
    / is fully covered - the ordinary resume after an interruption - the
    / gaps all lie at or after it and the plan starts there, exactly as
    / before. When a gap lies behind it, the gap wins, because a gap is a
    / fact about the data and a cursor is a note about a previous run.
    todo:.qwrt.remaining[cfg`dataset;cfg`partition;s`source_version;as_of;s`range_from;s`range_to];
    if[0=count todo; :empty_windows[]];
    / one set of windows per uncovered sub-range, then flattened - a gap in
    / the middle must not be bridged by a window spanning it.
    ws:raze {[width;w] .qwrt.windows[w`range_from;w`range_to;width]}[cfg`width] each todo;
    .qlog.dbg[worker;"planned";`gaps`windows`width`cursor!(count todo;count ws;cfg`width;cursor)];
    ws}

/ ------------------------------------------------------- FETCH / PUBLISH

/ Fetch one window under the retry policy.
/ .
/ Transport failures retry; data failures do not, because retrying a schema
/ mismatch produces the same mismatch more slowly. with_retry returns a dict
/ rather than throwing, so the caller decides what a terminal failure means -
/ here, the window fails and the run moves on.
/ .
/ Every fetched window is validated against the source contract.
/ Not belt-and-braces: a source that dropped a column returns rows where the
/ missing column reads as a NULL in most q code, so without this the worker
/ publishes nulls and records the window as covered.
fetch:{[worker;from_ts;to_ts]
    cfg:def worker;
    h:read_state[worker;`handle];
    r:.qwrt.with_retry[.qwrt.policy[];
        {[source;h;from_ts;to_ts] last .qsrc.fetch_window[source;h;from_ts;to_ts]}[cfg`source;h;from_ts;to_ts]];
    .qlog.dbg[worker;"fetch attempted";
        `range_from`range_to`state`attempts`rows!(from_ts;to_ts;r`state;r`attempts;
            $[`ok~r`state; count r`result; 0N])];
    if[`failed~r`state; :r];
    .qsrc.validate[cfg`source;r`result];
    r}

/ Publish a window's rows into the source's declared target.
/ .
/ Returns the row count, which finish_window records as rows_published. Zero
/ is legal and meaningful: an empty window is positive evidence the
/ range was examined and held nothing.
publish:{[worker;batch]
    cfg:def worker;
    t:.qsrc.def[cfg`source]`target;
    .qio.write[.qio.for_cfg cfg;t;batch]}

/ Save the cursor. Present because the contract requires it; the
/ write goes through .qbfstate so the checkpoint's spec-binding is not
/ re-implemented.
checkpoint:{[worker;cursor] .qbfstate.save_checkpoint[worker;spec worker;cursor]}

/ ------------------------------------------------------------------- RUN

/ Run one bounded pass to completion.
/ .
/ a window that exhausts its retries is TERMINAL for that window and
/ the run continues: failing the whole pass would throw away the windows that
/ did succeed, and their coverage is what makes the retry cheap. Failed
/ windows stay uncovered, so the next run plans them again.
run:{[worker]
    / One run identity for the whole execution, so every window
    / this run materialises is attributable to it and to each other. Begun
    / before the first window and closed with the run's own outcome, so an
    / execution that dies mid-flight leaves a row reading `running` rather
    / than leaving no trace - see .qrun's header.
    begin_run[worker];
    cursor:.qbfstate.load_checkpoint[worker;spec worker];
    windows:own[worker;`plan][cursor];
    if[0=count windows;
        / "ran, found no work" is a SUCCESS, not a failure. An
        / orchestrator that cannot tell them apart retries a successful
        / no-op forever.
        / INF, not DBG: "the run did nothing" is the question this answers.
        s:spec worker;
        .qlog.info[worker;"idle - every window in the range is already covered at this source_version";
            `source_version`range_from`range_to!(s`source_version;s`range_from;s`range_to)];
        .qhb.beat[worker;`idle];
        end_run[`idle];
        :`state`windows_completed`windows_failed`rows_published`cursor!
            (`idle;0;0;0;cursor)];
    / The run's OWN cursor starts null, not at the loaded checkpoint. The
    / checkpoint's job was to inform the plan, and the plan is made; from
    / here the cursor tracks what THIS run has published, in the order it
    / publishes it. Seeding it from the checkpoint broke restatement: a
    / finished run's checkpoint is range_to, so the first refilled window
    / ended at or before it and advanced_to refused a cursor that "stood
    / still" - the strict-forward rule, correct within a run, applied
    / across two. The run then died after doing the work but before
    / recording it.
    write_state[worker;`progress;`windows_completed`windows_failed`rows_published`cursor!(0;0;0;0Np)];
    .qhb.beat[worker;`running];
    do_window[worker] each windows;
    / The io manager's end-of-run step, after the LAST window and whatever
    / its outcome: a window that failed wrote nothing, but the ones that
    / succeeded may have left a store that is not finished data until this
    / runs (the HDB writer sorts and attributes its partitions here). A
    / manager with no finish - memory, discard - makes this a no-op, and a
    / dry run, which wrote nothing, gives it nothing to do.
    .qio.finish .qio.for_cfg def worker;
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
/ Wrapped for the same reason .qmatz.current_run is: run.q is not a load-time
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
    cfg:def worker;
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
    cfg:def worker;
    columns:(.qsrc.def cfg`source)`columns;
    nm:cfg`transform;
    .qxf.apply[nm;(.qxf.input_names nm)!enlist columns#batch]}

/ Private: one window, end to end - fetch, transform, check, publish, record.
/ .
/ Accumulates into the worker's own `progress` rather than returning, because
/ a q lambda does not close over an enclosing local and `each` over windows
/ needs somewhere to put the totals.
/ @param worker the worker's name
/ @param w a row carrying range_from and range_to
/ @return 1b when the window completed, 0b when it failed and the run
/   should continue with the next one
do_window:{[worker;w]
    cfg:def worker;
    .qlog.dbg[worker;"window start";`range_from`range_to!(w`range_from;w`range_to)];
    f:own[worker;`fetch][w`range_from;w`range_to];
    if[`failed~f`state;
        / ERR, not a throw: a failed window is terminal for that
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
    / same terminal-window path as a failed fetch: nothing published,
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
    /: the window is not published, no coverage is staged, the run
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
    / Materialisation metadata, recorded HERE rather than in
    / finish_window because this is the only place the batch itself is in
    / hand - finish_window receives a niladic publisher, not rows.
    record_facts[worker;cfg;w;out;r];
    / THE PUBLICATION EVENT (.qreact). One place rows enter a dataset on this
    / path, so this is where downstream work hears about it - with the range
    / in hand, rather than a timer discovering it later by diffing coverage.
    / .
    / After record_facts, so a reaction reading the materialisation sees it.
    / Suppressed on a dry run, which published nothing: firing there would
    / make a rehearsal trigger real downstream work.
    / .
    / Protected like begin_run for the same reason - react.q is not a load
    / time dependency and a minimal loader must still run a worker.
    if[not r`dry_run;
        @[{[a] .qreact.notify_from_here . a};
          (cfg`dataset;w`range_from;w`range_to);
          {[e] (::)}]];
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
/ It was asked whether backfill is strictly oldest-first, and whether the order
/ matters to correctness or only to observability. It is oldest-first by
/ construction - windows[] builds starts as from_ts+width*til n, and remaining
/ hands back ascending sub-ranges. The order used to matter to CORRECTNESS:
/ plan[] took this cursor as a hard lower bound, so a window processed out
/ of order pushed the cursor past windows still uncovered and "they simply
/ never got filled while that checkpoint stood". plan[] no longer does that
/ - coverage decides what is left, and a gap behind the cursor is planned -
/ so the consequence is gone. The invariant stays, because a cursor that
/ moves backwards or stands still would still mean a window was processed
/ out of the order the plan produced, which is a bug worth refusing loudly
/ rather than one the new plan[] happens to survive.
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

publish_last_batch:{[worker;unused] own[worker;`publish] read_state[worker;`last_batch]}

/ Release everything the worker holds. Safe on the failure branch too, since
/ release_lock is a no-op when not held.
cleanup:{[worker]
    h:read_state[worker;`handle];
    closer:$[`odbc~(.qsrc.def (def worker)`source)`transport;
        .qodbc.close;
        {[h] @[hclose;h;::]}];
    if[not null h; closer h; write_state[worker;`handle;0Ni]];
    .qbfstate.release_lock worker}

\d .
