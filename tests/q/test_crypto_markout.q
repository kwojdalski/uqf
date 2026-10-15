/ test_crypto_markout.q - the crypto_markout streaming job (.qt.qpipe.job.crypto_markout).
/ .
/ Every expected number is worked by hand from the books below: a markout
/ that reads the wrong venue or the wrong side of the book still produces a
/ plausible bps figure, so only an independently known price catches it.


.qt.qpipe.job.crypto_markout.t0:2026.09.17D10:00:00.000000000
.qt.qpipe.job.crypto_markout.btc:`$"BTC-USDT"

/ crypto_trades rows, as the plant carries them.
.qt.qpipe.job.crypto_markout.fill:{[side;px;tm;id]
    ([] time:enlist tm; sym:enlist .qt.qpipe.job.crypto_markout.btc; venue:enlist `a; side:enlist side;
        trade_price:enlist px; size:enlist 0.5; fee:enlist 0f; fee_currency:enlist `USDT;
        exchange_fill_id:enlist id)}

/ crypto_book rows: one top-of-book level a side per venue.
.qt.qpipe.job.crypto_markout.book:{[venue;bid;ask;tm]
    ([] time:enlist tm; source_time:enlist tm; venue:enlist venue; sym:enlist .qt.qpipe.job.crypto_markout.btc;
        bid_prices:enlist enlist bid; bid_sizes:enlist enlist 1f;
        ask_prices:enlist enlist ask; ask_sizes:enlist enlist 1f)}

.qt.qpipe.job.crypto_markout.reset:{[]
    .qetl.job.stream.reset `crypto_markout;
    `.qt.qpipe.job.crypto_markout.sent set ();
    .qetl.job.stream.wire[`crypto_markout;{[t;x] .qt.qpipe.job.crypto_markout.sent,:enlist (t;x)}];
    }

.qt.qpipe.job.crypto_markout.sent:()

/ Drive the job: books from venues a and b, one fill, then the timer once
/ the 10s horizon has passed. Returns the published rows.
.qt.qpipe.job.crypto_markout.drive:{[side;books]
    .qt.qpipe.job.crypto_markout.reset[];
    .qpipe.job.crypto_markout.on_batch[`crypto_book;books];
    .qpipe.job.crypto_markout.on_batch[`crypto_trades;.qt.qpipe.job.crypto_markout.fill[side;62000f;.qt.qpipe.job.crypto_markout.t0;`f1]];
    .qpipe.job.crypto_markout.score_ready .qt.qpipe.job.crypto_markout.t0+0D00:00:10;
    last last .qt.qpipe.job.crypto_markout.sent}

.qt.qpipe.job.crypto_markout.two_venues:{[] (.qt.qpipe.job.crypto_markout.book[`a;62000f;62010f;.qt.qpipe.job.crypto_markout.t0]),.qt.qpipe.job.crypto_markout.book[`b;62004f;62008f;.qt.qpipe.job.crypto_markout.t0]}

.qt.qpipe.job.crypto_markout.test_the_reference_is_the_best_mid_across_venues:{[t]
    / best bid 62004 (b), best ask 62008 (b): mid 62006, 6 above the fill
    r:.qt.qpipe.job.crypto_markout.drive[1;.qt.qpipe.job.crypto_markout.two_venues[]];
    .qunit.assertEquals[first r`ref_price;62006f;"the highest bid and lowest ask of any venue"];
    .qunit.assertTrue[null last r`ref_price;"and by the 10s horizon both books are past max_age"];
    .qt.support.assertApprox[first r`markout_bps;10000*6%62000;1e-9;"a buy the market rose 6 above is +0.97 bps"]};

.qt.qpipe.job.crypto_markout.test_a_zero_size_top_does_not_set_the_best_price:{[t]
    / b's 62004/62008 top is pulled (size 0): a alone counts, mid 62005 (#1261)
    pulled:([] time:enlist .qt.qpipe.job.crypto_markout.t0; source_time:enlist .qt.qpipe.job.crypto_markout.t0; venue:enlist `b; sym:enlist .qt.qpipe.job.crypto_markout.btc;
        bid_prices:enlist enlist 62004f; bid_sizes:enlist enlist 0f;
        ask_prices:enlist enlist 62008f; ask_sizes:enlist enlist 0f);
    r:.qt.qpipe.job.crypto_markout.drive[1;(.qt.qpipe.job.crypto_markout.book[`a;62000f;62010f;.qt.qpipe.job.crypto_markout.t0]),pulled];
    .qunit.assertEquals[first r`ref_price;62005f;"the zero-size level is absent, not a price"];
    .qt.support.assertApprox[first r`markout_bps;10000*5%62000;1e-9;"0.806 bps, not 0.968"]};

