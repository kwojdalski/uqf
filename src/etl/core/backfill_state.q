/ backfill_state.q - the bounded worker lifecycle contract (.qbfstate).
/ .
/ Implements requirements ETL-01 to ETL-05 of docs/reference/etl-framework-requirements.md.
/ Note those IDs belong to the REQUIREMENTS document; the question bank uses
/ an overlapping E-nn scheme for a different set of questions about source
/ adapters. Where a question-bank answer is cited below it is named as such.
/ .
/ The distinction this file exists to enforce (ETL-02):
/   a BOUNDED worker takes an explicit [range_from;range_to) request, a
/   resumable run specification, a private state file, and has a terminal
/   completion path. That is an enforced contract, checked at init.
/   a CONTINUOUS worker is a long-running poll-and-cursor loop. That is an
/   established pattern with NO registry and no contract (ETL-03), and the
/   asymmetry is deliberate - see docs issue ETL-22/ETL-24.
/ .
/ "A bounded worker must not silently become an unbounded tailer" is the
/ sentence the whole contract exists to make true.

\d .qbfstate

/ ---------------------------------------------------------------- CONTRACT

/ Every bounded worker must define these functions in its own namespace.
/ Chosen to match the lifecycle the framework actually drives: acquire a
/ request, turn a cursor into windows, fetch one window, publish it, then
/ record the cursor that acknowledges it.
bounded_worker_methods:`init`plan`fetch`publish`checkpoint

/ ...and these globals. range_from/range_to make the bound explicit and
/ inspectable rather than buried in a call; source_version is mandatory
/ because coverage recorded under one source release says nothing about
/ another (ETL-09), so a run that cannot name its release cannot record
/ coverage either.
bounded_worker_globals:`source_version`range_from`range_to

