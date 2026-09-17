/ torq_pipeline.q - the shared .qpipe block library every scripts/torq_*.q
/ feed and ETL process builds on. Created to close the gap those scripts'
/ own header comments kept documenting: "no shared-constant infra exists
/ across scripts/torq_*.q, each defines its own" (torq_markout_etl.q) - so
/ `fx_pairs` was duplicated in four files, the ~25-line subscribe/init/
/ .servers.startup closing block was duplicated in four more, and each of
/ the seven hard-won TorQ invariants below survived only as a prose comment
/ that the next author had to remember to copy.
/ .
/ This file holds the SOURCE/STATE/TRIGGER/SINK blocks as plain helper
/ functions. It deliberately does NOT generate the `upd` handler or run a
/ generic dispatcher: symbol resolution in this environment is fragile in
/ non-obvious ways (see invariant 5 below, found the hard way), so each
/ pipeline still writes its own readable, root-level `upd` and its own
/ compute function. The blocks own the plumbing, not the logic.
/ .
/ The seven invariants, each of which cost a live debugging session
/ somewhere in scripts/torq_*.q before it was written down:
/ .
/   1. .u.upd stamps its own `time` on receipt (see
/      lib/torq/code/processes/segmentedtickerplant.q's .stplg.updtab). A
/      publisher that sends its own `time` makes every message one column
/      too wide - a confirmed-live 'length error, not a silent mismatch.
/      -> .qpipe.publish drops `time` if present.
/   2. Keyed tables (type 99h) are rejected by the tickerplant's upd/.u.upd
/      machinery (Rule S7) - which is why .posbook.book can never be
/      published directly, only a flat snapshot of it.
/      -> .qpipe.publish unkeys a keyed table rather than letting it through.
/   3. .u.upd derives its row count from column length, so every column must
/      be a vector - never a bare atom, even for a single row (the vendored
/      feed.q always builds n-length vectors, n>=1).
/      -> .qpipe.publish accepts a dict of atoms and enlists it into a
/         1-row table.
/   4. A timer function that throws gets silently deactivated (Rule T3), so
/      every timer target must be trapped - but the textbook niladic trap
/      `.[f;();errfn]` does not work for a niladic {[] ...} function: its
/      handler fires even when f SUCCEEDS, discarding the real result.
/      Measured on KDB-X:
/        .[{[] 1+1};();{`caught}]  ->  `caught     (wrong - nothing threw)
/        @[{[] 1+1};::;{`caught}]  ->  2           (right)
/        @[{[] '"boom"};::;{`caught}] -> `caught    (right)
/      So the `.` form would log a spurious error on every single tick.
/      `@[f;::;errfn]` - @ with a generic-null placeholder arg - is the form
/      that actually works, and it behaves identically on both interpreters.
/      -> .qpipe.safe_timer wraps the function in the working idiom.
/   5. A function defined inside a non-root namespace does not reliably
/      resolve a root global (like the publish handle `h`) by its bare name
/      on this build. Every pipeline therefore defines `upd` and its compute
/      function at ROOT and fully-qualifies its own .<name>.* state refs.
/      -> .qpipe.publish takes the handle as an explicit argument instead of
/         reaching for a global, so it works from either context.
/   6. src/init.q's own \l lines are repo-root-relative and torq.sh does not
/      launch a process from the repo root.
/      -> .qpipe.load_uqf does the cd-there-and-back, and restores the cwd
/         even when the load throws (the hand-rolled copies in
/         torq_cross_etl.q/torq_vectorize_etl.q/torq_posbook_etl.q/
/         torq_markout_etl.q leave the process in the wrong directory if
/         init.q ever fails).
/   7. A real .sub.subscribe subscriber needs .servers.startup[]'s
/      access-listed handle to the tickerplant, which means borrowing an
/      already-credentialed proctype ("metrics") in process.csv.
/      -> .qpipe.subscribe_etl does the whole startup/depcycles/subscribe
/         dance and hands back the publish handle.
/ .
/ Loaded via each pipeline row's own `load` column in the process.csv
/ torq_orchestrator.core.bootstrap() generates - not by src/init.q, and not
/ part of the uqf library proper (this is TorQ plumbing, outside the eFX
/ pricing/risk/execution/microstructure scope src/*.q keeps to).

\d .qpipe

/ NOTE on parameter names: `desc` and `tables` are q builtins, and using
/ either as a lambda PARAMETER name throws a bare 'nyi when the function is
/ called - not at definition time, and regardless of whether the body ever
/ references the parameter (confirmed live on KDB-X while writing this file:
/ {[a;b;c;desc] 1+1}[1;2;3;4] fails, {[a;b;c;s] 1+1}[1;2;3;4] does not).
/ That is why safe_timer takes `timer_desc` and subscribe_etl takes
/ `sub_tables`. Both bugs were latent - they would have surfaced only on the
/ first live call inside TorQ.

/ The four pairs every FX feed/ETL in this demo actually produces - was a
/ duplicated literal in torq_fx_feed.q, torq_fx_trades_feed.q,
/ torq_markout_etl.q and torq_posbook_etl.q.
fx_pairs:`EURUSD`GBPUSD`USDJPY`AUDUSD

/ The tickerplant proctype every pipeline here talks to.
tp_type:`segmentedtickerplant

/ startupdepcycles tuning, previously re-declared as tpconsleep/
/ tpcheckcycles in each ETL.
con_sleep:10
check_cycles:0W

/ ---------------------------------------------------------------- SOURCE

/ Load uqf's own src/init.q (invariant 6). Restores the cwd even if the
/ load throws, unlike the hand-rolled copies this replaces.
/ @throws error if UQFROOT is unset, or if src/init.q fails to load
load_uqf:{[]
    root:getenv`UQFROOT;
    if[0=count root; '"qpipe.load_uqf: UQFROOT is not set"];
    cwd:first system"pwd";
    system"cd ",root;
    outcome:@[{system"l src/init.q"; `ok};::;{x}];
    system"cd ",cwd;
    if[not outcome~`ok; '"qpipe.load_uqf: could not load src/init.q: ",outcome];
    }

/ Bring this process up as a tickerplant subscriber and hand back a publish
/ handle (invariant 7). Replaces the ~25-line tickerplanttypes/requiredprocs/
/ subscribe/init/.servers.CONNECTIONS/.servers.startup/gethandlebytype block
/ each ETL previously carried its own copy of.
/ @param nm the pipeline's name, for log lines
/ @param sub_tables the table(s) to subscribe to, e.g. `trades`quote
/ @return the publish handle to the tickerplant (assign it to root-level `h`)
/ @throws error if no tickerplant can be found to subscribe to
subscribe_etl:{[nm;sub_tables]
    .servers.CONNECTIONS:tp_type;
    .servers.startup[];
    .servers.startupdepcycles[tp_type;con_sleep;check_cycles];
    handles:.sub.getsubscriptionhandles[tp_type;();()!()];
    if[0=count handles; '"qpipe.subscribe_etl: no ",(string tp_type)," found to subscribe to"];
    subproc:first handles;
    .lg.o[`qpipe;"subscribing ",(string nm)," to ",string subproc`procname];
    .sub.subscribe[sub_tables;`;0b;0b;subproc];
    / safe to acquire now - startupdepcycles above already blocked until the
    / tickerplant was confirmed up. Separate, unauthenticated handle from the
    / .servers.startup[] subscription handle, same as every ETL did by hand.
    .servers.gethandlebytype[tp_type;`any]}

/ Publish handle only, for a feed process that produces rows but subscribes
/ to nothing (torq_fx_feed.q / torq_quotes_feed.q / torq_wide_book_feed.q /
/ torq_fx_trades_feed.q all open with exactly these two lines).
/ @return the publish handle to the tickerplant
feed_handle:{[]
    .servers.startupdepcycles[tp_type;con_sleep;check_cycles];
    .servers.gethandlebytype[tp_type;`any]}

/ ----------------------------------------------------------------- STATE

/ Take every row matching mask out of a buffer table and return it, leaving
/ the rest behind - the queue block's drain step (markout1's pending_trades
/ pattern). mask is evaluated ONCE by the caller and used for both the read
/ and the delete, so a row arriving between the two can't be dropped
/ unscored; hand-written drain code that recomputes its cutoff in the delete
/ clause has exactly that race.
/ @param tblname the buffer table's fully-qualified name, e.g. `.markout.pending_trades
/ @param mask a boolean vector over that table, as long as it is
/ @return the drained rows, in their original order
/ @eg .qpipe.drain[`.markout.pending_trades; .markout.pending_trades[`time]<=cutoff]
/ @see .qpipe.evict - use that instead when a failed publish should retry
/   the batch rather than lose it (drain is at-most-once, evict at-least-once)
drain:{[tblname;mask]
    buffer:get tblname;
    if[0=count buffer; :buffer];
    ready:buffer where mask;
    tblname set buffer where not mask;
    ready}

