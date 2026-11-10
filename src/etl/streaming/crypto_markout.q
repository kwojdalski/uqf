/ crypto_markout.q - live markouts on real crypto fills (.qpipe.job.crypto_markout, #1936).
/ .
/ Subscribes to the canonical `executions` and `market_data`, queues each crypto fill
/ and scores it once its horizons are covered, publishing the long layout of the
/ markout contract onto `crypto_markout`: at each horizon the fill price against the
/ BEST MID ACROSS VENUES, in basis points (markout_bps), and net of the fee
/ (markout_net_bps), with the contract's 22 core columns beside them. See
/ src/etl/transforms/crypto_markout.q for the domain (how a BASE-QUOTE pair is priced
/ and the measures), src/etl/transforms/crypto_markout_outputs.q for the spec and the
/ scoring transform, and docs/architecture/markout-contract.md for the contract.
/ .
/ THIS REPLACES the job of the same name that read crypto_trades and crypto_book
/ directly and published crypto_execution_quality (#729). That job scored on the
/ plant's receipt `time` on both inputs, because crypto_trades carried no venue
/ stamp. cryptorust's fills recorder now sends the venue's fill time (cryptorust#1062,
/ crypto_trades.source_time), the executions normalizer puts the fill on the `venue
/ clock where it has one, and the core scores it against books on that clock. A fill
/ without a venue time is on the `receipt clock and the core refuses it against venue
/ books (a `clock_mismatch row naming why) instead of guessing, which is the rule the
/ old job could only avoid by reading `time` on both sides.
/ .
/ NOTHING HERE NAMES A SOURCE. The calculation, the clock rule and the horizon engine
/ (src/etl/core/horizon.q) are the shared ones; the FX markout (fx_markout.q) is the
/ same job over another domain.
/ .
/ WHAT THE DECLARATION SAYS, each choice and why:
/   event_time, reference_time  `source_time on both: the clock scoring uses is the
/                               source's, never the plant's receipt time
/   ready_on `both              scored only when the horizon has elapsed on the plant
/                               clock AND the reference has covered it
/   legs                        the fill's pair, and a conversion pair when the spec
/                               reads a rate from a book - the same function scoring uses
/   max_age                     the spec's: a venue silent for longer than the shared
/                               .qf.mkt.reference_max_age sets no reference and its book
/                               ages out of the history (#1410)
/   by `sym, retain_by `sym`source
/                               readiness waits on a pair; eviction keeps each venue's
/                               last book of it, as the best-across-venues reference needs
/   identity (venue; fill_id)   a redelivery before scoring replaces the pending fill;
/                               one scored within `remember` is dropped
/   lateness                how late a fill may arrive and still find its books: a polled
/                               feed trails the venue by up to a poll interval
/   keep                        only fills the core can score, and only books of pairs it
/                               prices from, so the queue never holds a fill that fails every tick
/   on_start, check, on_fail    the spec's configuration snapshot is recorded (pinned) before the
/                               job opens anything, and a batch whose rows name a snapshot that
/                               is not durably recorded is held whole: a result never becomes
/                               visible before the configuration that explains it (#1999)
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.

\d .

/ What this job keeps of a batch of either table: fills the crypto domain can score, and books of
/ the pairs it prices from. The predicate is the transform namespace's, which the bounded twin
/ shares; the job declares it as its own `keep`. Fully qualified, as every global a job reads is.
/ @param x a batch of executions or of market_data
/ @return a boolean per row
/ @eg .qpipe.job.crypto_markout.keep[([] sym:`$("BTC-USDT";"EURUSD"); source:`a`a)]  ->  10b
.qpipe.job.crypto_markout.keep:{[x] .qpipe.transform.crypto_markout_outputs.keep x}

/ Each pending fill's legs: the reference keys readiness waits on.
/ @param pending the pending executions
/ @return a list of symbol lists
/ @eg .qpipe.job.crypto_markout.legs[([] sym:enlist `$"BTC-USDT")]  ->  enlist enlist `$"BTC-USDT"
.qpipe.job.crypto_markout.legs:{[pending] .qpipe.transform.crypto_markout_outputs.legs pending}

\d .

.qetl.job.stream.at_horizons[`crypto_markout;.qpipe.transform.markout_job.decl[`crypto_markout;(
    (`procname;`crypto_markout1);
    (`events;`executions);
    (`reference;`market_data);
    (`transform;`crypto_markout_score);
    (`outputs;enlist `crypto_markout);
    (`horizon;.qpipe.transform.crypto_markout_outputs.wait[]);
    (`legs;.qpipe.job.crypto_markout.legs);
    (`lookback;.qpipe.transform.crypto_markout_outputs.lookback[]);
    (`max_age;(.qpipe.transform.crypto_markout_outputs.job_spec)`max_age);
    (`remember;.qpipe.transform.crypto_markout_outputs.remember);
    (`lateness;.qpipe.transform.crypto_markout_outputs.lateness);
    (`expire_after;.qpipe.transform.crypto_markout_outputs.expire_after[]);
    (`keep;.qpipe.job.crypto_markout.keep);
    (`note;"markouts on real crypto fills, in bps against the best mid across venues and net of the fee, from the canonical executions and market_data. Not started with the stack: its inputs come from cryptorust's recorders, or from cryptomock1 in their place, neither of which starts by default - `uqs start --profile crypto` brings it up with the mock");
    (`ephemeral;"fills waiting for their horizons at a restart are not scored, and the venue-book history is rebuilt from the next books; the job publishes scores, it holds no book of record - hdb_crypto_markout_backfill re-derives any window from the HDB");
    (`on_start;{[] .qpipe.transform.crypto_markout_outputs.pin[]; (::)}))]];