.qt.qpipe.job.crypto_markout.test_a_sell_into_a_rising_market_is_negative:{[t]
    r:.qt.qpipe.job.crypto_markout.drive[-1;.qt.qpipe.job.crypto_markout.two_venues[]];
    .qunit.assertTrue[all 0>r`markout_bps;"same move, the other side: against the fill"]};

.qt.qpipe.job.crypto_markout.test_a_venue_older_than_max_age_does_not_set_the_best_price:{[t]
    / b's better book is 6s old at the 1s horizon - past max_age, so a alone counts
    stale:.qt.qpipe.job.crypto_markout.book[`b;62004f;62008f;.qt.qpipe.job.crypto_markout.t0-0D00:00:05];
    r:.qt.qpipe.job.crypto_markout.drive[1;(.qt.qpipe.job.crypto_markout.book[`a;62000f;62010f;.qt.qpipe.job.crypto_markout.t0]),stale];
    .qunit.assertEquals[first r`ref_price;62005f;"venue a's own mid, (62000+62010)%2"]};

.qt.qpipe.job.crypto_markout.test_a_fill_with_no_live_book_keeps_a_null_row:{[t]
    r:.qt.qpipe.job.crypto_markout.drive[1;.qt.qpipe.job.crypto_markout.book[`a;62000f;62010f;.qt.qpipe.job.crypto_markout.t0-0D00:01:00]];
    .qunit.assertEquals[count r;2;"one row per horizon, not dropped"];
    .qunit.assertTrue[all null r`markout_bps;"no venue was live, so no markout"]};

.qt.qpipe.job.crypto_markout.test_a_one_sided_market_has_no_mid:{[t]
    oneside:([] time:enlist .qt.qpipe.job.crypto_markout.t0; source_time:enlist .qt.qpipe.job.crypto_markout.t0; venue:enlist `a; sym:enlist .qt.qpipe.job.crypto_markout.btc;
        bid_prices:enlist enlist 62000f; bid_sizes:enlist enlist 1f;
        ask_prices:enlist `float$(); ask_sizes:enlist `float$());
    r:.qt.qpipe.job.crypto_markout.drive[1;oneside];
    .qunit.assertTrue[all null r`ref_price;"a bid with no ask anywhere is not a price"]};

.qt.qpipe.job.crypto_markout.test_a_fill_waits_for_its_longest_horizon:{[t]
    .qt.qpipe.job.crypto_markout.reset[];
    .qpipe.job.crypto_markout.on_batch[`crypto_book;.qt.qpipe.job.crypto_markout.two_venues[]];
    .qpipe.job.crypto_markout.on_batch[`crypto_trades;.qt.qpipe.job.crypto_markout.fill[1;62000f;.qt.qpipe.job.crypto_markout.t0;`f1]];
    .qpipe.job.crypto_markout.score_ready .qt.qpipe.job.crypto_markout.t0+0D00:00:09;
    .qunit.assertEquals[count .qt.qpipe.job.crypto_markout.sent;0;"the 10s horizon has not passed"];
    .qunit.assertEquals[count .qpipe.job.crypto_markout.pending;1;"so the fill stays queued"]};

.qt.qpipe.job.crypto_markout.test_scoring_drains_the_queue_and_prunes_old_books:{[t]
    .qt.qpipe.job.crypto_markout.reset[];
    .qpipe.job.crypto_markout.on_batch[`crypto_book;(.qt.qpipe.job.crypto_markout.book[`a;62000f;62010f;.qt.qpipe.job.crypto_markout.t0-0D00:01:00]),.qt.qpipe.job.crypto_markout.two_venues[]];
    .qpipe.job.crypto_markout.on_batch[`crypto_trades;.qt.qpipe.job.crypto_markout.fill[1;62000f;.qt.qpipe.job.crypto_markout.t0;`f1]];
    .qpipe.job.crypto_markout.score_ready .qt.qpipe.job.crypto_markout.t0+0D00:00:10;
    .qunit.assertEquals[count .qpipe.job.crypto_markout.pending;0;"the scored fill leaves the queue"];
    .qunit.assertEquals[exec min time from .qpipe.job.crypto_markout.history;.qt.qpipe.job.crypto_markout.t0;
        "a book no pending fill could use is dropped"]};

.qt.qpipe.job.crypto_markout.test_only_real_fills_are_buffered:{[t]
    / crypto_sim_fills is never subscribed, and a stray table is ignored
    .qt.qpipe.job.crypto_markout.reset[];
    .qpipe.job.crypto_markout.on_batch[`crypto_sim_fills;.qt.qpipe.job.crypto_markout.fill[1;62000f;.qt.qpipe.job.crypto_markout.t0;`f1]];
    .qunit.assertEquals[count .qpipe.job.crypto_markout.pending;0;"simulated execution never reaches the real markouts"];
    .qunit.assertEquals[.qetl.job.stream.def[`crypto_markout]`subscribe_to;`crypto_trades`crypto_book;
        "and the job subscribes to real fills only"]};

/ What test_job_output_contracts.q drives crypto_markout with, so every table
/ it publishes is held to its plant table by name, order and type.
.qt.qpipe.job.crypto_markout.contract_driver:{[]
    / clear the buffers, not the wiring: the contract suite wired publish
    .qetl.job.stream.reset `crypto_markout;
    .qpipe.job.crypto_markout.on_batch[`crypto_book;.qt.qpipe.job.crypto_markout.two_venues[]];
    .qpipe.job.crypto_markout.on_batch[`crypto_trades;.qt.qpipe.job.crypto_markout.fill[1;62000f;.qt.qpipe.job.crypto_markout.t0;`f1]];
    .qpipe.job.crypto_markout.score_ready .qt.qpipe.job.crypto_markout.t0+0D00:00:10;
    }
