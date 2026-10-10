/ worker_runtime.q - retry classification, dry-run, and the authority split
/ (.qetl.job.bounded.runtime).
/ .
/ Implements the retry decisions recorded on issues #71/#72.
/ .
/ The guarantee this file provides is deliberately WEAKER than the one people
/ assume, in as many words: do not assume exactly-once processing - the
/ framework establishes retry-safe publication and coverage skipping, which
/ is a weaker and more honest guarantee. So a window may be
/ fetched twice; what must not happen is that a window is published twice
/ without the ledger knowing, or skipped while reporting success.
/ .
/ Three things here are easy to get backwards, so each is enforced rather
/ than commented:
/ .
/   1. Data failures do NOT retry. Retrying a schema mismatch produces
/      the same mismatch more slowly and buries the real error under N
/      identical ones. Only transport failures retry.
/   2. Unclassifiable errors are treated as DATA, i.e. terminal. Defaulting
/      the other way means a genuine bug retries until the backoff cap and
/      then fails anyway, having hidden itself for the duration.
/   3. Dry-run must suppress THREE side effects, not one. Suppressing
/      only the row publication leaves coverage claiming the window is
/      complete - the exact lie the ledger exists to prevent.

\d .qetl.job.bounded.runtime

/ ------------------------------------------------------------- AUTHORITY

/ The authority split, written down as data so a diagnostic can cite it and a test
/ can assert nobody quietly moved a concern across the line.
q_owned:`startup`source_reads`query_failures`checkpoints`run_counts`window_counts`coverage_events
airflow_owned:`task_ordering`scheduling`retries`timeouts`concurrency`alert_routing

/ The split assigns RETRIES to Airflow, and this file retries transport
/ errors in-process. That reads like a contradiction and is not, so state the
/ resolution plainly rather than leaving the next reader to reconcile it:
/ .
/   - in-process retry is WITHIN one task attempt, for a transient blip on a
/     connection this process already holds. Airflow cannot help there; by
/     the time a task fails and is rescheduled the cheap fix is long gone.
/   - task-level retry - "run this task again" - is Airflow's, and nothing
/     here reschedules itself, re-enqueues work, or decides how many task
/     attempts there should be.
/ .
/ The line is therefore: bounded, in-attempt, transport-only here; everything
/ about whether and when the TASK runs again is Airflow's.
retry_boundary:"in-attempt transport retry is q's; task-level retry is Airflow's"

/ Which layer owns a concern, for a diagnostic that would otherwise guess.
/ @throws error if the concern belongs to neither layer
/ @eg .qetl.job.bounded.runtime.owner[`scheduling]  ->  `airflow
owner:{[concern]
    $[concern in q_owned; `q;
      concern in airflow_owned; `airflow;
      '"owner: ",string[concern]," is not an assigned concern - the authority split is a fixed list, so an unlisted concern means the split needs amending, not guessing"]}

/ ------------------------------------------------------ CLASSIFICATION

/ Transport failures: the connection, the host, the socket. Retryable,
/ because the next attempt genuinely may differ.
transport_patterns:("*connection*";"*timeout*";"*timed out*";"*broken pipe*";
                    "*refused*";"*reset*";"*unreachable*";"*temporarily unavailable*";
                    "*no route*";"*handle*";"*closed*")

/ Data failures: the payload is wrong. Terminal, because the next attempt is
/ identical.
data_patterns:("*schema*";"*type*";"*cast*";"*parse*";"*length*";
               "*mismatch*";"*null*";"*domain*";"*not covered*";"*invalid*")

/ Classify a caught error string as `transport or `data.
/ .
/ Data patterns are tested FIRST, because the overlap is real and the safe
/ direction is terminal: "schema mismatch on connection to rdb" mentions a
/ connection but retrying it is pointless. Getting this order wrong turns one
/ clear failure into max_attempts identical ones.
/ .
/ An unrecognised error is `data, i.e. terminal - see the header's point 2.
/ @param err the caught error string
/ @return `transport or `data
/ @eg .qetl.job.bounded.runtime.classify["connection refused"]  ->  `transport
classify:{[err]
    e:lower err;
    $[any e like/: data_patterns;      `data;
      any e like/: transport_patterns; `transport;
      `data]}

