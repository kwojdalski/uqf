/ crypto_markout_outputs.q - the crypto markout DATASET: its spec, its scoring
/ transforms, and the predicates the live job and its bounded twin share
/ (.qpipe.transform.crypto_markout_outputs, #1936).
/ .
/ crypto_markout.q (#1934) is the crypto DOMAIN: how a BASE-QUOTE instrument is
/ priced, and the bps and fee measures. This file is what turns it into a
/ dataset: the one grid and policy every producer scores with, the declared
/ transforms, and the keep/legs callbacks. The live job (src/etl/streaming/
/ crypto_markout.q) and the twin (src/etl/workers/hdb_crypto_markout_backfill.q)
/ both apply the transforms declared here, so a live row and a refilled one are
/ produced by the same call and cannot differ, as for fx_markout_outputs.q.
/ .
/ THE INPUTS ARE THE CANONICAL TABLES. `executions` and `market_data`, never
/ crypto_trades and crypto_book: the executions normalizer is what decides whose
/ clock a fill is on (cryptorust's venue fill time where the recorder sends it,
/ the plant's receipt time where it does not, #1936), and the core scores a fill
/ only against books on the same clock. A site whose recorder predates the venue
/ stamp gets `clock_mismatch rows, which say why, instead of a bps figure
/ mixing two clocks; that is what #1923 decided and what the old job, reading
/ the plant's receipt `time` on both, avoided only by never asking.
/ .
/ THE SPEC, each choice and why:
/   horizons      1s and 10s, the old crypto_markout's grid, so the figures are comparable
/   report_ccy    USD, with USDT declared worth 1 USD. The peg is the SPEC's assumption and
/                 every row says so (rate_source `fixed); a site with a USDT-USD book
/                 declares `USDT!`source`USDT-USD instead and the readiness gate then holds
/                 a window until the book exists. A quote currency with no rate is `no_rate:
/                 the bps do not depend on it
/   rate_timing   execution: a fill is valued at the rate it was struck at
/   max_age       .qf.mkt.reference_max_age, the one cross-venue staleness policy: a venue
/                 whose top of book is older than that at the horizon does not count
/ .
/ WHAT STAYS OUT OF THE LONG ROWS. `time` is the plant's on a live row, so the live
/ transform's output is the plant table without it; the twin writes the core's own
/ `time`, the execution's time plus the furthest horizon: the earliest a live row
/ could have been stamped, since all of an execution's horizons are published together.

\d .

/ The executions columns scoring reads: the core's, and the fee the crypto measures net.
.qpipe.transform.crypto_markout_outputs.executions_in:.qetl.plant.columns[`executions;`source_time`venue`sym`side`size`price`fee`fee_ccy`fill_id`clock]

/ The market_data columns scoring reads: the venues' books, one row per snapshot, and the clock each is stamped on.
.qpipe.transform.crypto_markout_outputs.market_data_in:.qetl.plant.columns[`market_data;`source_time`sym`source`bid`ask`clock]

/ The live output: the plant table, without `time`.
.qpipe.transform.crypto_markout_outputs.out:.qetl.plant.published `crypto_markout

/ The grid and policy the live job and its twin score with - one definition.
.qpipe.transform.crypto_markout_outputs.job_spec:`horizons`report_ccy`rate_timing`rates`max_age!(0D00:00:01 0D00:00:10;`USD;`execution;(enlist `USDT)!enlist 1f;.qf.mkt.reference_max_age)

/ The snapshot id the job spec's results belong to: stamped by the live job, its bounded twin
/ and the examples. The spec is complete, so config_markout.id takes it as it stands;
/ tests/q/test_config_markout.q holds that equal to the validated spec's.
.qpipe.transform.crypto_markout_outputs.config_id:.qpipe.transform.config_markout.id[.qpipe.transform.crypto_markout.domain;.qpipe.transform.crypto_markout_outputs.job_spec]

/ Pin the job spec's configuration snapshot: the live job's `on_start` and its twin's `pin`
/ call it, so the snapshot is durable before the first calculation (#1999).
/ @return the snapshot id, as a one-item list
/ @eg count .qpipe.transform.crypto_markout_outputs.pin[]  ->  1
.qpipe.transform.crypto_markout_outputs.pin:{[]
    enlist .qpipe.transform.config_markout.pin[.qpipe.transform.crypto_markout.domain;.qpipe.transform.crypto_markout_outputs.job_spec;"crypto_markout job spec: the live job crypto_markout1 and its bounded twin"]}

/ The least a job waits before scoring, however early its horizons are.
.qpipe.transform.crypto_markout_outputs.settle:0D00:00:01

/ How long a job waits for a fill's references before giving up on it: the wait for
/ its horizons, and five minutes more.
/ @return a timespan
/ @eg .qpipe.transform.crypto_markout_outputs.expire_after[]  ->  0D00:05:10
.qpipe.transform.crypto_markout_outputs.expire_after:{[]
    .qpipe.transform.crypto_markout_outputs.wait[]+0D00:05}

/ How long a job remembers a fill it has scored, to drop a redelivery.
.qpipe.transform.crypto_markout_outputs.remember:0D01

/ How much older than its arrival a fill's source_time can be: the bound on the
/ books the horizon kind keeps for a late fill. A polled feed delivers a fill up to
/ a poll interval after the venue printed it, and the recorder's default poll is
/ one second; thirty seconds leaves room for a slow poll and a reconnect without
/ holding a day of every venue's books (the kind's default for an event on
/ source_time). A fill later than this may find its books evicted and expires unscored.
.qpipe.transform.crypto_markout_outputs.lateness:0D00:00:30

/ How long a job waits before scoring a fill: its furthest horizon, but never less than settle.
/ @return the horizon the horizon kind declares
/ @eg .qpipe.transform.crypto_markout_outputs.wait[]  ->  0D00:00:10
.qpipe.transform.crypto_markout_outputs.wait:{[]
    .qpipe.transform.markout_core.wait[.qpipe.transform.crypto_markout_outputs.job_spec;.qpipe.transform.crypto_markout_outputs.settle]}

/ How far BEFORE a fill the reference is read: the most negative horizon, or none.
/ @return a timespan of zero or more
/ @eg .qpipe.transform.crypto_markout_outputs.lookback[]  ->  0D00:00:00
.qpipe.transform.crypto_markout_outputs.lookback:{[]
    .qpipe.transform.markout_core.lookback .qpipe.transform.crypto_markout_outputs.job_spec}

/ The market_data syms a conversion rate is read from, when the spec declares one
/ as `source`sym rather than a fixed float: books the history must keep.
/ @return a symbol list, empty while every rate is fixed
/ @eg .qpipe.transform.crypto_markout_outputs.rate_syms[]  ->  `symbol$()
.qpipe.transform.crypto_markout_outputs.rate_syms:{[]
    r:value (.qpipe.transform.crypto_markout_outputs.job_spec)`rates;
    declared:r where 11h=type each r;
    `symbol$$[count declared; last each declared; ()]}

/ What the horizon kind keeps of a batch of either table: fills the core can score
/ and the books of the pairs it prices from, so a queue never holds a fill that
/ would fail every tick and the history never holds a pair nothing reads. Fills and
/ books are told apart by `fill_id`, as the FX job does.
/ .
/ The equity venues' fills and books are not kept (#2574): the resolver prices any
/ BASE-QUOTE symbol, so a ticker with a dash - XNAS `BF-B` - read as a pair quoted
/ in `B`. Their venue is the tell, as eq_markout_outputs.q says, and its list is
/ the one place an equity venue is named.
/ @param x a batch of executions or of market_data
/ @return a boolean per row
/ @eg .qpipe.transform.crypto_markout_outputs.keep[([] sym:`$("BTC-USDT";"EURUSD";"BF-B"); source:`a`a`XNAS)]  ->  100b
.qpipe.transform.crypto_markout_outputs.keep:{[x]
    dom:.qpipe.transform.crypto_markout.domain;
    spec:.qpipe.transform.markout_core.validate[dom;.qpipe.transform.crypto_markout_outputs.job_spec];
    eq:.qpipe.transform.eq_markout_outputs.venues;
    if[`fill_id in cols x; :(null .qpipe.transform.markout_core.why[dom;spec;x]) and not (x`venue) in eq];
    syms:distinct x`sym;
    priced:(.qpipe.transform.crypto_markout.resolve[spec;syms]`ok) syms?x`sym;
    (priced or x[`sym] in .qpipe.transform.crypto_markout_outputs.rate_syms[]) and not (x`source) in eq}

/ Each pending fill's legs: the reference keys readiness waits on - its pair, and the
/ conversion pair when the spec reads a rate from a book.
/ @param pending the pending executions
/ @return a list of symbol lists
/ @eg .qpipe.transform.crypto_markout_outputs.legs[([] sym:enlist `$"BTC-USDT")]  ->  enlist enlist `$"BTC-USDT"
.qpipe.transform.crypto_markout_outputs.legs:{[pending]
    .qpipe.transform.markout_core.legs_of[.qpipe.transform.crypto_markout.domain;.qpipe.transform.crypto_markout_outputs.job_spec;pending]}

/ The plant table's rows for some fills and the books around them: the core's rows and
/ the crypto measures, with the core's own `time`. Fills of an equity venue are not scored.
/ @param exe executions, as executions_in
/ @param md market_data, as market_data_in
/ @return crypto_markout's rows, `time` first
/ @eg count .qpipe.transform.crypto_markout_outputs.rows[0#.qpipe.transform.crypto_markout_outputs.executions_in;0#.qpipe.transform.crypto_markout_outputs.market_data_in]  ->  0
.qpipe.transform.crypto_markout_outputs.rows:{[exe;md]
    exe:select from exe where not venue in .qpipe.transform.eq_markout_outputs.venues;
    r:.qpipe.transform.crypto_markout.score[.qpipe.transform.crypto_markout_outputs.job_spec;exe;md];
    (cols .qetl.plant.shape `crypto_markout)#r}

