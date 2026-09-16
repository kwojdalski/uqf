/ heartbeat.q - a per-worker liveness table monitoring can poll (.qhb).
/ .
/ Answers bank question K-04, decided by the maintainer: yes, a heartbeat
/ table.
/ .
/ WHAT IT ADDS OVER THE STATUS FILES. A status file records what a run is
/ doing, and .qpipe writes one per lifecycle transition. That is enough to see
/ a run that finished or failed, and it is what /ops/backfill and the Airflow
/ sensor read. It is NOT enough to see a run that stopped making progress: a
/ worker wedged inside a window - a source that accepted the connection and
/ never answered, a lock never released - leaves a status file that says
/ `running` and keeps saying it indefinitely. Nothing in that file ages.
/ .
/ A heartbeat ages. beat[] is called per window, so `last_seen` moves while
/ work is happening and stops moving when it is not, and `stale` turns that
/ into an answer instead of a judgement.
/ .
/ THE LIMITATION, STATED RATHER THAN DISCOVERED. This table lives inside the
/ worker's own process, so it cannot report that its process died - the table
/ dies with it. That is not a gap to patch here, it is a division of labour:
/ .
/   is the process alive?      the monitor's query either connects or does not
/   is it making progress?     THIS table's last_seen
/   what did the last run do?  the status file (FE-06)
/   is the data complete?      the coverage ledger (ETL-07)
/ .
/ Four questions, four answers, no overlap. Trying to make any one of them
/ answer another is how a monitor ends up confidently wrong - and a
/ heartbeat's particular temptation is to read a missing row as "dead" when it
/ means "never started", which `stale` keeps separate below.
/ .
/ Staleness is computed at READ time and never stored. A persisted is_stale
/ flag is wrong the instant after it is written, and this repository has
/ already deleted one such flag (shape_is_assumed) that was set by a single
/ call site and read by nothing.

\d .qhb

/ The table's declared shape. Named so require_schema can check it rather
/ than assume it - the mistake .qcov.require_schema exists to prevent.
columns:`worker`state`last_seen`windows

/ Create the root table if it is absent, and return its name.
/ .
/ Keyed on `worker`: monitoring wants each worker's CURRENT state, not a
/ history. History already exists twice over - the status files per
/ transition, and the coverage ledger per publication - and a third
/ append-only log of the same runs would be a third thing to reconcile.
/ .
/ Backtick form (`worker_heartbeat set), not a bare name: inside \d .qhb a
/ bare `worker_heartbeat` resolves to .qhb.worker_heartbeat, NOT the root
/ table. Same trap coverage.q documents at length.
init_table:{[]
    if[not `worker_heartbeat in tables `.;
        `worker_heartbeat set 1!([] worker:`symbol$(); state:`symbol$();
                                    last_seen:`timestamp$(); windows:`long$())];
    `worker_heartbeat}

/ The root table. Exists so no read below names it bare.
ledger:{[] value `worker_heartbeat}

/ Refuse if the table is not the declared shape (ETL-16's posture).
/ @throws error naming the missing or unexpected column(s)
require_schema:{[]
    actual:cols ledger[];
    missing:columns where not columns in actual;
    if[count missing;
        '"require_schema: worker_heartbeat is missing ",", " sv string missing];
    extra:actual where not actual in columns;
    if[count extra;
        '"require_schema: worker_heartbeat has unexpected column(s) ",
         ", " sv string extra];
    1b}

/ Create the table and verify it, returning its name.
/ .
/ Called from a worker's init. Present because .qcov.require_schema spent a
/ while defined, tested, and reached from no live path - a check that cannot
/ fire protects nothing, and its existence reads as protection.
attach:{[]
    existed:`worker_heartbeat in tables `.;
    init_table[];
    if[existed; require_schema[]];
    `worker_heartbeat}

/ ------------------------------------------------------------------ BEAT

/ Record that `worker` is alive and in `state`, stamping the time HERE.
/ .
/ The worker stamps its own beat. A reader stamping arrival time would
/ measure the reader's clock and the network, and would keep advancing for a
/ worker that had stopped - which is the one thing this table exists to
/ notice.
/ @param worker the worker's name
/ @param state a lifecycle state symbol, e.g. `running or `idle
/ @return the worker's name
/ @eg .qhb.beat[`demo_deals_backfill;`running]
/ `seen`, not `prior`: prior is a q BUILTIN (in key `.q), so assigning it as
/ a lambda local throws at LOAD time and aborts the rest of the file - the
/ eighth reserved-name collision in this repository, after desc, tables, sv,
/ load, var, save, get and the `_` parameter. check_q_traps did not have it.
beat:{[worker;state]
    init_table[];
    seen:$[worker in key ledger[]; first (),(ledger[])[(enlist worker)]`windows; 0j];
    `worker_heartbeat upsert (worker;state;.z.p;seen);
    worker}

/ Record a beat AND count a completed window.
/ .
/ Separate from beat so the count means "windows finished", not "beats
/ recorded". Conflating them would make the number grow while a worker sat
/ wedged, which is exactly the signal being destroyed.
/ @eg .qhb.beat_window[`demo_deals_backfill]
beat_window:{[worker]
    init_table[];
    seen:$[worker in key ledger[]; first (),(ledger[])[(enlist worker)]`windows; 0j];
    `worker_heartbeat upsert (worker;`running;.z.p;seen+1);
    worker}

/ --------------------------------------------------------------- READING

/ Every worker's beat, with its age derived AT READ TIME.
report:{[]
    t:0!ledger[];
    if[0=count t; :([] worker:`symbol$(); state:`symbol$();
                       last_seen:`timestamp$(); windows:`long$(); age:`timespan$())];
    update age:.z.p-last_seen from t}

/ Workers whose last beat is older than `max_age`.
/ .
/ Returns a TABLE rather than a list of names, because "which workers are
/ stale" is never the whole question - how stale, and in what state, is what
/ decides whether to page someone.
/ @param max_age a timespan, e.g. 0D00:05
/ @eg .qhb.stale[0D00:05]
stale:{[max_age]
    if[not 16h=abs type max_age;
        '"stale: max_age must be a timespan, e.g. 0D00:05"];
    select from report[] where age>max_age}

/ Has this worker ever beaten at all?
/ .
/ The distinction the table would otherwise lose. A worker with no row has
/ NEVER STARTED; a worker with an old row started and stopped. Both are
/ "not beating", and they need different people woken up, so `stale` above
/ deliberately cannot report the first - it has no age to compare.
/ @eg .qhb.has_beaten[`demo_deals_backfill]
has_beaten:{[worker] worker in exec worker from report[]}

/ Registered workers that have never beaten, from .qbw's registry.
/ .
/ Derived from the worker registry rather than from a second list, so a
/ worker cannot be missing from monitoring by being forgotten here - the
/ same reason .qdag adopts rather than asking anyone to re-declare.
never_started:{[]
    if[not `qbw in key `; :`$()];
    ws:key .qbw.config;
    ws where not has_beaten each ws}

\d .
