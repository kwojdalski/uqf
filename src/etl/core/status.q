/ status.q - the process-status file a worker writes and an orchestrator
/ reads (.qstatus).
/ .
/ One file per worker instance, airflow_status_<instance_id>.txt in
/ status_dir, one JSON object per file: the state, the run specification,
/ the progress counters and the error. Written by write_status, guarded by
/ the transition table below so an illegal state change is refused rather
/ than recorded.
/ .
/ THIS FORMAT IS A CROSS-REPOSITORY CONTRACT. Two other programs read these
/ files without going through q:
/ .
/   python/uqf_airflow_provider   status_reader.py, and the sensor built on
/                                 it - what Airflow polls to decide whether a
/                                 backfill task succeeded, idled or failed
/   python/uqf_frontend           status.py - the Control view's worker list
/ .
/ Both carry the filename shape and the state set as literals, deliberately,
/ so they need no q to run. Renaming a key, adding a state or moving the
/ file is therefore a change to three trees, not one. The status directory
/ is also where bounded workers keep their locks and checkpoints and
/ continuous workers their cursors (.qbfstate.lock_dir, .qcont.cursor_path),
/ so status_dir is the one spelling of "this deployment's runtime state".
/ .
/ WHY THIS IS ITS OWN FILE (#229). These functions lived in
/ scripts/torq_pipeline.q, the TorQ adapter, and they have nothing to do
/ with TorQ - env vars, .j.j, mkdir and mv. Worse, backfill_state.q in src/
/ called them, which put src/ downstream of scripts/ and is why lock_dir was
/ wrapped in a try-with-fallback: the author knew .qpipe might not be
/ loaded. Here, under src/etl/core/, the dependency points the right way and
/ the guard is gone. Same convention as coverage.q, run.q and heartbeat.q:
/ one file per persisted artefact that something outside this process reads.

\d .qstatus

/ The lifecycle states a worker may report. Three of them are terminal, and
/ the distinction between the first two is the one C-07 asks for - "nothing
/ to do" must not look like "failed", or an orchestrator retries a
/ successful no-op forever.
/   starting  - initialising, not yet acquired work
/   running   - processing a window
/   idle      - ran, found no work, nothing wrong (terminal for this run)
/   completed - ran, did work, finished the window (terminal)
/   failed    - error set, see the error field (terminal)
status_states:`starting`running`idle`completed`failed

/ Which states may legally follow which (question-bank G-02).
/ .
/ G-02 asks for "the legal status values AND which transitions are
/ forbidden". The values were already here and validated; the transitions
/ were not, so every illegal one wrote cleanly. The dangerous case is
/ specific and silent:
/ .
/   completed -> running, or failed -> completed
/ .
/ Either RESURRECTS a terminal run. A reader polling this file sees the new
/ state and nothing else - the previous one is gone, because the file is
/ overwritten per instance - so a failure that was correctly recorded
/ vanishes and the run reports as healthy. Nothing errors; the history is
/ simply no longer there to contradict it.
/ .
/ Two transitions out of a terminal state are legal, and the second was
/ missing from the first version of this rule - found by running it against
/ the real worker path rather than only in isolation:
/ .
/   -> starting  a worker beginning a genuinely new run.
/   -> failed    a failure, which must ALWAYS be recordable.
/ .
/ That second one matters more than it looks. .qbfstate.fail is the shell's
/ error path, so refusing `failed -> failed` made a second consecutive
/ failure THROW INSIDE THE ERROR HANDLER - masking the original error with a
/ complaint about state transitions. A rule that exists to stop a failure
/ being hidden must not itself hide one.
/ .
/ It is also safe by the rule's own logic: replacing one terminal state with
/ `failed` is strictly LESS optimistic, so it cannot manufacture the false
/ health the rule guards against. What is forbidden is claiming progress
/ (`running`) or success (`completed`/`idle`) after finishing.
/ .
/ So the rule reads: you may always begin again, and you may always report a
/ failure; you may never claim progress or success without declaring a new
/ start.
/ .
/ running -> running is legal and normal: it is the next window.
legal_transitions:(!). flip (
    (`starting;  `running`idle`completed`failed);
    (`running;   `running`idle`completed`failed);
    / terminal states: begin again, or report a failure
    (`idle;      `starting`failed);
    (`completed; `starting`failed);
    (`failed;    `starting`failed))

/ Validate a transition, or throw naming what it would have hidden.
/ .
/ `from` of `` ` `` (no previous status) permits any state: a first write has
/ nothing to contradict, and a worker whose init failed before it could write
/ `starting` should still be able to record `failed`. Being strict at entry
/ would trade a real diagnostic for a rule with nothing to protect.
/ @throws error when the transition is forbidden
/ @eg .qstatus.require_transition[`completed;`running]  -> throws
/ `from_state`, not `from`: `from` is a qSQL keyword, and a lambda that takes
/ it cannot run `select ... from ...` in its own body - `{[from] select x from
/ t where x>from}` throws a bare 'type. Nothing here runs qSQL today, so this
/ was latent rather than broken; the linter's QF001 found it.
require_transition:{[from_state;to]
    if[null from_state; :1b];
    if[not from_state in key legal_transitions;
        '"require_transition: unknown previous state ",string from_state];
    allowed:legal_transitions from_state;
    if[not to in allowed;
        '"require_transition: ",string[from_state]," -> ",string[to]," is forbidden",
         $[from_state in `idle`completed`failed;
            / Consequence FIRST: q truncates a thrown string at 255 bytes,
            / so anything after that is silently lost - and what gets lost
            / is the part that explains the failure. This message was 254
            / bytes and "read as healthy" was cut off mid-phrase.
            " - a recorded failure would silently read as healthy. Write `starting for a new run, or `failed to report a failure";
            " - legal next states are ",", " sv string allowed]];
    1b}

