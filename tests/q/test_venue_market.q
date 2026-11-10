/ test_venue_market.q - which market an execution belongs to (.qetl.venue, #2650).
/ Load src/init.q, src/etl/init.q, tests/lib/qunit.q and tests/lib/support.q before this file.
/ .
/ The reference values are the venues the stack carries: XNAS (equity), fx (the FX feed's one
/ venue), and the crypto exchanges, which are open-ended, so any other name is crypto.

.qt.qetl.venue.test_a_venue_has_the_market_that_claims_it_and_an_unclaimed_one_is_crypto:{[tc]
    .qunit.assertEquals[.qetl.venue.market_of `XNAS`fx`binance_spot`a;`equity`fx`crypto`crypto;"one market per venue"];
    .qunit.assertEquals[.qetl.venue.market_of `symbol$();`symbol$();"no venues, no markets"]};

.qt.qetl.venue.test_in_market_is_the_boolean_per_venue_each_dataset_keeps_by:{[tc]
    v:`XNAS`fx`binance_spot`bybit;
    .qunit.assertEquals[.qetl.venue.in_market[`crypto;v];0011b;"crypto is the remainder"];
    .qunit.assertEquals[.qetl.venue.in_market[`equity;v];1000b;"equity"];
    .qunit.assertEquals[.qetl.venue.in_market[`fx;v];0100b;"fx"];
    .qunit.assertEquals[(.qetl.venue.in_market[`crypto;v])+(.qetl.venue.in_market[`equity;v])+.qetl.venue.in_market[`fx;v];4#1i;"the markets partition the venues: each belongs to exactly one"]};

.qt.qetl.venue.test_no_venue_is_claimed_by_two_closed_markets:{[tc]
    all_v:raze value .qetl.venue.closed;
    .qunit.assertEquals[count all_v;count distinct all_v;"a venue listed under two markets would be scored by both"];
    .qunit.assertFalse[any null all_v;"a null venue is not a market's"]};

.qt.qetl.venue.test_an_unknown_market_is_refused_with_the_markets_named:{[tc]
    .qunit.assertThrows[{.qetl.venue.in_market[`bonds;`XNAS]};::;"venue: no market bonds; the markets are equity, fx, crypto";"in_market"];
    .qunit.assertThrows[{.qetl.venue.venues_of `bonds};::;"venue: no market bonds; the markets are equity, fx, crypto";"venues_of"]};

.qt.qetl.venue.test_a_closed_market_lists_its_venues_and_the_open_one_has_no_list:{[tc]
    .qunit.assertEquals[.qetl.venue.venues_of `equity;enlist `XNAS;"equity"];
    .qunit.assertThrows[{.qetl.venue.venues_of `crypto};::;"venue: crypto is open*";"crypto has no list to subtract from"]};

/ --- the datasets consult it, and only it -------------------------------------------------

.qt.qetl.venue.test_crypto_scores_no_fill_or_book_of_another_market:{[tc]
    k:.qpipe.transform.crypto_markout_outputs.keep;
    b:`$"BTC-USDT";
    t:2026.09.17D10:00:00;
    e:([] source_time:3#t; venue:`XNAS`fx`binance_spot; sym:3#b; side:3#1; size:3#1f; price:3#62000f; fee:3#0f; fee_ccy:3#`USDT; fill_id:`x`y`z; clock:3#`venue);
    .qunit.assertEquals[k e;001b;"only the crypto exchange's fill"];
    .qunit.assertEquals[k ([] sym:3#b; source:`XNAS`fx`binance_spot);001b;"and only its book"]};

.qt.qetl.venue.test_no_crypto_declaration_names_the_equity_dataset:{[tc]
    files:`$system "ls src/etl/transforms/crypto_*.q src/etl/sources/hdb_crypto_*.q src/etl/workers/hdb_crypto_*.q src/etl/streaming/crypto_*.q";
    hits:{[f] $[any (read0 hsym f) like "*eq_markout_outputs*"; enlist f; ()]} each files;
    .qunit.assertEquals[raze hits;();"a crypto file naming the equity dataset to learn what is not crypto"]};

.qt.qetl.venue.test_a_crypto_process_does_not_load_the_equity_files:{[tc]
    plan:.qetl.load.procs `crypto_markout1;
    .qunit.assertEquals[plan where plan like "*eq_*";();"crypto_markout1's load list"];
    plan:.qetl.load.procs `hdb_crypto_markout_backfill1;
    .qunit.assertEquals[plan where plan like "*eq_*";();"hdb_crypto_markout_backfill1's load list"]};