/ worker name -> the namespace symbol holding its implementation.
bounded_workers:(`symbol$())!`symbol$()

/ Private: the names defined in a namespace, or an empty list if the
/ namespace does not exist. Wrapped because `key` on an absent namespace
/ throws rather than returning empty, and a missing namespace is a contract
/ violation we want reported by name rather than as a bare error.
ns_names:{[ns] @[{key x};ns;`symbol$()]}

/ Register a bounded worker. Does NOT validate - require_contract does that,
/ at init, when the namespace is actually populated. Registering early and
/ checking late is deliberate: a worker's methods are defined as its file
/ loads, so validating at registration would force declaration order.
/ @param worker the worker's name, e.g. `markout_backfill
/ @param ns the namespace symbol holding its implementation, e.g.
/   `.qwrk.markout_backfill - .qbw.define derives it, so only a worker
/   built without the shell passes one by hand
/ @return the worker name
/ @eg .qbfstate.register[`demo_deals_backfill;`.qwrk.demo_deals_backfill]
register:{[worker;ns]
    bounded_workers[worker]:ns;
    worker}

/ Every registered bounded worker, for introspection and for tests.
registered:{[] key bounded_workers}

/ Check that a registered worker implements the whole contract, and throw
/ naming everything missing if it does not (question-bank C-01).
/ .
/ Called during the worker's own initialisation, so an incomplete worker
/ fails at startup rather than part-way through a backfill against a live
/ source. It reports ALL missing names at once rather than the first,
/ because fixing them one error at a time is the slow way to find out a
/ worker was never wired up.
/ .
/ This is what makes "is this worker complete" a deterministic test rather
/ than a code-review question, which is what ETL-18 asks for.
/ @param worker a name previously passed to register
/ @return the worker name, so it can be used inline in an init chain
/ @throws error if the worker is unregistered, its namespace is absent, or
/   any contract method or global is missing
/ @eg .qbfstate.require_contract[`demo_deals_backfill]
require_contract:{[worker]
    if[not worker in key bounded_workers;
        '"require_contract: ",string[worker]," is not registered - call .qbfstate.register first"];
    ns:bounded_workers worker;
    names:ns_names ns;
    if[0=count names;
        '"require_contract: ",string[worker],"'s namespace ",string[ns]," is empty or absent"];
    missing_methods:bounded_worker_methods where not bounded_worker_methods in names;
    missing_globals:bounded_worker_globals where not bounded_worker_globals in names;
    if[count missing_methods,missing_globals;
        '"require_contract: ",string[worker]," is incomplete",
         $[count missing_methods; " - missing method(s): ",", " sv string missing_methods; ""],
         $[count missing_globals; " - missing global(s): ",", " sv string missing_globals; ""]];
    worker}

/ -------------------------------------------------------------------- LOCK

/ Where lock files live. Shares the status directory, since .qstatus.status_dir
/ already establishes one per deployment and a lock is the same kind of
/ per-deployment runtime state. A plain call: status.q is loaded by this
/ tree's own init.q, so there is nothing to guard against - the try-with-
/ fallback this used to carry existed because status_dir lived in scripts/
/ and might not have been loaded (#229).
lock_dir:{[] .qstatus.status_dir[]}

lock_path:{[worker] (lock_dir[]),"/",string[worker],".lock"}

/ Take an exclusive single-instance lock, or refuse to start
/ (question-bank C-09).
/ .
/ One instance per worker is what lets ETL-06's checkpoint stay PRIVATE to that
/ worker: no claim column, no shared cursor state, no claim-expiry logic for
/ dead instances. Double-processing becomes impossible rather than unlikely,
/ and the cost - no horizontal scaling of a single worker - costs nothing on
/ a single-host deployment.
/ .
/ Uses mkdir as the atomic primitive rather than a file existence check.
/ `if[not exists; create]` is a race: two workers starting together can both
/ see no lock and both proceed. mkdir either succeeds or fails atomically on
/ every POSIX filesystem, so exactly one wins.
/ @param worker the worker's name
/ @return the lock path
/ @throws error if another instance already holds the lock
/ @eg .qbfstate.acquire_lock[`markout_backfill]
acquire_lock:{[worker]
    dir:lock_dir[];
    system"mkdir -p ",dir;
    path:lock_path worker;
    / mkdir on an existing directory returns non-zero: that IS the test.
    rc:@[{system"mkdir ",x," 2>/dev/null"; 0};path;{[e] 1}];
    if[rc<>0;
        '"acquire_lock: ",string[worker]," is already running (lock held at ",path,
         ") - refusing to start a second instance, because two instances would ",
         "both advance the same private checkpoint"];
    / record who holds it, so a stale lock can be diagnosed rather than just
    / deleted blindly.
    (hsym `$path,"/owner") 0: enlist .j.j `pid`started!(.z.i;.z.p);
    path}

/ Release the lock. Safe to call when not held, so it can sit in a cleanup
/ path that also runs on the failure branch.
release_lock:{[worker]
    path:lock_path worker;
    system"rm -rf ",path;
    path}

/ Is the lock currently held? For diagnostics and tests, not for gating -
/ gating on this would reintroduce the race acquire_lock avoids.
lock_held:{[worker] not () ~ @[{key hsym `$x};lock_path worker;{()}]}

/ ------------------------------------------------------------ LEDGER LOCK

/ How long to wait for another process to finish its write before giving up.
/ .
/ Bounded rather than indefinite: a lock left behind by a process that died
/ mid-write would otherwise wedge every worker on the host forever, and a
/ loud failure after five seconds beats a silent hang.
file_lock_wait:0D00:00:05

/ Where a named ledger's mutex lives.
/ @param name the ledger's name, e.g. `etl_coverage
/ @return the lock directory path
/ @eg .qbfstate.file_lock_path `etl_coverage
file_lock_path:{[name] (lock_dir[]),"/",string[name],".lock"}

/ Run `f . args` holding a named ledger's mutex, releasing it however f ends.
/ .
/ DISTINCT FROM acquire_lock above, which guards one INSTANCE of one worker
/ and deliberately REFUSES when the lock is held - the right answer when a
/ second instance would corrupt a private checkpoint. This is a short
/ critical section that several processes legitimately contend for, so it
/ WAITS instead.
/ .
/ mkdir is the atomic primitive here for the same reason it is there:
/ `if[not exists; create]` is a race two processes can both win, while mkdir
/ either succeeds or fails atomically on every POSIX filesystem.
/ .
/ ARGS ARE A SEPARATE PARAMETER, and that is not stylistic. The obvious
/ spelling - with_file_lock[name] {[x] ...}[value] - defers nothing: a
/ fully-applied projection in q is a CALL, so the body would run BEFORE this
/ function is entered and the lock would protect nothing. The first version
/ of the coverage ledger made exactly that mistake, and it was invisible
/ from outside because the writes still happened and still persisted; only
/ the mutual exclusion was missing.
/ .
/ The result is captured as (ok; value) so a throw inside f still releases
/ the lock before being re-thrown. An error path that skips the release is
/ how one failed write wedges every later one.
/ @param name the ledger to lock, e.g. `etl_coverage
/ @param f the function to run under the lock
/ @param args its arguments, as a list
/ @return whatever f returns
/ @throws error when the lock cannot be taken within file_lock_wait
/ @eg .qbfstate.with_file_lock[`etl_coverage;{[n] n};enlist 1]
with_file_lock:{[name;f;args]
    dir:lock_dir[];
    system"mkdir -p ",dir;
    path:file_lock_path name;
    deadline:.z.p+file_lock_wait;
    while[0<>@[{system"mkdir ",x," 2>/dev/null"; 0};path;{[e] 1}];
        if[.z.p>deadline;
            '"with_file_lock: could not take ",string[name]," at ",path," within ",
             string[file_lock_wait]," - another process may have died mid-write"];
        system"sleep 0.01"];
    / Record the holder, so a lock left by a dead process can be diagnosed
    / rather than deleted blindly. Same courtesy acquire_lock extends.
    (hsym `$path,"/owner") 0: enlist .j.j `pid`started!(.z.i;.z.p);
    r:@[{[fa] (1b; (fa 0) . fa 1)};(f;args);{[e] (0b;e)}];
    system"rm -rf ",path;
    if[not first r; 'last r];
    last r}

/ -------------------------------------------------------- CHECKPOINT

/ Where a worker's private state file lives. PRIVATE is the operative word
/ (ETL-06): one worker's checkpoint is never evidence that a dataset is
/ complete, and no other worker may read it. Cross-process completeness has
/ exactly one channel, the append-only etl_coverage ledger (ETL-07), and
/ conflating the two is how a dataset gets declared complete because some
/ unrelated worker happened to get far enough.
checkpoint_path:{[worker] (lock_dir[]),"/",string[worker],".checkpoint"}

/ Save a cursor together with the FULL run specification that produced it
/ (ETL-06).
/ .
/ The specification is stored, not just the cursor, because a cursor is only
/ meaningful relative to the run that produced it. A cursor from a [Sep 1;
/ Sep 5) run at source_version v1 says nothing about a [Sep 1; Sep 30) run
/ at v2, and resuming from it would skip most of the second run's range
/ while reporting progress.
/ @param worker the worker's name
/ @param spec the run specification - source_version, range_from, range_to
/ @param cursor how far the run has got
/ @return the path written
save_checkpoint:{[worker;spec;cursor]
    dir:lock_dir[];
    system"mkdir -p ",dir;
    path:checkpoint_path worker;
    payload:`source_version`range_from`range_to`cursor`saved_at!
            (spec`source_version;spec`range_from;spec`range_to;cursor;.z.p);
    (hsym `$path) 0: enlist .j.j payload;
    path}

/ Load a cursor, but only if it belongs to THIS run specification (ETL-06).
/ .
/ Returns the saved cursor when the specification matches, and a null
/ timestamp when there is no checkpoint or the specification differs -
/ "discard saved state when the current specification differs", which is the
/ requirement's own wording. Discarding is the safe direction: re-running a
/ window that was already done is idempotent under ETL-13's retry-safe
/ publication, whereas resuming from a foreign cursor silently skips data.
/ @param worker the worker's name
/ @param spec the CURRENT run specification
/ @return the cursor to resume from, or 0Np to start from range_from
load_checkpoint:{[worker;spec]
    path:checkpoint_path worker;
    raw:@[{first read0 hsym `$x};path;{""}];
    if[0=count raw; :0Np];
    saved:@[{.j.k x};raw;{()!()}];
    if[0=count saved; :0Np];
    / Compare PARSED values, not strings. .j.j writes a timestamp as ISO
    / ("2026-09-13T00:00:00.000000000") while `string` on a q timestamp
    / gives "2026.09.13D00:00:00.000000000" - so a string comparison fails
    / even for an identical specification, and every resume silently
    / restarted from the beginning while reporting success.
    matches:all (
        (`$saved`source_version) ~ spec`source_version;
        ("P"$saved`range_from)   ~ spec`range_from;
        ("P"$saved`range_to)     ~ spec`range_to);
    / every element must match, not just the version: a narrowed or widened
    / range is a different run, and resuming across one skips data.
    $[matches; "P"$saved`cursor; 0Np]}

/ Remove a worker's checkpoint, for a deliberate restart from the beginning.
clear_checkpoint:{[worker]
    system"rm -f ",checkpoint_path worker;
    checkpoint_path worker}

/ ------------------------------------------------------------------- SHELL

/ Convert a thrown error into a terminal failed status (question-bank M-01).
/ .
/ The convention is: deterministic code throws with a message prefixed by its
/ own name, and the worker SHELL catches once and converts. That follows
/ ETL-04's existing pure/impure split rather than adding a second axis to
/ remember - the code that throws is exactly the code ETL-04 already calls
/ unit-testable, and the code that converts is exactly the shell.
/ .
/ Note an empty result is NOT an error and must not come through here: "ran,
/ found no work" is an `idle status, a success. Conflating the two is what
/ C-07 warns against, because an orchestrator that cannot tell them apart
/ retries a successful no-op forever.
/ @param worker the worker's name
/ @param spec the run specification, for the status record
/ @param progress what had been done when it failed - partial progress is
/   kept, since a failed window published nothing (ETL-05) but earlier windows
/   in the pass did
/ @param err the caught error string
/ @return the status file path written
fail:{[worker;spec;progress;err]
    .qstatus.write_status[worker;worker;`failed;spec;progress;err]}

/ Run a worker's pass under the shell contract: trap, convert, release.
/ .
/ `@[f;::;handler]` rather than `.[f;();handler]` - the latter fires its
/ handler even when f SUCCEEDS on this build, discarding the real result.
/ That is documented at length in scripts/torq_pipeline.q's safe_timer, and
/ it is the same trap here.
/ @param worker the worker's name
/ @param spec the run specification
/ @param pass a niladic function performing one bounded pass, returning a
/   progress dict
/ @return the progress dict on success; on failure, writes a failed status
/   and rethrows so the caller's own exit path still runs
run_pass:{[worker;spec;pass]
    outcome:@[{(`ok;x[])};pass;{(`err;x)}];
    if[`err~first outcome;
        fail[worker;spec;`cursor`rows_published`windows_completed!(0Np;0;0);last outcome];
        release_lock worker;
        '"run_pass: ",string[worker]," failed: ",last outcome];
    last outcome}

\d .