/ The live job's scoring: the rows without `time`, which the plant stamps on publication.
/ @param exe executions, as executions_in
/ @param md market_data, as market_data_in
/ @return the live output's rows
/ @eg count .qpipe.transform.crypto_markout_outputs.live[0#.qpipe.transform.crypto_markout_outputs.executions_in;0#.qpipe.transform.crypto_markout_outputs.market_data_in]  ->  0
.qpipe.transform.crypto_markout_outputs.live:{[exe;md]
    (cols .qpipe.transform.crypto_markout_outputs.out)#.qpipe.transform.crypto_markout_outputs.rows[exe;md]}

/ ------------------------------------------------ THE EXAMPLES' INPUTS AND ROWS
/ .
/ Worked by hand; none is read back from the function it checks. See the
/ declaration below for the arithmetic.

/ One BTC-USDT buy of 0.25 at 62000 with a 15.5 USDT fee, on the venue clock.
/ @private
.qpipe.transform.crypto_markout_outputs.sample_executions:{[]
    ([] source_time:enlist 2026.09.17D10:00:00; venue:enlist `a; sym:enlist `$"BTC-USDT"; side:enlist 1;
        size:enlist 0.25; price:enlist 62000f; fee:enlist 15.5; fee_ccy:enlist `USDT; fill_id:enlist `f1; clock:enlist `venue)}

/ Two venues' books around it: both quote at the fill, then only a does, nine seconds later.
/ @private
.qpipe.transform.crypto_markout_outputs.sample_books:{[]
    ([] source_time:2026.09.17D10:00:00 2026.09.17D10:00:00 2026.09.17D10:00:09;
        sym:3#`$"BTC-USDT"; source:`a`b`a;
        bid:62000 62004 62010f;
        ask:62010 62008 62020f;
        clock:3#`venue)}

/ Its rows: a total and an execution component at each horizon.
/ @private
.qpipe.transform.crypto_markout_outputs.sample_rows:{[]
    n:4;
    ([] time:n#2026.09.17D10:00:10; source_time:n#2026.09.17D10:00:00; clock:n#`venue; venue:n#`a; fill_id:n#`f1; sym:n#`$"BTC-USDT";
        side:n#1; size:n#0.25; horizon:0D00:00:01 0D00:00:01 0D00:00:10 0D00:00:10; kind:n#`total`execution; step:n#0;
        price:n#62000f; quote_ccy:n#`USDT; ref_t0:n#62006f; ref_t1:62006 62006 62015 62015f;
        move:6 6 15 6f; quote_amount:1.5 1.5 3.75 1.5; rate:n#1f; rate_source:n#`fixed;
        rate_sym:n#`; rate_time:n#0Np; report_amount:1.5 1.5 3.75 1.5;
        status:n#`ok; config_id:n#.qpipe.transform.crypto_markout_outputs.config_id;
        markout_bps:10000*6 6 15 6f%62000; fee_quote:n#15.5; fee_bps:n#10f;
        markout_net_bps:(-10+10000*6%62000;0n;-10+10000*15%62000;0n))}

\d .

/ ------------------------------------------------------------- THE EXAMPLES
/ .
/ All worked by hand; none is read back from the function it checks.
/ .
/ A buy of 0.25 BTC at 62000 against two venues. At the fill a quotes 62000/62010 and b
/ 62004/62008: the best bid and ask are b's, mid 62006. +1s: the same books are the
/ latest, so 62006. +10s: b's book is ten seconds old, past the five-second max_age, and
/ a's 9s-old book 62010/62020 is the only one: mid 62015. Moves 6 and 15 on the total; 6 on the
/ execution component at both horizons (the reference at the fill, 62006, less the price, but
/ ref_t1 is the horizon's on every row). bps = 10000*move%62000.
/ The fee is 15.5 USDT on a notional of 0.25*62000 = 15500: 10 bps, taken off the total
/ only. USDT is declared worth 1 USD, so the amounts are size*move: 1.5 and 3.75.
.qetl.transform.define[`crypto_markout_score;`inputs`output`fn`examples!(
    `executions`market_data!(.qpipe.transform.crypto_markout_outputs.executions_in;.qpipe.transform.crypto_markout_outputs.market_data_in);
    .qpipe.transform.crypto_markout_outputs.out;
    .qpipe.transform.crypto_markout_outputs.live;
    enlist `inputs`expected!(
        `executions`market_data!(.qpipe.transform.crypto_markout_outputs.sample_executions[];.qpipe.transform.crypto_markout_outputs.sample_books[]);
        delete time from .qpipe.transform.crypto_markout_outputs.sample_rows[]))];
