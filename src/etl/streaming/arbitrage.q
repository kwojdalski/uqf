/ arbitrage.q - direct cross-source price opportunities (.qpipe.job.arbitrage).
/ .
/ One status row per pair and superbook snapshot, including active=0b when
/ an opportunity disappears. Gross quoted opportunity only: no fees, credit
/ checks, execution or synthetic crosses. Size is the smaller base quantity
/ at the two selected levels, not a sum that reuses the same liquidity.

\d .qpipe.job.arbitrage

publish:.qetl.job.stream.unwired `arbitrage;
arbitrage:([] sym:`symbol$(); as_of:`timestamp$(); active:`boolean$(); buy_source:`symbol$(); sell_source:`symbol$(); ask:`float$(); bid:`float$(); size:`float$(); gross_edge:`float$(); gross_profit:`float$())

/ Find the widest strictly positive bid-minus-ask across distinct sources.
/ A crossed single source is not a cross-source opportunity; still consider
/ the other sources, instead of testing only the first bid and first ask.
/ @param book one sorted superbook row
/ @return one arbitrage status row, inactive with null prices when none exists
/ @eg (.qpipe.job.arbitrage.opportunity[first 1#.qpipe.job.superbook.superbook]) 2 -> 0b
opportunity:{[book]
    result:(book`sym;book`as_of;0b;`;`;0n;0n;0n;0n;0n);
    best_edge:0f;
    i:0;
    while[i<count book`bid_prices;
        candidates:where (book`ask_sources)<>(book`bid_sources) i;
        if[count candidates;
            j:first candidates;
            edge:((book`bid_prices) i)-(book`ask_prices) j;
            if[edge>best_edge;
                best_edge:edge;
                qty:((book`bid_sizes) i)&(book`ask_sizes) j;
                result:(book`sym;book`as_of;1b;(book`ask_sources) j;
                    (book`bid_sources) i;(book`ask_prices) j;(book`bid_prices) i;
                    qty;edge;qty*edge)]];
        i+:1];
    result}

/ Convert complete superbook snapshots into opportunity status rows.
/ @param batch superbook rows
/ @return the arbitrage status table
/ @eg count .qpipe.job.arbitrage.evaluate[.qpipe.job.superbook.superbook] -> 0
evaluate:{[batch]
    result:0#.qpipe.job.arbitrage.arbitrage;
    i:0;
    while[i<count batch; result:result upsert opportunity batch i; i+:1];
    result}

/ Publish status for every updated pair, including clears.
/ @param t incoming table name
/ @param x superbook rows
/ @return nothing
/ @eg .qpipe.job.arbitrage.on_batch[`unrelated;()]
on_batch:{[t;x]
    if[not t=`superbook; :()];
    if[count x; .qpipe.job.arbitrage.publish[`arbitrage;evaluate x]];
    }

\d .

.qetl.job.stream.define[`arbitrage;`procname`subscribe_to`publishes`on_batch`note!(
    `arbitrage1;
    enlist `superbook;
    enlist `arbitrage;
    .qpipe.job.arbitrage.on_batch;
    "gross direct cross-source opportunities, including inactive clearing rows. Tail of the marketdata1 chain - see there")];
