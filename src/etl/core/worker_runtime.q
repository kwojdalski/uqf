/ worker_runtime.q - retry classification, dry-run, and the authority split
/ (.qwrt).
/ .
/ Implements requirements ETL-13 to ETL-17 of docs/reference/etl-framework-requirements.md,
/ and the retry decisions recorded on issues #71/#72 (question-bank M-04,
/ M-05).
/ .
/ The guarantee this file provides is deliberately WEAKER than the one people
/ assume. ETL-13 says so in as many words: "do not assume exactly-once
/ processing - the framework establishes retry-safe publication and coverage
/ skipping, which is a weaker and more honest guarantee." So a window may be
/ fetched twice; what must not happen is that a window is published twice
/ without the ledger knowing, or skipped while reporting success.
/ .
/ Three things here are easy to get backwards, so each is enforced rather
/ than commented:
/ .
/   1. Data failures do NOT retry (M-04). Retrying a schema mismatch produces
/      the same mismatch more slowly and buries the real error under N
/      identical ones. Only transport failures retry.
/   2. Unclassifiable errors are treated as DATA, i.e. terminal. Defaulting
/      the other way means a genuine bug retries until the backoff cap and
/      then fails anyway, having hidden itself for the duration.
/   3. Dry-run must suppress THREE side effects, not one (ETL-14). Suppressing
/      only the row publication leaves coverage claiming the window is
/      complete - the exact lie the ledger exists to prevent.

\d .qwrt

/ ------------------------------------------------------------- AUTHORITY

/ ETL-15's split, written down as data so a diagnostic can cite it and a test
/ can assert nobody quietly moved a concern across the line.
q_owned:`startup`source_reads`query_failures`checkpoints`run_counts`window_counts`coverage_events
airflow_owned:`task_ordering`scheduling`retries`timeouts`concurrency`alert_routing

/ ETL-15 assigns RETRIES to Airflow, and M-04 has this file retrying transport
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
/ @eg .qwrt.owner[`scheduling]  ->  `airflow
owner:{[concern]
    $[concern in q_owned; `q;
      concern in airflow_owned; `airflow;
      '"owner: ",string[concern]," is not an assigned concern - ETL-15 splits a fixed list, so an unlisted concern means the split needs amending, not guessing"]}

/ ------------------------------------------------------ CLASSIFICATION

/ Transport failures: the connection, the host, the socket. Retryable,
/ because the next attempt genuinely may differ (M-04).
transport_patterns:("*connection*";"*timeout*";"*timed out*";"*broken pipe*";
                    "*refused*";"*reset*";"*unreachable*";"*temporarily unavailable*";
                    "*no route*";"*handle*";"*closed*")

/ Data failures: the payload is wrong. Terminal, because the next attempt is
/ identical (M-04).
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
/ @eg .qwrt.classify["connection refused"]  ->  `transport
classify:{[err]
    e:lower err;
    $[any e like/: data_patterns;      `data;
      any e like/: transport_patterns; `transport;
      `data]}

/ Is this error worth another attempt in this process?
retryable:{[err] `transport~classify err}

/ ------------------------------------------------------------ RETRYING

/ Bounded backoff, config-driven (M-04). Defaults are deliberately small:
/ this is an in-attempt blip, not a scheduling policy - a worker sitting in
/ backoff for minutes is Airflow's job to time out, not this file's job to
/ wait through.
default_policy:`max_attempts`base_delay_ms`max_delay_ms!(3j;250j;8000j)

/ Resolve the policy from configuration, falling back to the defaults.
/ .
/ Read through .qwcfg so the precedence is the one C-02 fixed, rather than a
/ second ad-hoc lookup order that drifts from it.
policy:{[]
    read_one:{[k;fallback]
        v:@[{.qwcfg.raw x};k;{""}];
        $[0=count v; fallback; null j:"J"$v; fallback; j]};
    `max_attempts`base_delay_ms`max_delay_ms!(
        read_one[`retry_max_attempts;  default_policy`max_attempts];
        read_one[`retry_base_delay_ms; default_policy`base_delay_ms];
        read_one[`retry_max_delay_ms;  default_policy`max_delay_ms])}

/ Exponential backoff for attempt n (1-based), capped.
/ @eg .qwrt.backoff_ms[.qwrt.default_policy;3]  ->  1000
backoff_ms:{[pol;attempt]
    raw:"j"$(pol`base_delay_ms)*2 xexp attempt-1;
    (pol`max_delay_ms) & raw}

/ Private: wait, in milliseconds. Zero and negative waits do nothing, which
/ is what lets a test set base_delay_ms to 0 and run at full speed rather
/ than sleeping through its own retry assertions.
sleep_ms:{[ms] if[ms>0; system"sleep ",string ms%1000]; ms}