/ Is this error worth another attempt in this process?
retryable:{[err] `transport~classify[err]}

/ ------------------------------------------------------------ RETRYING

/ Bounded backoff, config-driven. Defaults are deliberately small:
/ this is an in-attempt blip, not a scheduling policy - a worker sitting in
/ backoff for minutes is Airflow's job to time out, not this file's job to
/ wait through.
default_policy:`max_attempts`base_delay_ms`max_delay_ms!(3j;250j;8000j)

/ Resolve the policy from configuration, falling back to the defaults.
/ .
/ Read through .qetl.cfg so the precedence is the settled one, rather than a
/ second ad-hoc lookup order that drifts from it.
policy:{[]
    read_one:{[k;fallback]
        v:@[{.qetl.cfg.raw x};k;{""}];
        $[0=count v; fallback; null j:"J"$v; fallback; j]};
    `max_attempts`base_delay_ms`max_delay_ms!(
        read_one[`retry_max_attempts;  default_policy`max_attempts];
        read_one[`retry_base_delay_ms; default_policy`base_delay_ms];
        read_one[`retry_max_delay_ms;  default_policy`max_delay_ms])}

/ Exponential backoff for attempt n (1-based), capped.
/ @eg .qetl.job.bounded.runtime.backoff_ms[.qetl.job.bounded.runtime.default_policy;3]  ->  1000
backoff_ms:{[pol;attempt]
    raw:"j"$(pol`base_delay_ms)*2 xexp attempt-1;
    (pol`max_delay_ms) & raw}

/ Private: wait, in milliseconds. Zero and negative waits do nothing, which
/ is what lets a test set base_delay_ms to 0 and run at full speed rather
/ than sleeping through its own retry assertions.
/ @private
sleep_ms:{[ms] if[ms>0; system"sleep ",string ms%1000]; ms}

/ Run a niladic function under the retry policy.
/ .
/ Returns a dict rather than throwing, because the CALLER decides what a
/ terminal failure means: a worker's failed window is terminal and
/ the worker moves on, which is a decision about the run, not about this
/ function.
/ @param pol a policy dict as returned by `policy`
/ @param f a niladic function performing the attempt
/ @return dict of state (`ok or `failed), kind (`none/`transport/`data),
/   attempts, result (on success) and error (on failure)
/ @eg .qetl.job.bounded.runtime.with_retry[.qetl.job.bounded.runtime.default_policy;{42}]`result  ->  42
with_retry:{[pol;f]
    / `cap`, not `max` - max is a q builtin, and shadowing it inside the
    / lambda breaks the & fallback below in a way that reports as `nyi at
    / call time. Third time this trap has bitten this repository.
    cap:pol`max_attempts;
    attempt:1;
    outcome:(`err;"with_retry: attempt function never ran");
    while[attempt<=cap;
        / The attempt joins the log context, so each retry's requests read
        / as attempt 2, 3... of the same window.
        outcome:.qetl.log.with_context[enlist[`attempt]!enlist attempt;
            {@[{(`ok;x[])};x;{(`err;x)}]};enlist f];
        if[`ok~first outcome;
            :`state`kind`attempts`result`error!(`ok;`none;attempt;last outcome;"")];
        kind:classify[last outcome];
        .[{.qetl.log.dbg[x;y;z]};(`qetl.job.bounded.runtime;"attempt failed";
            `attempt`of`kind`error!(attempt;cap;kind;last outcome));::];
        if[kind=`data;
            :`state`kind`attempts`result`error!(`failed;`data;attempt;::;last outcome)];
        if[attempt=cap;
            :`state`kind`attempts`result`error!(`failed;`transport;attempt;::;last outcome)];
        wait:backoff_ms[pol;attempt];
        .[{.qetl.log.warn[x;y;z]};(`qetl.job.bounded.runtime;"retrying after a transport error";
            `attempt`of`backoff_ms`error!(attempt;cap;wait;last outcome));::];
        sleep_ms[wait];
        attempt+:1];
    `state`kind`attempts`result`error!(`failed;`transport;cap;::;last outcome)}

/ ------------------------------------------------------------- RUN MODES
/ .
/ What a bounded run is asked to do, each mode defined by what it may TOUCH:
/ .
/   validate  configuration and code only: the declaration, the contract, the
/             fixture, the range. Reads no ledger, opens no source.
/   plan      validate, plus the local ledgers read-only: which windows a run
/             would fetch, which are already covered. Opens no source.
/   dry_run   plan, plus the source: fetch, transform, check, count. Writes
/             nothing durable.
/   run       all of it.
/ .
/ validate and plan never reach the window loop - .qetl.job.bounded.validate
/ and .qetl.job.bounded.plan_only are separate entry points - so the gate
/ below only has to hold the line between dry_run and run.
modes:`validate`plan`dry_run`run

/ EVERY durable effect a run makes, named once. A mode other than `run makes
/ none of them, and an effect not on this list cannot be gated at all - so a
/ new one must be added here, which is the point: dry runs used to leak
/ because this list held three effects and the run ledger, the facts, the
/ store's finish and the reactions simply never reached it.
/ .
/ Suppressing a SUBSET is the dangerous case: rows withheld while coverage is
/ still published leaves the ledger asserting a window is complete when
/ nothing was written.
suppressed_in_dry_run:`publish_rows`publish_coverage`write_checkpoint`record_run`record_facts`finish_store`notify_reactions

/ This run's mode: `mode` (UQF_MODE, or `uqs backfill --mode`) when set, else
/ `dry_run when the older dry_run flag is set, else `run.
/ .
/ An explicit `run with the dry_run flag also set is refused rather than
/ resolved: one of the two settings is a mistake, and guessing which means
/ either writing when someone asked not to or not writing when they asked to.
/ @return one of .qetl.job.bounded.runtime.modes
/ @throws a mode not in modes, or `run together with the dry_run flag
/ @eg .qetl.job.bounded.runtime.mode[]  ->  `run
mode:{[]
    / Protected like the flag: a loader without .qetl.cfg has no mode set,
    / which is not the same as a mode set wrongly.
    m:@[{.qetl.cfg.raw `mode};::;{[e] ""}];
    dry:@[{.qetl.cfg.get_flag `dry_run};::;{0b}];
    if[0=count m; :$[dry; `dry_run; `run]];
    m:`$lower m;
    if[not m in .qetl.job.bounded.runtime.modes;
        '"mode: ",string[m]," is not one of ",", " sv string .qetl.job.bounded.runtime.modes];
    if[dry and m~`run; '"mode: run, but dry_run is also set - unset one of them"];
    m}

