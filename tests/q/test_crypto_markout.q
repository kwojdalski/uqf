/ test_crypto_markout.q - the crypto_markout streaming job (.crypto_markouttest).
/ .
/ Every expected number is worked by hand from the books below: a markout
/ that reads the wrong venue or the wrong side of the book still produces a
/ plausible bps figure, so only an independently known price catches it.

\d .crypto_markouttest

t0:2026.09.17D10:00:00.000000000
btc:`$"BTC-USDT"

/ crypto_trades rows, as the plant carries them.
fill:{[side;px;tm;id]
    ([] time:enlist tm; sym:enlist .crypto_markouttest.btc; venue:enlist `a; side:enlist side;
        trade_price:enlist px; size:enlist 0.5; fee:enlist 0f; fee_currency:enlist `USDT;
        exchange_fill_id:enlist id)}

/ crypto_book rows: one top-of-book level a side per venue.
book:{[venue;bid;ask;tm]
    ([] time:enlist tm; source_time:enlist tm; venue:enlist venue; sym:enlist .crypto_markouttest.btc;
        bid_prices:enlist enlist bid; bid_sizes:enlist enlist 1f;
        ask_prices:enlist enlist ask; ask_sizes:enlist enlist 1f)}

reset:{[]
    `.qpipe.job.crypto_markout.pending set 0#.qpipe.job.crypto_markout.pending;
    `.qpipe.job.crypto_markout.history set 0#.qpipe.job.crypto_markout.history;
    `.crypto_markouttest.sent set ();
    .qetl.job.stream.wire[`crypto_markout;{[t;x] .crypto_markouttest.sent,:enlist (t;x)}];
    }

sent:()

/ Drive the job: books from venues a and b, one fill, then the timer once
/ the 10s horizon has passed. Returns the published rows.
drive:{[side;books]
    reset[];
    .qpipe.job.crypto_markout.on_batch[`crypto_book;books];
    .qpipe.job.crypto_markout.on_batch[`crypto_trades;fill[side;62000f;t0;`f1]];
    .qpipe.job.crypto_markout.score_ready t0+0D00:00:10;
    last last sent}

two_venues:{[] (book[`a;62000f;62010f;t0]),book[`b;62004f;62008f;t0]}

test_the_reference_is_the_best_mid_across_venues:{[t]
    / best bid 62004 (b), best ask 62008 (b): mid 62006, 6 above the fill
    r:drive[1;two_venues[]];
    .qunit.assertEquals[first r`ref_price;62006f;"the highest bid and lowest ask of any venue"];
    .qunit.assertTrue[null last r`ref_price;"and by the 10s horizon both books are past max_age"];
    .testutil.assertApprox[first r`markout_bps;10000*6%62000;1e-9;"a buy the market rose 6 above is +0.97 bps"]};

test_a_sell_into_a_rising_market_is_negative:{[t]
    r:drive[-1;two_venues[]];
    .qunit.assertTrue[all 0>r`markout_bps;"same move, the other side: against the fill"]};

test_a_venue_older_than_max_age_does_not_set_the_best_price:{[t]
    / b's better book is 6s old at the 1s horizon - past max_age, so a alone counts
    stale:book[`b;62004f;62008f;t0-0D00:00:05];
    r:drive[1;(book[`a;62000f;62010f;t0]),stale];
    .qunit.assertEquals[first r`ref_price;62005f;"venue a's own mid, (62000+62010)%2"]};

test_a_fill_with_no_live_book_keeps_a_null_row:{[t]
    r:drive[1;book[`a;62000f;62010f;t0-0D00:01:00]];
    .qunit.assertEquals[count r;2;"one row per horizon, not dropped"];
    .qunit.assertTrue[all null r`markout_bps;"no venue was live, so no markout"]};

test_a_one_sided_market_has_no_mid:{[t]
    oneside:([] time:enlist t0; source_time:enlist t0; venue:enlist `a; sym:enlist btc;
        bid_prices:enlist enlist 62000f; bid_sizes:enlist enlist 1f;
        ask_prices:enlist `float$(); ask_sizes:enlist `float$());
    r:drive[1;oneside];
    .qunit.assertTrue[all null r`ref_price;"a bid with no ask anywhere is not a price"]};

test_a_fill_waits_for_its_longest_horizon:{[t]
    reset[];
    .qpipe.job.crypto_markout.on_batch[`crypto_book;two_venues[]];
    .qpipe.job.crypto_markout.on_batch[`crypto_trades;fill[1;62000f;t0;`f1]];
    .qpipe.job.crypto_markout.score_ready t0+0D00:00:09;
    .qunit.assertEquals[count sent;0;"the 10s horizon has not passed"];
    .qunit.assertEquals[count .qpipe.job.crypto_markout.pending;1;"so the fill stays queued"]};

test_scoring_drains_the_queue_and_prunes_old_books:{[t]
    reset[];
    .qpipe.job.crypto_markout.on_batch[`crypto_book;(book[`a;62000f;62010f;t0-0D00:01:00]),two_venues[]];
    .qpipe.job.crypto_markout.on_batch[`crypto_trades;fill[1;62000f;t0;`f1]];
    .qpipe.job.crypto_markout.score_ready t0+0D00:00:10;
    .qunit.assertEquals[count .qpipe.job.crypto_markout.pending;0;"the scored fill leaves the queue"];
    .qunit.assertEquals[exec min time from .qpipe.job.crypto_markout.history;t0;
        "a book no pending fill could use is dropped"]};

test_only_real_fills_are_buffered:{[t]
    / crypto_sim_fills is never subscribed, and a stray table is ignored
    reset[];
    .qpipe.job.crypto_markout.on_batch[`crypto_sim_fills;fill[1;62000f;t0;`f1]];
    .qunit.assertEquals[count .qpipe.job.crypto_markout.pending;0;"simulated execution never reaches the real markouts"];
    .qunit.assertEquals[.qetl.job.stream.def[`crypto_markout]`subscribe_to;`crypto_trades`crypto_book;
        "and the job subscribes to real fills only"]};

/ What test_job_output_contracts.q drives crypto_markout with, so every table
/ it publishes is held to its plant table by name, order and type.
contract_driver:{[]
    / clear the buffers, not the wiring: the contract suite wired publish
    `.qpipe.job.crypto_markout.pending set 0#.qpipe.job.crypto_markout.pending;
    `.qpipe.job.crypto_markout.history set 0#.qpipe.job.crypto_markout.history;
    .qpipe.job.crypto_markout.on_batch[`crypto_book;.crypto_markouttest.two_venues[]];
    .qpipe.job.crypto_markout.on_batch[`crypto_trades;.crypto_markouttest.fill[1;62000f;.crypto_markouttest.t0;`f1]];
    .qpipe.job.crypto_markout.score_ready .crypto_markouttest.t0+0D00:00:10;
    }

\d .
