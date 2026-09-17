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

/ --- the data-quality gate ------------------------------------------------

\d .qddbf

/ Refuse a batch that is shaped correctly but cannot be true.
/ .
/ Runs between fetch and publish, so a batch that fails is never published
/ and its window is never recorded as covered - the next run plans it again.
/ Before this gate existed the ledger would have recorded a window of nulls
/ or of zero rates as complete, and read as published forever.
/ .
/ The three conditions are deliberately ones no correct deal can meet, rather
/ than statistical outlier detection: a non-positive rate or notional is
/ arithmetically impossible for a trade, and a null deal_id cannot be
/ reconciled against anything. A check that fires on merely UNUSUAL data
/ would train its reader to ignore it, and an ignored check is worse than
/ none because it still reads as protection.
/ .
/ Returns FAILURES, so an empty table means the batch passed - the shape
/ .qdqc.summarize_checks already uses.
/ @param batch the fetched rows, before publication
/ @return a table check/status/detail, one row per offending row
/ @eg .qddbf.quality_check[.qsdemo.fixture[]]  ->  an empty table
quality_check:{[batch]
    if[0=count batch; :.qbw.no_failures[]];
    bad_rate:select from batch where not rate>0;
    bad_notional:select from batch where not notional>0;
    bad_id:select from batch where null deal_id;
    raze {[nm;t]
        if[0=count t; :.qbw.no_failures[]];
        ([] check:count[t]#nm; status:count[t]#`breach; detail:.Q.s1 each t)
      }'[`nonpositive_rate`nonpositive_notional`null_deal_id;
         (bad_rate;bad_notional;bad_id)]}

\d .

/ The transform. Pass-through: this job copies deals into their target
/ unchanged, and declaring that is what makes it a tested claim. The example
/ is the source's own hand-written fixture.
.qxf.passthrough[`demo_deals_passthrough;`batch;0#.qsdemo.fixture[];.qsdemo.fixture[]];

/ Window width is this worker's own business rather than part of the
/ contract, but it is what makes the bound observable window by window.
/ .
/ `check` is optional on the declaration; this worker declares one, which
/ makes it unskippable for every window this worker ever fetches.
.qbw.define[`demo_deals_backfill;
    `ns`source`dataset`width`transform`check!
    (`.qddbf;`demo_deals;`demo_deals;1D;`demo_deals_passthrough;.qddbf.quality_check)];
