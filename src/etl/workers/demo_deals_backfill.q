/ demo_deals_backfill.q - the demo deals bounded worker (.qddbf).
/ .
/ A declaration over the generic shell in src/etl/core/bounded_worker.q
/ (#124). This file was 238 lines of which four were worker-specific; the
/ other 234 were glue every bounded worker needs, and are now in .qbw once.
/ .
/ WHAT IT IS AND IS NOT
/ .
/ A real worker with a synthetic source, not a demonstration of a worker.
/ The lifecycle, coverage and retry behaviour are the production ones; only
/ the rows are invented (A-04). Its tests say nothing about the real source's
/ schema, which only .qsrc.validate_live on the work machine can settle.
/ .
/ WHY THE GLOBALS ARE STILL DECLARED HERE
/ .
/ ETL-01's contract, enforced by .qbfstate.require_contract, requires
/ source_version/range_from/range_to to be names in THIS namespace. Moving
/ them into the shell would make the contract check inspect the shell rather
/ than the worker, and every worker would pass it vacuously. So the shell
/ reads and writes them here - see bounded_worker.q's header.

\d .qddbf

worker_name:`demo_deals_backfill

/ --- the contract's required globals (ETL-01, ETL-02) --------------------

/ Explicit and inspectable rather than buried in a call, which is what
/ ETL-02 asks for: a bounded worker must make its bound visible. Written by
/ .qbw.init.
source_version:`;
range_from:0Np;
range_to:0Np;

/ The live handle, or 0Ni when running on the fixture. Resolved at init.
handle:0Ni;

/ Run accumulators, written by the shell. Here rather than in .qbw because a
/ q lambda does not close over an enclosing local, so `each` over windows
/ needs a named place to put the running totals - and one per worker, or two
/ workers running in one process would share them.
progress:`windows_completed`windows_failed`rows_published`cursor!(0;0;0;0Np);
last_batch:();

/ --- the contract's required methods, delegated -------------------------

/ Ordinary names in this namespace that happen to delegate. A worker needing
/ a genuinely different publish path defines its own here and the shell does
/ not object - .qbw is a default, not an owner.
spec:{[] .qbw.spec worker_name}
init:{[run_spec] .qbw.init[worker_name;run_spec]}
plan:{[cursor] .qbw.plan[worker_name;cursor]}
fetch:{[from_ts;to_ts] .qbw.fetch[worker_name;from_ts;to_ts]}
publish:{[batch] .qbw.publish[worker_name;batch]}
checkpoint:{[cursor] .qbw.checkpoint[worker_name;cursor]}
run:{[] .qbw.run worker_name}
cleanup:{[] .qbw.cleanup worker_name}

\d .

/ Window width is this worker's own business rather than part of the
/ contract, but it is what makes the bound observable window by window.
.qbw.define[`demo_deals_backfill;
    `ns`source`dataset`width!(`.qddbf;`demo_deals;`demo_deals;1D)];