/ Run a niladic function under the retry policy.
/ .
/ Returns a dict rather than throwing, because the CALLER decides what a
/ terminal failure means: under M-05 a worker's failed window is terminal and
/ the worker moves on, which is a decision about the run, not about this
/ function.
/ @param pol a policy dict as returned by `policy`
/ @param f a niladic function performing the attempt
/ @return dict of state (`ok or `failed), kind (`none/`transport/`data),
/   attempts, result (on success) and error (on failure)
/ @eg .qwrt.with_retry[.qwrt.default_policy;{42}]`result  ->  42
with_retry:{[pol;f]
    / `cap`, not `max` - max is a q builtin, and shadowing it inside the
    / lambda breaks the & fallback below in a way that reports as `nyi at
    / call time. Third time this trap has bitten this repository.
    cap:pol`max_attempts;
    attempt:1;
    outcome:(`err;"with_retry: attempt function never ran");
    while[attempt<=cap;
        outcome:@[{(`ok;x[])};f;{(`err;x)}];
        if[`ok~first outcome;
            :`state`kind`attempts`result`error!(`ok;`none;attempt;last outcome;"")];
        kind:classify last outcome;
        if[kind=`data;
            :`state`kind`attempts`result`error!(`failed;`data;attempt;::;last outcome)];
        if[attempt=cap;
            :`state`kind`attempts`result`error!(`failed;`transport;attempt;::;last outcome)];
        sleep_ms backoff_ms[pol;attempt];
        attempt+:1];
    `state`kind`attempts`result`error!(`failed;`transport;cap;::;last outcome)}

/ ------------------------------------------------------------- DRY RUN

/ The three side effects ETL-14 suppresses, named once so they cannot drift
/ apart. Suppressing a subset is the dangerous case: rows withheld while
/ coverage is still published leaves the ledger asserting a window is
/ complete when nothing was written.
suppressed_in_dry_run:`publish_rows`publish_coverage`write_checkpoint

/ Is this run diagnostic-only?
/ .
/ Opt-in via .qwcfg.get_flag, which defaults absent-to-false - so a
/ misconfigured worker does real work rather than silently doing none.
is_dry_run:{[] @[{.qwcfg.get_flag `dry_run};::;{0b}]}

/ Perform one named side effect, or record that dry-run withheld it.
/ .
/ Every ETL-14-suppressed effect goes through here rather than being guarded
/ inline at its call site, so "what does dry-run skip" has one answer that a
/ test can enumerate.
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
        '"commit: ",string[effect]," is not one of the effects ETL-14 suppresses (",
         ", " sv string suppressed_in_dry_run,") - add it there deliberately rather than bypassing the gate"];
    / `f . ()` is a TYPE error rather than a niladic call; the unary-null
    / argument list is what actually applies a niladic function.
    applied:$[0=count args; enlist(::); args];
    $[dry; (`skipped;effect); (`done;effect;action . applied)]}

/ ----------------------------------------------------------- WINDOWS

/ Split [from_ts;to_ts) into consecutive half-open windows of `width`.
/ .
/ ETL-18 names "window boundaries" as one of six lifecycle decision points
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
/ WHAT A "DAY" IS HERE (issue #80's L-04, and L-05 with it)
/ .
/ A 1D window is 24h of ELAPSED UTC TIME measured from from_ts. It is not a
/ calendar day, not a business date, and not aligned to any venue's session:
/ a range starting at 17:00 produces windows starting at 17:00, and a
/ five-day range over a weekend is five windows, not five trading days.
/ .
/ WHAT DEFINES A TRADING DAY FOR THE CANONICAL WORKERS IS NOT KNOWABLE FROM
/ THIS TREE - that answer lived with the bank's calendars, which A-04 keeps
/ out of a public repository. So the assumption is written down instead of
/ guessed at, and tests/q/test_time_zone.q pins it: whoever adds a venue
/ calendar has to change a failing test rather than a comment. Nothing else
/ here has a business-date notion either - .qcov composes half-open
/ intervals and .qdcf counts actual calendar days - and the only day roll
/ this tree knows about is TorQ's EOD reload, which is an operational state
/ rather than a business date.
/ .
/ Cutting in UTC is also what makes DST harmless (L-05): a daily window is
/ exactly 24h across a transition, never the 23h or 25h a local calendar day
/ becomes, so coverage keeps tiling exactly. The variable local span is
/ handled where it belongs, in .qsrc's per-source zone conversion.
/ @param from_ts range start
/ @param to_ts range end, exclusive
/ @param width a timespan, e.g. 1D
/ @return a table of range_from/range_to
/ @eg .qwrt.windows[2026.09.01D00:00;2026.09.04D00:00;1D]  -> 3 daily windows
windows:{[from_ts;to_ts;width]
    .qcov.require_interval[from_ts;to_ts];
    if[not width>0D00:00;
        '"windows: width must be positive, got ",string width];
    n:"j"$ceiling (to_ts-from_ts)%width;
    starts:from_ts+width*til n;
    ([] range_from:starts; range_to:to_ts&starts+width)}

/ ----------------------------------------------------- COVERAGE SKIPPING

/ Should this window be fetched, or is it already published (ETL-13)?
/ .
/ Version-specific by construction: is_covered takes source_version as a
/ required parameter, so a window covered at v1 does NOT suppress a fetch at
/ v2. That is ETL-10, and it is the direction that matters - the alternative
/ skips a re-extraction the version bump exists to force.
/ @return 1b when the window still needs fetching
needs_fetch:{[ds;version;from_ts;to_ts]
    not .qcov.is_covered[ds;version;from_ts;to_ts]}

/ Narrow a requested range to the parts not yet published (ETL-13).
/ .
/ Returns the gaps rather than a yes/no, so a retry after a partial run
/ re-fetches only what is missing instead of the whole range. An empty result
/ means there is nothing to do - which under C-07 is an `idle success, not a
/ failure.
remaining:{[ds;version;from_ts;to_ts]
    .qcov.missing[ds;version;from_ts;to_ts]}

/ Complete one window: publish rows, record coverage, save the checkpoint -
/ in that order, and all three behind the dry-run gate.
/ .
/ The ORDER is the requirement, not an implementation detail. Coverage is
/ staged only after the publication it describes has happened (ETL-07), and the
/ checkpoint only after coverage, so every possible interruption point leaves
/ an under-claim rather than an over-claim: a re-run redoes work, which
/ retry-safe publication tolerates, instead of skipping work the ledger
/ wrongly believes is done.
/ @param worker the worker's name
/ @param ds the dataset
/ @param spec the run specification (source_version, range_from, range_to)
/ @param from_ts window start
/ @param to_ts window end, exclusive
/ @param publish a niladic function publishing the window, returning a row
/   count
/ @return dict of the three effects' outcomes plus the row count
finish_window:{[worker;ds;spec;from_ts;to_ts;publish]
    dry:is_dry_run[];
    published:commit[dry;`publish_rows;publish;()];
    rows:$[`done~first published; last published; 0];
    covered:commit[dry;`publish_coverage;.qcov.stage_completion;
        (ds;spec`source_version;from_ts;to_ts;rows)];
    checkpointed:commit[dry;`write_checkpoint;.qbfstate.save_checkpoint;
        (worker;spec;to_ts)];
    `dry_run`rows_published`published`covered`checkpointed!
        (dry;rows;published;covered;checkpointed)}

/ ---------------------------------------------------------- DEPENDENCIES

/ worker -> the TorQ process types it needs a connection to (ETL-16).
declared_dependencies:(`symbol$())!();

/ Declare what a worker needs before it can run.
/ @eg .qwrt.declare_dependencies[`markout_backfill;`tickerplant`hdb]
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
connected_override:();

/ The proctypes currently reachable in the fleet.
/ .
/ Empty when TorQ is absent, which makes require_dependencies report every
/ declared dependency as missing and refuse to start - the direction a total
/ function has to pick, so that a broken probe stops a worker at init rather
/ than letting it run blind.
/ @return a symbol vector of proctypes, empty outside a TorQ process
/ @eg .qwrt.connected[]
connected:{[]
    if[count connected_override; :connected_override];
    @[{exec distinct proctype from .servers.SERVERS where not null w};::;{`symbol$()}]}

/ Fail at INITIALISATION when a declared dependency is unavailable (ETL-16).
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
    if[count missing;
        '"require_dependencies: ",string[worker]," cannot start - no connection to ",
         ", " sv string missing,
         " (declared dependencies must resolve through .servers at init, ETL-16)"];
    worker}

/ Fail at initialisation when an upstream dataset is not published for the
/ requested range (ETL-16's "coverage precondition").
/ .
/ Distinct from needs_fetch above, which asks about THIS worker's own output.
/ This asks about its INPUT: a markout backfill over a range whose trades are
/ not yet published would compute markouts against missing trades and record
/ coverage saying it had done so.
/ @throws error naming the missing upstream ranges
require_upstream:{[upstream;version;from_ts;to_ts]
    .qcov.require_covered[upstream;version;from_ts;to_ts]}

\d .
