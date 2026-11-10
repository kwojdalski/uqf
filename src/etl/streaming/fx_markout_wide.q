/ fx_markout_wide.q - live normalized FX markouts, wide layout (.qpipe.job.fx_markout_wide, #1395).
/ .
/ Subscribes to the canonical `executions` and `market_data`, queues each execution
/ and scores it once its horizons are covered, publishing the wide layout of the
/ calculation onto `fx_markout_wide`. See src/etl/transforms/fx_markout.q for what is computed
/ and the decisions behind it, and docs/services/fx-markout.md for the contract.
/ .
/ NOTHING HERE NAMES A SOURCE. A deal tape or an order book is an adapter onto those
/ two tables (src/etl/transforms/fx_source_adapters.q is a worked pair) and a line in
/ the executions or market_data normalizer's `input`; the calculation, the layouts and
/ the horizon engine (src/etl/core/horizon.q) are unchanged by it. fx_markout is the same job with the long layout.
/ .
/ WHAT THE DECLARATION SAYS, each choice and why:
/   event_time, reference_time  `source_time on both: the clock scoring uses is the
/                               source's, never the plant's receipt time
/   ready_on `both              scored only when the horizon has elapsed on the plant
/                               clock AND the reference has covered it
/   legs                        the route of each execution's instrument under the
/                               convention - the same function scoring prices along
/   lookback                    how far back the most negative horizon reaches
/   max_age                     the spec's: a source silent for longer than the shared
/                               .qf.mkt.reference_max_age sets no reference and its book
/                               ages out of the history (#1410)
/   by `sym, retain_by `sym`source
/                               readiness waits on a leg; eviction keeps each source's
/                               last book of a leg, as the best-of-sources reference needs
/   identity (venue; fill_id)   a redelivery before scoring replaces the pending
/                               execution; one scored within `remember` is dropped
/   lateness                how late an execution may arrive and still find its books:
/                               bounds the history instead of the kind's day-long default
/   expire_after                an execution whose references never cover it is given
/                               up on, with the reason in `expired`
/   keep                        only executions the calculation can score, and only
/                               books of pairs the convention routes - the same
/                               predicates scoring applies, so the queue never holds a row
/                               that would fail every tick
/   on_start, check, on_fail    the spec's configuration snapshot is recorded (pinned) before the
/                               job opens anything, and a batch whose rows name a snapshot that
/                               is not durably recorded is held whole: a result never becomes
/                               visible before the configuration that explains it (#1999)
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.

\d .

/ What this job keeps of a batch of either table: executions it can score, and books
/ of the pairs the convention routes. Fully qualified, as every global a job reads is.
/ @param x a batch of executions or of market_data
/ @return a boolean per row
/ @eg .qpipe.job.fx_markout_wide.keep[([] sym:`EURUSD`BTCUSDT)]  ->  10b
.qpipe.job.fx_markout_wide.keep:{[x]
    spec:.qpipe.transform.fx_markout_outputs.job_spec;
    $[`fill_id in cols x;
        .qpipe.transform.fx_markout.scoreable[spec;x];
        .qpipe.transform.fx_markout.routable[spec`convention;x`sym]]}

/ Each pending execution's legs: the reference keys readiness waits on.
/ @param pending the pending executions
/ @return a list of symbol lists
/ @eg .qpipe.job.fx_markout_wide.legs[([] sym:enlist `USDPLN)]  ->  enlist `EURUSD`EURPLN
.qpipe.job.fx_markout_wide.legs:{[pending] .qpipe.transform.fx_markout.legs_of[.qpipe.transform.fx_markout_outputs.job_spec;pending]}

\d .

.qetl.job.stream.at_horizons[`fx_markout_wide;.qpipe.transform.markout_job.decl[`fx_markout_wide;(
    (`procname;`fxmarkoutwide1);
    (`events;`executions);
    (`reference;`market_data);
    (`transform;`fx_markout_score_wide);
    (`outputs;enlist `fx_markout_wide);
    (`horizon;.qpipe.transform.fx_markout.wait[.qpipe.transform.fx_markout_outputs.job_spec;.qpipe.transform.fx_markout_outputs.settle]);
    (`legs;.qpipe.job.fx_markout_wide.legs);
    (`lookback;.qpipe.transform.fx_markout.lookback[.qpipe.transform.fx_markout_outputs.job_spec]);
    (`max_age;.qpipe.transform.fx_markout_outputs.job_spec`max_age);
    (`remember;.qpipe.transform.fx_markout_outputs.remember);
    (`lateness;.qpipe.transform.fx_markout_outputs.lateness);
    (`expire_after;.qpipe.transform.fx_markout_outputs.expire_after[]);
    (`keep;.qpipe.job.fx_markout_wide.keep);
    (`note;"normalized FX markouts, wide layout: the markout, the execution residual, each cross leg's allocation and the residual, from the canonical executions and market_data. On demand - it needs executions and market_data sources a site wires in");
    (`ephemeral;"executions waiting for their horizons at a restart are not scored, and the book history is rebuilt from the next snapshots; the job publishes derived rows, not a book of record - hdb_fx_markout_wide_backfill re-derives any window from the HDB");
    (`on_start;{[] .qpipe.transform.fx_markout_outputs.pin[]; (::)}))]];
