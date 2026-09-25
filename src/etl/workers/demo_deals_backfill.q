/ demo_deals_backfill.q - the demo deals bounded worker (.qwrk.demo_deals_backfill).
/ .
/ A declaration over the generic shell in src/etl/core/bounded_worker.q
/ (#124). This file was 238 lines of which four were worker-specific; the
/ other 234 were glue every bounded worker needs, and are now in .qbw once.
/ .
/ WHAT IT IS AND IS NOT
/ .
/ A real worker with a synthetic source, not a demonstration of a worker.
/ The lifecycle, coverage and retry behaviour are the production ones; only
/ the rows are invented. Its tests say nothing about the real source's
/ schema, which only .qsrc.validate_live on the work machine can settle.
/ .
/ WHERE THE CONTRACT'S NAMES ARE
/ .
/ In this namespace, stamped by the define call at the bottom (#227):
/ The contract's globals and the delegating methods are written here by .qbw
/ because require_contract looks for them HERE, and a worker that wanted a
/ different fetch or publish would define its own above that call. This
/ file declares only what is this worker's: its check and its transform.

\d .qwrk.demo_deals_backfill

/ --- the data-quality gate ------------------------------------------------

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
/ @eg .qwrk.demo_deals_backfill.quality_check[.qfeed.demo_deals.fixture[]]  ->  an empty table
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
.qxf.passthrough[`demo_deals_passthrough;`batch;0#.qfeed.demo_deals.fixture[];.qfeed.demo_deals.fixture[]];

/ Window width is this worker's own business rather than part of the
/ contract, but it is what makes the bound observable window by window.
/ .
/ `check` is optional on the declaration; this worker declares one, which
/ makes it unskippable for every window this worker ever fetches.
.qbw.define[`demo_deals_backfill;
    `source`dataset`width`transform`check`procname`note!
    (`demo_deals;`demo_deals;1D;`demo_deals_passthrough;.qwrk.demo_deals_backfill.quality_check;
     `deals_backfill1;
     "bounded: runs a window range and exits, so it must not start with the stack")];
