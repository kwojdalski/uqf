/ test_eq_orderbook_backfill.q - the eq_orderbook quality gate
/ (.qpipe.job.eq_orderbook_backfill.quality_check) on the Databento MBP-10
/ fixture: an emptied side or a book clear is not a crossed book or a bad
/ price (#1148), while a truly crossed book and a truly non-positive price
/ still fail.

\d .eq_orderbook_backfilltest

book_of:{[f] .qetl.transform.apply[`eq_orderbook;enlist[`batch]!enlist f]}

test_the_fixture_passes_the_quality_check:{[t]
    r:.qpipe.job.eq_orderbook_backfill.quality_check book_of .qpipe.source.databento_mbp10.fixture[];
    .qunit.assertEquals[count r;0;"the fixture is a clean book"]};

test_an_emptied_ask_side_is_not_a_crossed_book:{[t]
    f:update ask_px_00:0n, ask_sz_00:0N from .qpipe.source.databento_mbp10.fixture[] where i=3;
    r:.qpipe.job.eq_orderbook_backfill.quality_check book_of f;
    .qunit.assertEquals[count r;0;"a null ask orders below every bid but is absent, not wrong"]};

test_a_book_clear_is_not_a_failure:{[t]
    lv:.qpipe.source.databento_mbp10.level_fields;
    r:update action:`R, side:`N, price:0n, size:0 from 1#.qpipe.source.databento_mbp10.fixture[];
    r:![r;();0b;lv!{$[x like "*_px_*";0n;0]} each string lv];
    .qunit.assertEquals[count .qpipe.job.eq_orderbook_backfill.quality_check book_of r;0;"all levels empty and a null price"]};

test_a_crossed_book_still_fails:{[t]
    f:update bid_px_00:ask_px_00+1f from .qpipe.source.databento_mbp10.fixture[] where i=3;
    r:.qpipe.job.eq_orderbook_backfill.quality_check book_of f;
    .qunit.assertEquals[r`check;enlist `crossed_top_of_book;"a bid above the ask is a mis-fold"]};

test_a_nonpositive_price_still_fails:{[t]
    f:update price:0f from .qpipe.source.databento_mbp10.fixture[] where i=3;
    r:.qpipe.job.eq_orderbook_backfill.quality_check book_of f;
    .qunit.assertEquals[r`check;enlist `nonpositive_price;"a printed price of zero is bad data"]};

\d .
