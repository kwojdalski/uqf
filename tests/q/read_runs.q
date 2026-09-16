/ read_runs.q - print the run ledger a FRESH process can see.
/ .
/ The child half of run_backfill_process.q's run-durability check. Loads a
/ minimal subset deliberately: this must prove the tables on disk are
/ sufficient, so pulling in the whole tree - and anything that might begin a
/ run of its own - would weaken what the check establishes.
/ .
/ Run from the repository root, with UQFSTATUSDIR pointing at the directory
/ the parent used.

system"l src/init.q";
system"l src/etl/core/backfill_state.q";
system"l src/etl/core/coverage.q";
system"l src/etl/core/run.q";
system"l scripts/torq_pipeline.q";

.qrun.attach[];
-1 "RUNS:",string count .qrun.history[];
-1 "UNFINISHED:",string count .qrun.unfinished[];
/ The question run identity exists to answer, asked from a process that did
/ not do the work: given a coverage row, what execution produced it?
.qcov.attach[];
rid:first exec run_id from .qcov.ledger[] where dataset=`run_ds;
-1 "RESOLVES:",$[0<count .qrun.of_run[rid];"yes";"no"];
-1 "WORKER:",$[0<count .qrun.of_run[rid];string first exec worker from .qrun.of_run[rid];"none"];
exit 0
