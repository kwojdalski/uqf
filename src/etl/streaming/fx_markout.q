/ fx_markout.q - live normalized FX markouts, long layout (.qpipe.job.fx_markout, #1395).
/ .
/ Subscribes to the canonical `executions` and `market_data`, queues each execution
/ and scores it once its horizons are covered, publishing the long layout of the
/ calculation onto `fx_markout`. See src/etl/transforms/fx_markout.q for what is computed
/ and the decisions behind it, and docs/services/fx-markout.md for the contract.
/ .
/ NOTHING HERE NAMES A SOURCE. A deal tape or an order book is an adapter onto those
/ two tables (src/etl/transforms/fx_source_adapters.q is a worked pair) and a line in
/ the executions or market_data normalizer's `input`; the calculation, the layouts and
/ the horizon engine (src/etl/core/horizon.q) are unchanged by it. fx_markout_wide is the same job with the other layout.
/ .
/ WHAT THE DECLARATION SAYS, each choice and why:
/   event_time, reference_time  `source_time on both: the clock scoring uses is the
/                               source's, never the plant's receipt time
/   ready_on `both              scored only when the horizon has elapsed on the plant
/                               clock AND the reference has covered it
/   legs                        the route of each execution's instrument under the
/                               convention - the same function scoring prices along
/   lookback                    how far back the most negative horizon reaches
/   by `sym, retain_by `sym`source
/                               readiness waits on a leg; eviction keeps each source's
/                               last book of a leg, as the best-of-sources reference needs
/   identity (venue; fill_id)   a redelivery before scoring replaces the pending
/                               execution; one scored within `remember` is dropped
/   max_lateness                how late an execution may arrive and still find its books:
/                               bounds the history instead of the kind's day-long default
/   expire_after                an execution whose references never cover it is given
/                               up on, with the reason in `expired`
/   keep                        only executions the calculation can score, and only
/                               books of pairs the convention routes - the same
/                               predicates scoring applies, so the queue never holds a row
/                               that would fail every tick
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.

\d .

/ What this job keeps of a batch of either table: executions it can score, and books
/ of the pairs the convention routes. Fully qualified, as every global a job reads is.
/ @param x a batch of executions or of market_data
/ @return a boolean per row
/ @eg .qpipe.job.fx_markout.keep[([] sym:`EURUSD`BTCUSDT)]  ->  10b
.qpipe.job.fx_markout.keep:{[x]
    spec:.qpipe.transform.fx_markout_outputs.job_spec;
    $[`fill_id in cols x;
        .qpipe.transform.fx_markout.scoreable[spec;x];
        .qpipe.transform.fx_markout.routable[spec`convention;x`sym]]}

/ Each pending execution's legs: the reference keys readiness waits on.
/ @param pending the pending executions
/ @return a list of symbol lists
/ @eg .qpipe.job.fx_markout.legs[([] sym:enlist `USDPLN)]  ->  enlist `EURUSD`EURPLN
.qpipe.job.fx_markout.legs:{[pending] .qpipe.transform.fx_markout.legs_of[.qpipe.transform.fx_markout_outputs.job_spec;pending]}

\d .

.qetl.job.stream.at_horizons[`fx_markout;`procname`events`reference`transform`publishes`horizon`period`event_time`reference_time`ready_on`legs`lookback`by`retain_by`identity`remember`max_lateness`expire_after`keep`note`ephemeral!(
    `fxmarkout1;
    `executions;
    `market_data;
    `fx_markout_score_long;
    `fx_markout;
    .qpipe.transform.fx_markout.wait[.qpipe.transform.fx_markout_outputs.job_spec;.qpipe.transform.fx_markout_outputs.settle];
    0D00:00:01;
    `source_time;
    `source_time;
    `both;
    .qpipe.job.fx_markout.legs;
    .qpipe.transform.fx_markout.lookback[.qpipe.transform.fx_markout_outputs.job_spec];
    `sym;
    `sym`source;
    `venue`fill_id;
    .qpipe.transform.fx_markout_outputs.remember;
    .qpipe.transform.fx_markout_outputs.max_lateness;
    .qpipe.transform.fx_markout_outputs.expire_after;
    .qpipe.job.fx_markout.keep;
    "normalized FX markouts, long layout: the markout, the execution residual, each cross leg's allocation and the residual, from the canonical executions and market_data. On demand - it needs executions and market_data sources a site wires in";
    "executions waiting for their horizons at a restart are not scored, and the book history is rebuilt from the next snapshots; the job publishes derived rows, not a book of record - hdb_fx_markouts_backfill re-derives any window from the HDB")];
