// run_backfill_process.q - the q-backfill-process lane (ETL-21).
//
// What separates this lane from q-unit is not the assertions but the
// ENVIRONMENT. These checks are meaningless in-process:
//
//   - a single-instance lock only means something against a real filesystem,
//     and only proves anything if a SECOND process tries to take it. A
//     same-process test can assert acquire_lock throws, but that is q's own
//     refusal, not the mutual exclusion the lock exists to provide.
//   - resumption across a RESTART is the case the checkpoint exists for. A
//     same-process resume never discards its own in-memory state, so it
//     cannot show that the state on disk was sufficient.
//
// Run via `scripts/test.sh q-backfill-process`, which gives each run a fresh
// UQFSTATUSDIR - a stale directory would make a leftover lock look like a
// passing exclusion test.

\c 400 1000

\l tests/lib/qunit.q
\l tests/lib/testutil.q
\l src/init.q
\l scripts/torq_pipeline.q
\l src/etl/core/backfill_state.q
\l src/etl/core/coverage.q
\l src/etl/core/worker_config.q
\l src/etl/core/worker_runtime.q

if[""~getenv `UQFSTATUSDIR;
    -1 "run_backfill_process: UQFSTATUSDIR is unset - refusing to run against a shared default, since a stale lock there would make this lane pass spuriously";
    exit 2];

statusdir:getenv `UQFSTATUSDIR;
system"mkdir -p ",statusdir;
.testutil.reset_coverage_ledger[];

Q:$[""~getenv `UQFQ; (getenv[`HOME]),"/.kx/bin/q"; getenv `UQFQ];

failures:0;
/ `label`, not `desc`: desc is a q BUILTIN (descending sort), so using it as
/ a parameter name gives a bare `nyi at CALL time rather than an error at
/ definition - the same trap torq_pipeline.q's timer_desc names around.
check:{[label;ok]
    -1 $[ok;"pass  ";"FAIL  "],label;
    if[not ok; `failures set failures+1];
    ok}

/ --- single instance, against a real second process ---------------------

lockworker:`crossproc;
.qbfstate.release_lock lockworker;
held:.qbfstate.acquire_lock lockworker;
check["this process holds the lock";.qbfstate.lock_held lockworker];

/ A genuinely separate q process, which is the only thing that tests mutual
/ exclusion rather than q's own bookkeeping. It must FAIL to acquire.
/ Two details that are not stylistic. `< /dev/null`: the child inherits this
/ process's stdin, and a q child left reading it hangs the parent's `system`
/ call indefinitely - which looks like a slow test rather than a deadlock.
/ And no `-q`: under KDB-X that flag suppressed the child's `-1` output
/ entirely, so every check below would have failed for want of a line to
/ match rather than for want of the behaviour. The banner it re-enables is
/ harmless, since the checks match a line rather than the whole output.
child:"QHOME=",getenv[`QHOME]," UQFSTATUSDIR=",statusdir," ",Q,
      " tests/q/try_acquire.q < /dev/null 2>/dev/null";
out:@[{system x};child;{enlist "SPAWN-FAILED: ",x}];
check["a second process is refused the lock";any out like\: "REFUSED*"];

.qbfstate.release_lock lockworker;
check["the lock is gone after release";not .qbfstate.lock_held lockworker];

/ ...and a second process CAN take it once released, which is what makes the
/ refusal above meaningful rather than a process that always fails.
out2:@[{system x};child;{enlist "SPAWN-FAILED: ",x}];
check["a second process acquires a released lock";any out2 like\: "ACQUIRED*"];
.qbfstate.release_lock lockworker;

/ --- resumption across a restart ----------------------------------------

d:{[n] 2026.09.10D00:00:00.000000000+n*1D};
spec:`source_version`range_from`range_to!(`v1;d 1;d 4);
rworker:`restartproc;
.qbfstate.clear_checkpoint rworker;
.qbfstate.save_checkpoint[rworker;spec;d 2];

/ Read the checkpoint from a FRESH process, which shares nothing with this
/ one but the directory. That is the actual requirement: the state on disk
/ must be sufficient to resume.
rchild:"QHOME=",getenv[`QHOME]," UQFSTATUSDIR=",statusdir," ",Q,
       " tests/q/read_checkpoint.q < /dev/null 2>/dev/null";
rout:@[{system x};rchild;{enlist "SPAWN-FAILED: ",x}];
check["a fresh process resumes from the cursor on disk";any rout like\: "CURSOR:2026.09.12D00:00:00.000000000*"];

.qbfstate.clear_checkpoint rworker;
cout:@[{system x};rchild;{enlist "SPAWN-FAILED: ",x}];
check["a cleared checkpoint gives a fresh process nothing to resume from";any cout like\: "CURSOR:0Np*"];

/ --- the coverage ledger survives the process (ETL-07) -------------------

/ The requirement calls etl_coverage the channel for "durable cross-process
/ completeness". It was neither: an in-memory table created by attach, never
/ written anywhere, so a bounded worker - which runs a range and exits - took
/ its coverage with it and ETL-13's skip-what-is-covered could not fire
/ across runs. Nothing caught that, because a single long-lived process
/ behaves correctly.
/ .
/ This is the check that would have. Stage here, read from a process that
/ shares nothing with this one but the directory.

.qcov.attach[];
.qcov.stage_completion[`durable_ds;`v1;d 1;d 2;7];

cchild:"QHOME=",getenv[`QHOME]," UQFSTATUSDIR=",statusdir," ",Q,
       " tests/q/read_coverage.q < /dev/null 2>/dev/null";
covout:@[{system x};cchild;{enlist "SPAWN-FAILED: ",x}];
check["a fresh process sees coverage this one staged";any covout like\: "ROWS:1*"];
check["and can answer is_covered from it";any covout like\: "COVERED:yes*"];

/ A withdrawn claim must stay withdrawn across a restart - a supersession
/ that did not persist would let a claim come back from the dead, which is
/ the worst failure this ledger has.
.qcov.supersede[`durable_ds;`v1;d 1;d 2];
supout:@[{system x};cchild;{enlist "SPAWN-FAILED: ",x}];
check["a supersession survives the process too";any supout like\: "COVERED:no*"];

-1 "";
-1 "==================== q-backfill-process ====================";
-1 $[0=failures; "all checks passed"; (string failures)," check(s) FAILED"];
-1 "============================================================";
exit $[0=failures; 0; 1];