/ Is this run diagnostic-only - any mode but `run?
/ .
/ Not protected: a mode that cannot be read throws here, and the effect it
/ was guarding fails with it. Defaulting a broken setting to "write" is how a
/ rehearsal turns into a run.
is_dry_run:{[] not `run~mode[]}

/ May this run make `effect`?
/ @param effect one of suppressed_in_dry_run
/ @return 1b only in mode `run
/ @throws an effect not on the list, so a new one cannot bypass the gate
/ @eg .qetl.job.bounded.runtime.allows `record_run  ->  1b
allows:{[effect]
    if[not effect in .qetl.job.bounded.runtime.suppressed_in_dry_run;
        '"allows: ",string[effect]," is not one of the effects a dry run suppresses - add it to suppressed_in_dry_run"];
    not is_dry_run[]}

/ Perform one named side effect, or record that dry-run withheld it.
/ .
/ One of the TWO ways an effect is gated, and both consult the same list,
/ suppressed_in_dry_run. This one serves finish_window's three effects, whose
/ order matters and whose outcomes are reported back. The other four are
/ guarded at their call sites in bounded_worker.q through `allows`, because
/ each is a single protected call with nothing to report. Neither is the
/ guarantee: tests/q/test_every_worker_runs.q compares everything durable
/ before and after a dry run of every worker, which catches an effect that
/ reaches neither.
/ Takes the action and its arguments SEPARATELY, applying them with `.` only
/ on the non-dry branch. That is the whole point: a fully-applied projection
/ like f[a;b;c] is not a deferred call in q, it is a CALL - so writing
/ commit[dry;`publish_rows;publish[a;b]] would perform the publication while
/ building the argument, and dry-run would suppress only the recording of an
/ effect that had already happened. The bug would be invisible in the return
/ value, which would correctly say `skipped.
/ @param dry whether this is a dry run
/ @param effect one of suppressed_in_dry_run
/ @param action the function performing the effect
/ @param args a list of arguments for it; () for a niladic action, which is
/   applied as `enlist(::)` because `f . ()` is a type error, not a call
/ @return (`skipped;effect) or (`done;effect;the action's result)
/ @throws error on an unrecognised effect name
commit:{[dry;effect;action;args]
    if[not effect in suppressed_in_dry_run;
        '"commit: ",string[effect]," is not one of the effects a dry run suppresses (",
         (", " sv string suppressed_in_dry_run),") - add it there deliberately rather than bypassing the gate"];
    / `f . ()` is a TYPE error rather than a niladic call; the unary-null
    / argument list is what actually applies a niladic function.
    applied:$[0=count args; enlist(::); args];
    if[dry; .[{.qetl.log.dbg[x;y;z]};(`qetl.job.bounded.runtime;"dry run - skipped";enlist[`effect]!enlist effect);::]];
    $[dry; (`skipped;effect); (`done;effect;action . applied)]}

/ ----------------------------------------------------------- WINDOWS

/ Split [from_ts;to_ts) into consecutive half-open windows of `width`.
/ .
/ "Window boundaries" is one of the six lifecycle decision points
/ where a wrong answer is silent, and this is where that answer lives. Two
/ properties make it right, and both are tested:
/ .
/   - the windows TILE the range: each one's range_to is the next one's
/     range_from, with no overlap and no gap. Overlap double-publishes;
/     a gap leaves data unfetched while coverage composes cleanly over the
/     whole range and reports it complete.
/   - the LAST window is clipped to to_ts, never extended past it. An
/     over-running final window records coverage for a range that was never
/     requested, which a later run then skips.
/ .
/ WHAT A "DAY" IS HERE (issue #80)
/ .
/ A 1D window is 24h of ELAPSED UTC TIME measured from from_ts. It is not a
/ calendar day, not a business date, and not aligned to any venue's session:
/ a range starting at 17:00 produces windows starting at 17:00, and a
/ five-day range over a weekend is five windows, not five trading days.
/ .
/ WHAT DEFINES A TRADING DAY FOR THE CANONICAL WORKERS IS NOT KNOWABLE FROM
/ THIS TREE - that answer lived with the bank's calendars, which being a
/ public repository keeps
/ out of a public repository. So the assumption is written down instead of
/ guessed at, and tests/q/test_time_zone.q pins it: whoever adds a venue
/ calendar has to change a failing test rather than a comment. Nothing else
/ here has a business-date notion either - .qetl.coverage composes half-open
/ intervals and .qdcf counts actual calendar days - and the only day roll
/ this tree knows about is TorQ's EOD reload, which is an operational state
/ rather than a business date.
/ .
/ Cutting in UTC is also what makes DST harmless: a daily window is
/ exactly 24h across a transition, never the 23h or 25h a local calendar day
/ becomes, so coverage keeps tiling exactly. The variable local span is
/ handled where it belongs, in .qetl.source's per-source zone conversion.
/ @param from_ts range start
/ @param to_ts range end, exclusive
/ @param width a timespan, e.g. 1D
/ @return a table of range_from/range_to
/ @eg .qetl.job.bounded.runtime.windows[2026.09.01D00:00;2026.09.04D00:00;1D]  -> 3 daily windows
windows:{[from_ts;to_ts;width]
    .qetl.coverage.require_interval[from_ts;to_ts];
    if[not width>0D00:00;
        '"windows: width must be positive, got ",string width];
    / Integer arithmetic: the float divide loses a nanosecond tail once the
    / range passes ~104 days (2^53 ns), dropping the last window (#975).
    span:"j"$to_ts-from_ts;
    w:"j"$width;
    n:(span div w)+0<span mod w;
    starts:from_ts+width*til n;
    ([] range_from:starts; range_to:to_ts&starts+width)}

/ ----------------------------------------------------- COVERAGE SKIPPING

/ Should this window be fetched, or is it already published?
/ .
/ Version-specific by construction: is_covered takes source_version as a
/ required parameter, so a window covered at v1 does NOT suppress a fetch at
/ v2. That is the never-merge-versions rule, in the direction that matters - the alternative
/ skips a re-extraction the version bump exists to force.
/ .
/ Partition-specific for the same reason and in the same direction: a window
/ covered for `EURUSD does not suppress the fetch for `USDJPY. That is what
/ lets one dataset be backfilled by several workers at once (#185) - without
/ it, the first partition to finish would tell every other partition its work
/ was already done.
/ @param part this worker's partition, or ` when its dataset has none
/ @return 1b when the window still needs fetching
needs_fetch:{[ds;part;version;as_of;from_ts;to_ts]
    not .qetl.coverage.is_covered[ds;part;version;as_of;from_ts;to_ts]}

/ Narrow a requested range to the parts not yet published.
/ .
/ Returns the gaps rather than a yes/no, so a retry after a partial run
/ re-fetches only what is missing instead of the whole range. An empty result
/ means there is nothing to do - which is an `idle success, not a
/ failure.
remaining:{[ds;part;version;as_of;from_ts;to_ts]
    m:.qetl.coverage.missing[ds;part;version;as_of;from_ts;to_ts];
    / The answer to "why did the run do nothing": every window already covered
    / at this version, so there is nothing left to fetch.
    .[{.qetl.log.dbg[x;y;z]};(ds;"coverage gaps";
        `partition`source_version`range_from`range_to`gaps!(part;version;from_ts;to_ts;count m));::];
    m}

/ Complete one window: publish rows, record coverage, save the checkpoint -
/ in that order, and all three behind the dry-run gate.
/ .
/ The ORDER is the requirement, not an implementation detail. Coverage is
/ staged only after the publication it describes has happened, and the
/ checkpoint only after coverage, so every possible interruption point leaves
/ an under-claim rather than an over-claim: a re-run redoes work, which
/ retry-safe publication tolerates, instead of skipping work the ledger
/ wrongly believes is done.
/ @param worker the worker's name
/ @param ds the dataset
/ @param part the partition this worker fills, or ` when its dataset has no
/   partition dimension
/ @param spec the run specification (source_version, range_from, range_to)
/ @param from_ts window start
/ @param to_ts window end, exclusive
/ @param publish a niladic function publishing the window, returning a row
/   count
/ @return dict of the three effects' outcomes plus the row count
finish_window:{[worker;ds;part;spec;from_ts;to_ts;publish]
    dry:is_dry_run[];
    published:commit[dry;`publish_rows;publish;()];
    rows:$[`done~first published; last published; 0];
    covered:commit[dry;`publish_coverage;.qetl.coverage.stage_completion;
        (ds;part;spec`source_version;from_ts;to_ts;rows)];
    checkpointed:commit[dry;`write_checkpoint;.qetl.job.bounded.state.save_checkpoint;
        (worker;spec;to_ts)];
    .[{.qetl.log.dbg[x;y;z]};(worker;"window finished";
        `range_from`range_to`rows`dry_run!(from_ts;to_ts;rows;dry));::];
    `dry_run`rows_published`published`covered`checkpointed!
        (dry;rows;published;covered;checkpointed)}

/ ------------------------------------------------- FIXTURE CLAIMS (#1082)
/ A run with no credential records its windows under the release tagged
/ ~fixture (.qetl.source.fixture_version), so they never satisfy a live
/ run's plan. The live run that follows still finds the fixture's ROWS in
/ the dataset, and these clear them and withdraw the fixture's claims.

/ The release a run records coverage under: the one asked for, with the
/ worker's output revision when it declares one (#1097), and tagged when the
/ source has no credential (#1082) - so validate, plan, the run and its
/ ledgers all see one identity. A null is left for check_static to refuse.
/ @param cfg the worker's declaration, as .qetl.job.bounded.def returns it
/ @param v the release asked for
/ @return v, as `v@r<revision>` when revised, then `~fixture` when on the fixture
/ @eg .qetl.job.bounded.runtime.run_version[`source`revision!(`demo_deals;2);`v1]  ->  `$"v1@r2~fixture"
run_version:{[cfg;v]
    if[null v; :v];
    if[not null r:$[`revision in key cfg; cfg`revision; 0N]; v:`$string[v],"@r",string r];
    $[.qetl.source.has_credentials cfg`source; v; .qetl.source.fixture_version v]}

/ The output revision a worker declares (#1097), or 0N for none. Coverage
/ says which windows are done under a source release, and nothing about the
/ logic that made them: change a transform or a scoring rule under the same
/ release, and the old windows stay covered while new ones use the new
/ logic - one dataset, two definitions. Bump the revision with the logic and
/ every window is planned again, under `<release>@r<revision>`; old claims
/ stay in the ledger under their own identity and stop answering.
/ @param worker the worker's name
/ @param decl its declaration
/ @return the revision, a positive long, or 0N
/ @throws error when it is not a positive long
/ @eg .qetl.job.bounded.runtime.require_revision[`w;enlist[`revision]!enlist 2]  ->  2
require_revision:{[worker;decl]
    if[not `revision in key decl; :0N];
    r:decl`revision;
    / 0N is "none": a declaration copied from .qetl.job.bounded.def carries it
    if[(-7h=type r) and null r; :0N];
    / Type first, alone: q evaluates both sides of `and`, and `r2>0 is a 'type.
    bad:"define: ",string[worker],"'s revision must be a positive long, e.g. 2 - bump it when the output logic changes";
    if[not -7h=type r; 'bad];
    if[r<1; 'bad];
    r}

/ Refuse a run on `source`'s fixture: always under a deployment's --live
/ (#800), and otherwise unless fixture writes were asked for - a dry run
/ writes nothing, so it may still read the fixture.
/ @param who the caller, leading the message
/ @param source the source that has no credential
/ @throws error naming the source, its variable and the way out
refuse_fixture:{[who;source]
    .qetl.source.refuse_fixture[who;source];
    if[not is_dry_run[]; .qetl.source.refuse_fixture_writes[who;source]]}

/ Did a run on the fixture claim any of this window? Only a live run asks -
/ a fixture run's own claims are under the tagged release.
/ @param worker the worker's name
/ @param w the window, a dict of range_from and range_to
/ @return 1b when a current fixture claim overlaps the window
fixture_claimed:{[worker;w]
    cfg:.qetl.job.bounded.def worker;
    if[not .qetl.source.has_credentials cfg`source; :0b];
    v:.qetl.source.fixture_version .qetl.job.bounded.read_state[worker;`source_version];
    iv:.qetl.coverage.intervals[cfg`dataset;cfg`partition;v;.z.p];
    any (iv[`range_from]<w`range_to) and w[`range_from]<iv`range_to}

/ The strategy a window is written under: the run's own, unless a fixture
/ run claimed the window. Its rows are then still there, and an upsert would
/ keep every one whose key the live data does not repeat - so `replace,
/ which clears the window first, when the worker has a window_column. One
/ without upserts, and says what may be left behind.
/ @param worker the worker's name
/ @param w the window, a dict of range_from and range_to
/ @return the strategy, a symbol
write_strategy:{[worker;w]
    oc:.qetl.job.bounded.on_conflict worker;
    if[not fixture_claimed[worker;w]; :oc];
    if[not null (.qetl.job.bounded.def worker)`window_column; :`replace];
    .qetl.log.warn[worker;"a fixture run wrote this window, and with no window_column its rows cannot be cleared - fixture rows the live data does not overwrite stay";
        `range_from`range_to`on_conflict!(w`range_from;w`range_to;oc)];
    oc}

/ Withdraw the fixture's claims on the window just published. After the
/ write and before finish_window stages the live claim, so an interruption
/ between them leaves the window claimed by neither - an under-claim, which a
/ re-run repairs - never by both.
/ @param worker the worker's name
/ @param rows what the publish returned, passed through
/ @return rows
retire_fixture_claims:{[worker;rows]
    w:.qetl.job.bounded.read_state[worker;`last_window];
    if[not fixture_claimed[worker;w]; :rows];
    cfg:.qetl.job.bounded.def worker;
    v:.qetl.source.fixture_version .qetl.job.bounded.read_state[worker;`source_version];
    n:.qetl.coverage.supersede[cfg`dataset;cfg`partition;v;w`range_from;w`range_to];
    .qetl.log.info[worker;"live rows replace a fixture run's window";`range_from`range_to`claims!(w`range_from;w`range_to;n)];
    rows}

/ ---------------------------------------------------------- DEPENDENCIES

/ worker -> the TorQ process types it needs a connection to.
declared_dependencies:(`symbol$())!();

/ Declare what a worker needs before it can run.
/ @eg .qetl.job.bounded.runtime.declare_dependencies[`markout_backfill;`tickerplant`hdb]
declare_dependencies:{[worker;procs]
    declared_dependencies[worker]:procs;
    procs}

/ Private: which process types currently have a live connection.
/ .
/ Reads TorQ's .servers.SERVERS when it is loaded, and returns empty
/ otherwise. Wrapped because this file is unit-tested outside a TorQ process,
/ where .servers does not exist at all - and an unwrapped read would make
/ every test here depend on a running stack.
/ .
/ Overridable via connected_override so a test can present a fleet without
/ one.
/ @private
connected_override:();

/ The proctypes currently reachable in the fleet.
/ .
/ Empty when TorQ is absent, which makes require_dependencies report every
/ declared dependency as missing and refuse to start - the direction a total
/ function has to pick, so that a broken probe stops a worker at init rather
/ than letting it run blind.
/ @return a symbol vector of proctypes, empty outside a TorQ process
/ @eg .qetl.job.bounded.runtime.connected[]
connected:{[]
    if[count connected_override; :connected_override];
    @[{exec distinct proctype from .servers.SERVERS where not null w};::;{`symbol$()}]}

/ Fail at INITIALISATION when a declared dependency is unavailable.
/ .
/ At init, not at first use: a worker that starts, runs for twenty minutes
/ and then discovers the hdb was never reachable has already published a
/ partial window and burned the operator's time. Reports every missing
/ dependency at once, for the same reason require_contract does.
/ @throws error naming every unavailable dependency
require_dependencies:{[worker]
    needed:declared_dependencies[worker];
    if[0=count needed; :worker];
    live:connected[];
    missing:needed where not needed in live;
    .[{.qetl.log.dbg[x;y;z]};(worker;"dependencies";`needed`connected`missing!(needed;live;missing));::];
    if[count missing;
        '"require_dependencies: ",string[worker]," cannot start - no connection to ",
         (", " sv string missing),
         " (declared dependencies must resolve through .servers at init)"];
    worker}

/ Fail at initialisation when an upstream dataset is not published for the
/ requested range (the "coverage precondition").
/ .
/ Distinct from needs_fetch above, which asks about THIS worker's own output.
/ This asks about its INPUT: a markout backfill over a range whose trades are
/ not yet published would compute markouts against missing trades and record
/ coverage saying it had done so.
/ .
/ The upstream's partition is a SEPARATE parameter from this worker's own,
/ and deliberately so: a per-symbol markout worker may depend on a trades
/ dataset that is unpartitioned, or partitioned differently. Assuming the two
/ share a partitioning would check the wrong slice and admit a run whose
/ input is missing.
/ @param upstream_part the upstream's partition, or ` when it has none
/ @throws error naming the missing upstream ranges
require_upstream:{[upstream;upstream_part;version;as_of;from_ts;to_ts]
    .qetl.coverage.require_covered[upstream;upstream_part;version;as_of;from_ts;to_ts]}

\d .
