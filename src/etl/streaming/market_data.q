/ market_data.q - every venue's book in one shape (.qpipe.job.market_data).
/ .
/ A row replaces the whole book for (sym, source); empty ladders withdraw it.
/ source identifies independent liquidity, not a transport connection. Two
/ adapters carrying the same liquidity must use the same source identifier.
/ The quote and fx_orderbook feeds carry no exchange event timestamp, so
/ source_time preserves their ORIGINAL plant timestamp, before normalization;
/ crypto_book carries the venue's own, and that is what source_time holds.
/ .
/ WHOSE CLOCK (#1925). source_time is on different clocks by source, so each
/ row says which in `clock`: `receipt for quote and fx_orderbook (the plant's
/ own time, as above), `venue for crypto_book. The mapping stamps it because
/ the mapping is the only place that knows, and check refuses a row that
/ does not carry one of the two.
/ .
/ FX FROM quote AND fx_orderbook, CRYPTO FROM crypto_book, and each row says
/ which in `market` (#886): the mapping knows, so it records it. The one path from a
/ vendor's book to a standard shape: posbook1 marks positions to the level-0
/ mid of these rows, and superbook1 merges the FX ones by pair. A separate
/ `marks` normalizer used to compute that mid from quote and crypto_book on
/ its own, a second reading of the same books for one consumer.

\d .

.qpipe.job.market_data.publish:.qetl.job.stream.unwired `market_data;
.qpipe.job.market_data.market_data:.qetl.plant.published `market_data
.qpipe.job.market_data.quote:.qetl.plant.columns[`quote;`time`sym`bid`ask`bsize`asize`src]
.qpipe.job.market_data.fx_orderbook:.qetl.plant.shape `fx_orderbook
.qpipe.job.market_data.crypto_book:.qetl.plant.shape `crypto_book

/ Direct top-of-book quotes, with the feed's own source identifier.
/ The shared quote table also carries equities; only canonical FX pairs pass.
/ A side the quote has pulled - a null price, or a size that is null or not
/ positive - is an empty ladder, the market_data withdrawal, not a one-level
/ ladder the publish check would withhold as `bad_level with the row's live
/ side (#2418). A level that claims a price and is not one (negative or
/ infinite, with a positive size) is kept, for the check to refuse.
/ @param batch quote rows
/ @return complete single-level market_data snapshots, sizes in base currency
/ @eg count .qpipe.job.market_data.from_quote[.qpipe.job.market_data.quote] -> 0
/ @eg exec bid_prices from .qpipe.job.market_data.from_quote[([] time:enlist 2026.09.19D10:00:00; sym:enlist `EURUSD; bid:enlist 0n; ask:enlist 1.2; bsize:enlist 0; asize:enlist 1000; src:enlist `LP_A)] -> enlist `float$()
.qpipe.job.market_data.from_quote:{[batch]
    fx:batch where .qf.px.is_ccy_pair each batch`sym;
    ladder:{[on;v] $[on; enlist v; `float$()]};
    bsz:`float$fx`bsize;
    asz:`float$fx`asize;
    bid_on:(not null fx`bid) & 0<bsz;
    ask_on:(not null fx`ask) & 0<asz;
    .qetl.plant.with_flat ([] sym:fx`sym; source:fx`src; market:count[fx]#`fx; source_time:fx`time;
        bid_prices:ladder'[bid_on;fx`bid]; bid_sizes:ladder'[bid_on;bsz];
        ask_prices:ladder'[ask_on;fx`ask]; ask_sizes:ladder'[ask_on;asz]; clock:count[fx]#`receipt)}

/ The single synthetic depth feed, identified separately from UQFFX.
/ fx_orderbook has no source column: another publisher must use market_data or
/ add its own declared mapping, rather than interleave unidentified books.
/ @param batch fx_orderbook rows
/ @return complete market_data snapshots with the original receipt time
/ @eg count .qpipe.job.market_data.from_fx_orderbook[.qpipe.job.market_data.fx_orderbook] -> 0
.qpipe.job.market_data.from_fx_orderbook:{[batch]
    fx:batch where .qf.px.is_ccy_pair each batch`sym;
    .qetl.plant.with_flat select sym, source:`UQFDEPTH, market:`fx, source_time:time,
        bid_prices, bid_sizes, ask_prices, ask_sizes, clock:`receipt from fx}

/ A crypto venue's ladder, already level-0-first, with the venue as source.
/ .
/ source_time is crypto_book's `source_time` - the VENUE's stamp - never its
/ `time`, the plant's receipt stamp: a row replayed or backfilled hours later
/ must not claim the venue quoted it when the plant happened to receive it.
/ No FX filter, and `market` is `crypto by construction: the mapping knows
/ which market it reads, so no reader downstream guesses it from how a sym
/ is spelled (#886) - a venue spelling a pair XBTEUR stays crypto. And the
/ clock is `venue for the same reason: that is whose stamp source_time is.
/ @param batch crypto_book rows
/ @return complete market_data snapshots, one per venue and sym
/ @eg count .qpipe.job.market_data.from_crypto_book[.qpipe.job.market_data.crypto_book] -> 0
.qpipe.job.market_data.from_crypto_book:{[batch]
    .qetl.plant.with_flat select sym, source:venue, market:`crypto, source_time,
        bid_prices, bid_sizes, ask_prices, ask_sizes, clock:`venue from batch}

/ The rows market_data is about to publish, held to the .qf.mkt check (#944)
/ for a crossed book (bid through the ask; a locked one, bid at the ask,
/ passes, #1176). And to what a book must be before ANY consumer reads
/ it (#1021), judged here once rather than by each: a level 0 that is not a
/ price by .qf.mkt.level_ok (`bad_level), and a source_time further ahead of
/ this host than .qf.mkt.max_clock_lead (`bad_time), and a book superbook
/ could not hold (#2564): a price ladder and its size ladder of different
/ lengths, or a null source (`malformed) - left in, superbook threw on it
/ and dropped the whole batch, good books included. A book with an empty side is NOT a failure - the
/ withdrawal is how a venue pulls its book, and `one_sided is the check's
/ word for it - and a wide spread is a thin market, not bad data, so neither
/ is withheld.
/ .
/ A book's AGE is not judged here (#2456). It was, against the newest row of
/ its own batch, and so the same book was withheld or published depending on
/ which other venues' books the plant delivered with it; a series' own history
/ would be forgotten by a restart instead. How old a book may be is each
/ consumer's decision, already made by its max_age (.qf.mkt.reference_max_age).
/ @param rows market_data rows (sym, source, source_time, the four ladders, clock)
/ @return failures: check, status, detail, and `row`, the index in rows
/ @throws error when any row's clock is null or neither `venue nor `receipt (#1925): a mapping that did not stamp it is a bug, and the whole batch is withheld
/ @eg count .qpipe.job.market_data.check[0#.qpipe.job.market_data.market_data] -> 0
.qpipe.job.market_data.check:{[rows]
    if[0=count rows; :.qetl.job.bounded.no_failures[]];
    .qetl.plant.require_clock[`market_data;rows];
    / The quality check sorts its answer, so it is handed the row index as its
    / `time` (a timestamp is a long underneath) and the index comes back with it.
    / A table literal, not `select time:`timestamp$til count rows, ...`: on
    / KDB-X that select collapses to ONE row of nested columns (PeachQ gives
    / one row per book), and the check then threw 'type there - unseen by CI,
    / whose q suite runs on PeachQ only.
    q:([] time:`timestamp$til count rows; sym:rows`sym; bid_prices:rows`bid_prices;
        bid_sizes:rows`bid_sizes; ask_prices:rows`ask_prices; ask_sizes:rows`ask_sizes);
    graded:.qf.mkt.check_market_data_quality[q;0w];
    bad:select from graded where status=`crossed;
    crossed:([] check:count[bad]#`crossed_book; status:bad`status; detail:.qf.util.full each bad; row:`long$bad`time);
    ahead:where rows[`source_time]>.z.p+.qf.mkt.max_clock_lead;
    / An empty ladder is a withdrawal, not a bad level: only a level that is
    / there and is not a price fails.
    off:{[px;sz] $[count px; not .qf.mkt.level_ok["f"$first px;"f"$first sz]; 0b]};
    bad:where off'[rows`bid_prices;rows`bid_sizes] or off'[rows`ask_prices;rows`ask_sizes];
    level:([] check:count[bad]#`bad_level; status:count[bad]#`bad_level;
        detail:count[bad]#enlist "a level-0 price or size that is not positive and finite"; row:bad);
    timed:([] check:count[ahead]#`bad_time; status:count[ahead]#`future;
        detail:count[ahead]#enlist "source_time is further ahead of this host than .qf.mkt.max_clock_lead"; row:ahead);
    uneven:((count each rows`bid_prices)<>count each rows`bid_sizes) or (count each rows`ask_prices)<>count each rows`ask_sizes;
    bad:where uneven or null rows`source;
    shape:([] check:count[bad]#`malformed_book; status:count[bad]#`malformed;
        detail:{[u] $[u; "a price ladder and its size ladder differ in length"; "the source is null"]} each uneven bad; row:bad);
    crossed,level,timed,shape}

\d .

.qetl.transform.define[`market_data_from_quote;`inputs`output`fn`examples!(
    (enlist `quote)!enlist .qpipe.job.market_data.quote;
    .qpipe.job.market_data.market_data;
    .qpipe.job.market_data.from_quote;
    enlist `inputs`expected!(
        (enlist `quote)!enlist ([] time:enlist 2026.09.19D10:00:00.000000000;
            sym:enlist `EURUSD; bid:enlist 1.1; ask:enlist 1.2;
            bsize:enlist 1000; asize:enlist 2000; src:enlist `LP_A);
        ([] sym:enlist `EURUSD; source:enlist `LP_A; market:enlist `fx;
            source_time:enlist 2026.09.19D10:00:00.000000000;
            bid_prices:enlist enlist 1.1; bid_sizes:enlist enlist 1000f;
            ask_prices:enlist enlist 1.2; ask_sizes:enlist enlist 2000f; clock:enlist `receipt;
            bid:enlist 1.1; ask:enlist 1.2; rid:enlist 0N)))];

.qetl.transform.define[`market_data_from_fx_orderbook;`inputs`output`fn`examples!(
    (enlist `fx_orderbook)!enlist .qpipe.job.market_data.fx_orderbook;
    .qpipe.job.market_data.market_data;
    .qpipe.job.market_data.from_fx_orderbook;
    enlist `inputs`expected!(
        (enlist `fx_orderbook)!enlist ([] time:enlist 2026.09.19D10:00:00.000000000;
            sym:enlist `EURUSD; bid_prices:enlist 1.1 1.09; bid_sizes:enlist 1000 2000f;
            ask_prices:enlist 1.2 1.21; ask_sizes:enlist 3000 4000f);
        ([] sym:enlist `EURUSD; source:enlist `UQFDEPTH; market:enlist `fx;
            source_time:enlist 2026.09.19D10:00:00.000000000;
            bid_prices:enlist 1.1 1.09; bid_sizes:enlist 1000 2000f;
            ask_prices:enlist 1.2 1.21; ask_sizes:enlist 3000 4000f; clock:enlist `receipt;
            bid:enlist 1.1; ask:enlist 1.2; rid:enlist 0N)))];

.qetl.transform.define[`market_data_from_crypto_book;`inputs`output`fn`examples!(
    (enlist `crypto_book)!enlist .qpipe.job.market_data.crypto_book;
    .qpipe.job.market_data.market_data;
    .qpipe.job.market_data.from_crypto_book;
    enlist `inputs`expected!(
        (enlist `crypto_book)!enlist ([] time:enlist 2026.09.17D14:32:09.000000000;
            source_time:enlist 2026.09.17D10:00:01.000000000;
            venue:enlist `binance_spot; sym:enlist `$"BTC-USDT";
            bid_prices:enlist 61999 61998f; bid_sizes:enlist 0.5 1f;
            ask_prices:enlist 62001 62002f; ask_sizes:enlist 0.5 1f);
        ([] sym:enlist `$"BTC-USDT"; source:enlist `binance_spot; market:enlist `crypto;
            source_time:enlist 2026.09.17D10:00:01.000000000;
            bid_prices:enlist 61999 61998f; bid_sizes:enlist 0.5 1f;
            ask_prices:enlist 62001 62002f; ask_sizes:enlist 0.5 1f; clock:enlist `venue;
            bid:enlist 61999f; ask:enlist 62001f; rid:enlist 0N)))];

/ on_fail `drop, not `hold: market_data is every venue's book in one stream,
/ and a batch is whatever the plant delivered together. One venue's crossed
/ book must not withhold the other venues' good ones - posbook1 marks
/ positions to these mids, and a gap in them is worse than a missing bad row.
/ The bad row is withheld, logged and counted (`uqs status` shows the job
/ failing), and the next snapshot for that (sym, source) replaces the book.
.qetl.job.stream.normalize[`market_data;`procname`output`input`start_with_all`note`check`on_fail!(
    `marketdata1;
    .qpipe.job.market_data.market_data;
    `quote`fx_orderbook`crypto_book!`market_data_from_quote`market_data_from_fx_orderbook`market_data_from_crypto_book;
    1b;
    "every venue's book in one shape, with source identity and the source's own time: FX from quote and fx_orderbook, crypto from crypto_book. posbook1 marks positions to its level-0 mids, so it starts with the stack; superbook1 merges the FX books by pair for the arbitrage chain, which stays on demand (`uqs start --profile arbitrage`, #285)";
    .qpipe.job.market_data.check;
    `drop)];
