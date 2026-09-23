/ cross.q - the whole of the cross-rate reprice job (.qsub.cross).
/ .
/ Subscribes to `quotes`, mirrors it, and after every batch reprices four
/ synthetic cross pairs through USD, keeping the result as private process
/ state. It publishes nothing: the crosses are there to be queried on the
/ process itself.
/ .
/ WHAT IS IN THIS FILE: the mirror's schema, the repricing transform with its
/ examples, the batch handler, the job's state, and the declaration the
/ runner reads. Every step, in the order it runs.
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.
/ .
/ The mirror grows for as long as the job runs - a proof-of-concept
/ tradeoff, kept deliberately: an eviction policy here would have to decide
/ how much history a cross chain may need, which is a real question this
/ demo does not answer.

\d .qsub.cross

/ ------------------------------------------------------------- THE SHAPES

/ The synthetic pairs this job reprices - deliberately none of the directly
/ quoted pairs, so every one has to chain through USD.
cross_pairs:`EURJPY`GBPJPY`EURGBP`AUDJPY

/ Mirror of the quotes feed's own schema - what this process receives via
/ its subscription, and exactly what the transform reads.
quotes_in:([] time:`timestamp$(); sym:`symbol$(); bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:())

/ This job's output: one row per (pair, reprice).
cross_quotes:([] time:`timestamp$(); sym:`symbol$(); bid:`float$(); ask:`float$(); mid:`float$())

/ ---------------------------------------------------------- THE TRANSFORM

/ Reprice every cross pair from the quote mirror, as of one instant.
/ .
/ as_of is an ARGUMENT. This job used to call .z.p inside the computation,
/ twice per pair - once for the as-of quote lookup and once for the stamp -
/ so one reprice could price pairs at different instants and no two runs over
/ the same quotes agreed. The caller now reads the clock once.
/ .
/ A pair that cannot be priced - no chain of quoted legs yet, or a leg with
/ no quote before as_of - is left out, as it always was. The batch handler
/ logs which pairs are missing; the reason is not carried out, because a
/ transform has no log to write it to.
/ @param quotes the quote mirror, as this job keeps it
/ @param as_of price every pair from quotes at or before this instant
/ @return one row per pair that could be priced, in cross_pairs order
reprice_crosses:{[quotes;as_of]
    if[0=count quotes; :.qsub.cross.cross_quotes];
    q:`sym`ts xasc select ts:time, sym, bid_prices, bid_sizes, ask_prices, ask_sizes from quotes where time<=as_of;
    if[0=count q; :.qsub.cross.cross_quotes];
    rows:{[q;as_of;pair]
        r:.[.qfwd.cross_book_at;(q;pair;as_of;enlist .qsynth.size_unit;`bid`ask`mid);{[e] ()}];
        $[0=count r; .qsub.cross.cross_quotes;
            ([] time:enlist as_of; sym:enlist pair; bid:r`bid; ask:r`ask; mid:r`mid)]
      }[q;as_of] each .qsub.cross.cross_pairs;
    raze rows}

/ --------------------------------------------------------------- THE JOB

/ Where rows go. This job publishes nothing, so nothing wires this - it
/ stays a stub, and a future edit that starts publishing without declaring
/ it gets an error naming the job rather than silent rows.
publish:.qstream.unwired `cross;

/ The quote mirror, grouped on sym for the as-of lookups the chain does.
quotes:update `g#sym from quotes_in;

/ The repriced crosses, appended to on every batch.
crosses:cross_quotes;

/ Take the batch into the mirror, then reprice every cross pair as of one
/ instant read here.
/ .
/ A pair with no price is logged by name. The reason is not in the line -
/ the transform has no logger - so check that pair's legs are quoted when one
/ keeps appearing.
/ @param tbl the table the batch arrived on
/ @param batch the rows, as a table, already carrying `time`
/ @return nothing
on_batch:{[tbl;batch]
    if[not tbl=`quotes; :()];
    `.qsub.cross.quotes insert batch;
    .qsub.cross.reprice .qsub.cross.now[];
    }

/ Reprice from the current mirror as of `now`, append, and log any pair that
/ could not be priced. Separate from on_batch, and taking the instant as an
/ argument, so a test can drive it without a clock.
/ @param now the instant to price as of
/ @return the rows appended
reprice:{[now]
    if[0=count .qsub.cross.quotes; :.qsub.cross.cross_quotes];
    out:.qxf.apply_as_of[`cross_quotes;enlist[`quotes]!enlist .qsub.cross.quotes;now];
    missing:.qsub.cross.cross_pairs except out`sym;
    if[count missing;
        .qlog.info[`cross;"pairs could not be priced";enlist[`pairs]!enlist missing]];
    if[count out; `.qsub.cross.crosses insert out];
    out}

/ The clock, as a function so a test can replace it.
/ .
/ .z.p, NOT .proc.cp[]. Both read "now", and only one of them agrees with the
/ data: `.u.upd` stamps every row with the tickerplant's own .z.p, so the
/ times this is compared against are UTC. `.proc.cp[]` is `.z.P` - LOCAL time -
/ whenever TorQ was started with -localtime, which the vendored process.csv
/ does for all 23 of its processes.
/ .
/ The comparison below is therefore off by the machine's UTC offset. It was
/ found live as markout scoring trades an hour before their horizon had
/ elapsed on a UTC+1 machine, and patched then by starting that ONE process
/ with localtime=0 - which fixed the arithmetic and left every other process
/ reading a different clock, including this one.
/ .
/ Reading .z.p here is what superbook.q and cross_arbitrage.q already do, and
/ it holds whatever flag the process was started with.
now:{[] .z.p}

\d .

.qxf.define[`cross_quotes;`inputs`output`fn`examples`as_of!(
    enlist[`quotes]!enlist .qsub.cross.quotes_in;
    .qsub.cross.cross_quotes;
    .qsub.cross.reprice_crosses;
    / EURUSD and USDJPY are quoted, so only EURJPY can be built:
    / bid 1.10*150 = 165, ask 1.1002*150.02 = 165.052004, mid their average.
    / The EURUSD quote AFTER as_of must not move the price - that is the
    / whole reason as_of is an argument.
    enlist `inputs`expected`as_of!(
        enlist[`quotes]!enlist ([] time:2026.09.17D10:00:00 2026.09.17D10:00:00 2026.09.17D10:00:05;
            sym:`EURUSD`USDJPY`EURUSD;
            bid_prices:(enlist 1.1;enlist 150f;enlist 1.2);
            bid_sizes:(enlist 5e6;enlist 5e6;enlist 5e6);
            ask_prices:(enlist 1.1002;enlist 150.02;enlist 1.2002);
            ask_sizes:(enlist 5e6;enlist 5e6;enlist 5e6));
        ([] time:enlist 2026.09.17D10:00:01; sym:enlist `EURJPY; bid:enlist 165f; ask:enlist 165.052004; mid:enlist 165.026002);
        2026.09.17D10:00:01);
    1b)];

.qstream.register[`cross;`procname`subscribes`publishes`on_batch`note!(
    `cross1;
    enlist `quotes;
    `symbol$();
    .qsub.cross.on_batch;
    "keeps cross_quotes as private process state, publishes no table - so it is a leaf, and nothing downstream stalls while it is stopped. startwithall:0 to stay inside LICENCE_CONNECTION_LIMIT (#285); quotesfeed1 runs by default, so `uqf-stack start cross1` is enough")];