/ The state recorded in an instance's existing status file, or ` if there is
/ none. Exists so write_status can validate a transition without the caller
/ having to remember what it last wrote - which it would get wrong precisely
/ when it matters, after a restart.
previous_state:{[instance_id]
    path:(status_dir[]),"/airflow_status_",string[instance_id],".txt";
    raw:@[{first read0 hsym `$x};path;{""}];
    if[0=count raw; :`];
    saved:@[{.j.k x};raw;{()!()}];
    $[`state in key saved; `$saved`state; `]}

/ Where status files go. Overridden by UQFSTATUSDIR so the demo and a real
/ deployment can differ without a code change; defaults under TORQDATA
/ alongside the other generated state.
status_dir:{[]
    d:getenv`UQFSTATUSDIR;
    $[0<count d; d; (getenv[`TORQDATA]),"/status"]}

/ Publish one worker's status as a JSON object, for a reader outside q.
/ .
/ This is the q side of the frontend's backfill view. The format is defined
/ HERE rather than inferred, because this tree has no Airflow provider to be
/ compatible with - see the FE-04 decision to develop the pipeline layer in
/ this repository.
/ .
/ What belongs in this file is exactly what ETL-15 says q owns: process
/ startup, source reads, query failures, checkpoints, run and window counts,
/ and coverage events. It deliberately carries NO retry count, task ordering,
/ timeout or concurrency state - those are Airflow's facts, and a reader that
/ wants them must ask Airflow. Mixing the two is what ETL-15 forbids.
/ .
/ Written atomically: serialise, write to a temp path, then rename over the
/ target. A reader polling the directory (the frontend polls, per FE-10) would
/ otherwise be able to read a half-written file and see a truncated JSON
/ object as a parse error.
/ @param worker the worker's name, e.g. `markout_backfill
/ @param instance_id the process instance, e.g. `markout1 - names the file,
/   so two instances of one worker do not overwrite each other
/ @param state one of status_states
/ @param spec dict with `source_version`range_from`range_to - the run
/   specification. range is half-open [range_from;range_to) per ETL-08, and
/   source_version is mandatory per ETL-09 (coverage under one source release
/   says nothing about another)
/ @param progress dict with `cursor`rows_published`windows_completed
/ @param err an error string, or "" when there is none
/ @return the path written
/ @throws error if state is unknown, if the range is empty or reversed, if
/   source_version is missing, or if a failed state carries no error
/ @eg .qstatus.write_status[`markout_backfill;`markout1;`completed;
/       `source_version`range_from`range_to!(`v1;2026.09.13D00:00;2026.09.14D00:00);
/       `cursor`rows_published`windows_completed!(2026.09.14D00:00;1234;1);
/       ""]
write_status:{[worker;instance_id;state;spec;progress;err]
    if[not state in status_states;
        '"write_status: unknown state ",string[state]," - expected one of ",", " sv string status_states];
    req:`source_version`range_from`range_to;
    missing:req where not req in key spec;
    if[count missing; '"write_status: spec is missing ",", " sv string missing];
    if[null spec`source_version; '"write_status: source_version must be set (ETL-09)"];
    / ETL-08: half-open and forward-going. Rejecting here means a bad range
    / can never reach the file, rather than being caught by the reader.
    if[not spec[`range_to]>spec`range_from;
        '"write_status: range must be non-empty and forward-going, got [",
         string[spec`range_from],"; ",string[spec`range_to],")"];
    if[(state=`failed) and 0=count err;
        '"write_status: a failed state must carry an error string"];
    / G-02: refuse a transition that would resurrect a terminal run. Checked
    / HERE rather than left to the caller, because the caller gets it wrong
    / exactly when it matters - after a restart, when it no longer remembers
    / what it last wrote.
    require_transition[previous_state instance_id;state];
    dir:status_dir[];
    / mkdir -p is idempotent, and cheaper than checking first.
    system"mkdir -p ",dir;
    payload:`worker`instance_id`state`source_version`range_from`range_to,
            `cursor`rows_published`windows_completed`error`updated_at;
    values_:(worker;instance_id;state;spec`source_version;
             spec`range_from;spec`range_to;
             progress`cursor;progress`rows_published;progress`windows_completed;
             err;.z.p);
    target:dir,"/airflow_status_",string[instance_id],".txt";
    tmp:target,".tmp";
    (hsym `$tmp) 0: enlist .j.j payload!values_;
    / atomic on the same filesystem, so a polling reader sees the old file or
    / the new one, never a partial write
    system"mv ",tmp," ",target;
    target}

\d .