/ Remove every row matching mask from a buffer table, keeping the rest, and
/ return how many went - the queue block's eviction step for a
/ read-then-confirm flow: compute the mask once, read the rows, publish
/ them, and only then evict. Use this rather than `drain` whenever losing a
/ batch matters, because `safe_timer` swallows a publish failure: with
/ `drain` the rows are already gone and the batch is lost (at-most-once),
/ with `evict` they are still buffered and the next tick retries them
/ (at-least-once). Pass the SAME mask value to the read and to evict - a
/ recomputed cutoff between the two is the race both helpers exist to
/ prevent.
/ @param tblname the buffer table's fully-qualified name
/ @param mask the boolean vector already used to read the batch
/ @return the number of rows removed
/ The pattern: compute the mask once, read the batch with it, publish the
/ batch, and only then evict with that same mask.
/ @eg mask:.markout.pending_trades[`time]<=cutoff;
/   ready:.markout.pending_trades where mask;
/   .qpipe.evict[`.markout.pending_trades;mask]
evict:{[tblname;mask]
    buffer:get tblname;
    if[0=count buffer; :0];
    tblname set buffer where not mask;
    sum mask}

/ ------------------------------------------------------------------ SINK

/ Private: coerce whatever a pipeline computed into the flat, unkeyed table
/ .u.upd expects (invariants 2 and 3).
/ @throws error if handed something that isn't a table, keyed table or dict
as_table:{[data]
    t:type data;
    $[98h=t;
        data;
     99h=t;
        $[.Q.qt data;
            / invariant 2: unkey rather than letting a 99h through to a
            / tickerplant that will reject it.
            0!data;
          all 0>type each value data;
            / invariant 3: a dict of atoms is one row - enlist it into a
            / table so every column is a 1-element vector.
            enlist data;
            flip data];
        '"qpipe.publish: expected a table, keyed table or dict, got type ",string t]}

/ Publish rows onto the tickerplant (invariants 1, 2, 3 and 5). The one and
/ only way a pipeline in this demo should send data.
/ @param h the publish handle (passed explicitly, never read from a global -
/   see invariant 5)
/ @param tbl the destination table name, e.g. `execution_quality
/ @param data a table, keyed table, or dict (of atoms for one row, or of
/   vectors for many)
/ @return the number of rows published
/ @eg .qpipe.publish[h;`execution_quality;out]
/ @eg .qpipe.publish[h;`trades;`sym`side`trade_price`size`pip_factor!(`EURUSD;1;1.085;1e6;10000)]
publish:{[h;tbl;data]
    out:as_table data;
    if[0=count out; :0];
    / invariant 1: .u.upd stamps its own `time` - sending ours makes the
    / message one column too wide.
    out:$[`time in cols out; ![out;();0b;enlist `time]; out];
    h (`.u.upd;tbl;value flip out);
    count out}

/ ---------------------------------------------------------------- STATUS

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
/ @eg .qpipe.require_transition[`completed;`running]  -> throws
require_transition:{[from;to]
    if[null from; :1b];
    if[not from in key legal_transitions;
        '"require_transition: unknown previous state ",string from];
    allowed:legal_transitions from;
    if[not to in allowed;
        '"require_transition: ",string[from]," -> ",string[to]," is forbidden",
         $[from in `idle`completed`failed;
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
/ @eg .qpipe.write_status[`markout_backfill;`markout1;`completed;
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

/ --------------------------------------------------------------- TRIGGER

/ Register a repeating timer that runs fn and can never throw (invariant 4).
/ Creates a niladic wrapper .qpipe.tick_<nm> around fn and registers THAT
/ with .timer.repeat.
/ .
/ The wrapper is built by string-eval rather than as a projection because
/ .timer.repeat calls its target as (`name;`) and every working timer
/ function in this demo is a genuine niladic {[] ...} - a projection with
/ remaining parameters is not the same rank and does not stand in for one.
/ The generated wrapper is a real, inspectable function: call
/ .qpipe.tick_markout[] by hand to test it.
/ @param nm the pipeline's name - also names the wrapper and tags log lines
/ @param interval a timespan, e.g. 0D00:00:01.000
/ @param fn the fully-qualified name of the niladic function to run
/ @param timer_desc the description .timer.repeat shows
/ @return the generated wrapper's name
/ @eg .qpipe.safe_timer[`markout;0D00:00:01.000;`process_ready;"Score markouts"]
safe_timer:{[nm;interval;fn;timer_desc]
    wrapper:`$".qpipe.tick_",string nm;
    / `value` the lambda EXPRESSION only, then `set` the name - not
    / `value "name:{...}"`. Evaluating an assignment statement through
    / `value` from inside a lambda throws 'nyi on this build (confirmed
    / live while writing this file); parsing a bare lambda and assigning it
    / with `set` is well-defined and does the same job.
    body:"{[] @[get `",(string fn),";::;{[e] .lg.e[`",(string nm),";\"timer function ",(string fn)," failed: \",e]}]}";
    wrapper set value body;
    .timer.repeat[.proc.cp[];0Wp;interval;(wrapper;`);timer_desc];
    wrapper}

\d .
