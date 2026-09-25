/ market_data.q - direct FX source snapshots in one shape (.qpipe.job.market_data).
/ .
/ A row replaces the whole book for (sym, source); empty ladders withdraw it.
/ source identifies independent liquidity, not a transport connection. Two
/ adapters carrying the same liquidity must use the same source identifier.
/ The existing quote and quotes feeds carry no exchange event timestamp, so
/ source_time preserves their ORIGINAL plant timestamp, before normalization.

\d .qpipe.job.market_data

publish:.qetl.job.stream.unwired `market_data;
market_data:([] sym:`symbol$(); source:`symbol$(); source_time:`timestamp$(); bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:())
quote:([] time:`timestamp$(); sym:`symbol$(); bid:`float$(); ask:`float$(); bsize:`long$(); asize:`long$(); src:`symbol$())
quotes:([] time:`timestamp$(); sym:`symbol$(); bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:())

/ Direct top-of-book quotes, with the feed's own source identifier.
/ The shared quote table also carries equities; only canonical FX pairs pass.
/ @param batch quote rows
/ @return complete single-level market_data snapshots, sizes in base currency
/ @eg count .qpipe.job.market_data.from_quote[.qpipe.job.market_data.quote] -> 0
from_quote:{[batch]
    fx:batch where .qccy.is_ccy_pair each batch`sym;
    select sym, source:src, source_time:time,
        bid_prices:enlist each bid, bid_sizes:enlist each `float$bsize,
        ask_prices:enlist each ask, ask_sizes:enlist each `float$asize from fx}

/ The single synthetic depth feed, identified separately from UQFFX.
/ quotes has no source column: another publisher must use market_data or
/ add its own declared mapping, rather than interleave unidentified books.
/ @param batch quotes rows
/ @return complete market_data snapshots with the original receipt time
/ @eg count .qpipe.job.market_data.from_quotes[.qpipe.job.market_data.quotes] -> 0
from_quotes:{[batch]
    fx:batch where .qccy.is_ccy_pair each batch`sym;
    select sym, source:`UQFDEPTH, source_time:time,
        bid_prices, bid_sizes, ask_prices, ask_sizes from fx}

\d .

.qetl.transform.define[`market_data_from_quote;`inputs`output`fn`examples!(
    (enlist `quote)!enlist .qpipe.job.market_data.quote;
    .qpipe.job.market_data.market_data;
    .qpipe.job.market_data.from_quote;
    enlist `inputs`expected!(
        (enlist `quote)!enlist ([] time:enlist 2026.09.19D10:00:00.000000000;
            sym:enlist `EURUSD; bid:enlist 1.1; ask:enlist 1.2;
            bsize:enlist 1000; asize:enlist 2000; src:enlist `LP_A);
        ([] sym:enlist `EURUSD; source:enlist `LP_A;
            source_time:enlist 2026.09.19D10:00:00.000000000;
            bid_prices:enlist enlist 1.1; bid_sizes:enlist enlist 1000f;
            ask_prices:enlist enlist 1.2; ask_sizes:enlist enlist 2000f)))];

.qetl.transform.define[`market_data_from_quotes;`inputs`output`fn`examples!(
    (enlist `quotes)!enlist .qpipe.job.market_data.quotes;
    .qpipe.job.market_data.market_data;
    .qpipe.job.market_data.from_quotes;
    enlist `inputs`expected!(
        (enlist `quotes)!enlist ([] time:enlist 2026.09.19D10:00:00.000000000;
            sym:enlist `EURUSD; bid_prices:enlist 1.1 1.09; bid_sizes:enlist 1000 2000f;
            ask_prices:enlist 1.2 1.21; ask_sizes:enlist 3000 4000f);
        ([] sym:enlist `EURUSD; source:enlist `UQFDEPTH;
            source_time:enlist 2026.09.19D10:00:00.000000000;
            bid_prices:enlist 1.1 1.09; bid_sizes:enlist 1000 2000f;
            ask_prices:enlist 1.2 1.21; ask_sizes:enlist 3000 4000f)))];

.qetl.job.stream.normalize[`market_data;`procname`output`input`note!(
    `marketdata1;
    .qpipe.job.market_data.market_data;
    `quote`quotes!`market_data_from_quote`market_data_from_quotes;
    "direct FX snapshots with source identity and original receipt time. Head of a closed three-process chain - market_data is read only by superbook1, superbook only by arbitrage1, and arbitrage by nothing - so the whole chain is on demand together and no default-start job notices. startwithall:0 because this chain is three more plant connections than the default start holds, and the licence budget has no room for them (#285): `uqs start --profile arbitrage` is the supported route - it starts the chain and both its detectors as a set that fits")];
