/ eq_markout.q - live markouts on equity fills (.qpipe.job.eq_markout, #1981).
/ .
/ Subscribes to the canonical `executions` and `market_data`, queues each equity fill and
/ scores it once its horizons are covered, publishing the long layout of the markout contract
/ onto `eq_markout`: at each horizon the fill price against the MID of the venue's book, in
/ basis points (markout_bps), with the contract's 22 core columns beside it. See
/ src/etl/transforms/eq_domain.q for the domain (an equity's quote currency, its sessions and
/ halts), src/etl/transforms/eq_markout_outputs.q for the spec and the scoring transform, and
/ docs/architecture/markout-contract.md for the contract.
/ .
/ WHAT IT NEEDS IN ORDER TO DO ANYTHING. Equity books reach `market_data` from the Databento
/ bundle's databento1 (clock `venue, source XNAS); equity fills reach `executions` from a site's
/ live fills feed, which this tree does not ship - eq_fills_backfill writes the HDB, not the live
/ tape. So on its own the job idles, and it is not started with the stack. What is held here is
/ the other half of the pair: a site that wires a live equity fills feed gets markouts the moment
/ it publishes, from the very calculation hdb_eq_markout_backfill applies to the HDB.
/ .
/ NOTHING HERE NAMES A SOURCE. The calculation, the clock rule and the horizon engine
/ (src/etl/core/horizon.q) are the shared ones; fx_markout.q and crypto_markout.q are the same
/ job over other domains.
/ .
/ WHAT THE DECLARATION SAYS, each choice and why:
/   event_time, reference_time  `source_time on both: the clock scoring uses is the
/                               source's, never the plant's receipt time
/   ready_on `both              scored only when the horizon has elapsed on the plant
/                               clock AND the reference has covered it
/   legs                        the fill's symbol: an equity is a single leg, and the spec
/                               reads no conversion book
/   max_age                     the spec's: a book older than that sets no reference and ages
/                               out of the history
/   by `sym, retain_by `sym`source
/                               readiness waits on a symbol; eviction keeps each source's
/                               last book of it
/   identity (venue; fill_id)   a redelivery before scoring replaces the pending fill;
/                               one scored within `remember` is dropped
/   lateness                how late a fill may arrive and still find its books: a broker's
/                               drop copy trails the exchange by seconds
/   keep                        only the equity venues' fills the core can score, and only the
/                               equity venues' books, so the queue never holds an FX or crypto
/                               fill of the shared executions table
/   on_start, check, on_fail    the spec's configuration snapshot is recorded (pinned) before the
/                               job opens anything, and a batch whose rows name a snapshot that
/                               is not durably recorded is held whole: a result never becomes
/                               visible before the configuration that explains it (#1999)
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.

\d .

/ What this job keeps of a batch of either table: the equity venues' fills the domain can score,
/ and the equity venues' books. The predicate is the transform namespace's, which the bounded
/ twin shares; the job declares it as its own `keep`. Fully qualified, as every global a job reads is.
/ @param x a batch of executions or of market_data
/ @return a boolean per row
/ @eg .qpipe.job.eq_markout.keep[([] sym:`AAPL`AAPL; source:`XNAS`a)]  ->  10b
.qpipe.job.eq_markout.keep:{[x] .qpipe.transform.eq_markout_outputs.keep x}

/ Each pending fill's legs: the reference keys readiness waits on.
/ @param pending the pending executions
/ @return a list of symbol lists
/ @eg .qpipe.job.eq_markout.legs[([] sym:enlist `AAPL)]  ->  enlist enlist `AAPL
.qpipe.job.eq_markout.legs:{[pending] .qpipe.transform.eq_markout_outputs.legs pending}

\d .

.qetl.job.stream.at_horizons[`eq_markout;.qpipe.transform.markout_job.decl[`eq_markout;(
    (`procname;`eq_markout1);
    (`events;`executions);
    (`reference;`market_data);
    (`transform;`eq_markout_score);
    (`outputs;enlist `eq_markout);
    (`horizon;.qpipe.transform.eq_markout_outputs.wait[]);
    (`legs;.qpipe.job.eq_markout.legs);
    (`lookback;.qpipe.transform.eq_markout_outputs.lookback[]);
    (`max_age;(.qpipe.transform.eq_markout_outputs.job_spec)`max_age);
    (`remember;.qpipe.transform.eq_markout_outputs.remember);
    (`lateness;.qpipe.transform.eq_markout_outputs.lateness);
    (`expire_after;.qpipe.transform.eq_markout_outputs.expire_after[]);
    (`keep;.qpipe.job.eq_markout.keep);
    (`note;"markouts on equity fills, in bps against the mid of the venue's book, from the canonical executions and market_data, with the sessions and halts of eq_domain deciding which horizons are priced. Not started with the stack: its books come from the Databento bundle's databento1 and its fills from a live feed a site wires in, neither of which the stack starts");
    (`ephemeral;"fills waiting for their horizons at a restart are not scored, and the book history is rebuilt from the next books; the job publishes scores, it holds no book of record - hdb_eq_markout_backfill re-derives any window from the HDB");
    (`on_start;{[] .qpipe.transform.eq_markout_outputs.pin[]; (::)}))]];
