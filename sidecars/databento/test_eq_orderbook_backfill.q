/ test_eq_orderbook_backfill.q - the eq_orderbook quality gate
/ (.qpipe.job.eq_orderbook_backfill.quality_check) on the Databento MBP-10
/ fixture: an emptied side or a book clear is not a crossed book or a bad
/ price (#1148), while a truly crossed book and a truly non-positive price
/ still fail.


.qt.qpipe.job.eq_orderbook_backfill.book_of:{[f] .qetl.transform.apply[`eq_orderbook;enlist[`batch]!enlist f]}

.qt.qpipe.job.eq_orderbook_backfill.test_the_fixture_passes_the_quality_check:{[t]
    r:.qpipe.job.eq_orderbook_backfill.quality_check .qt.qpipe.job.eq_orderbook_backfill.book_of .qpipe.source.databento_mbp10.fixture[];
    .qunit.assertEquals[count r;0;"the fixture is a clean book"]};

.qt.qpipe.job.eq_orderbook_backfill.test_an_emptied_ask_side_is_not_a_crossed_book:{[t]
    f:update ask_px_00:0n, ask_sz_00:0N from .qpipe.source.databento_mbp10.fixture[] where i=3;
    r:.qpipe.job.eq_orderbook_backfill.quality_check .qt.qpipe.job.eq_orderbook_backfill.book_of f;
    .qunit.assertEquals[count r;0;"a null ask orders below every bid but is absent, not wrong"]};

.qt.qpipe.job.eq_orderbook_backfill.test_a_book_clear_is_not_a_failure:{[t]
    lv:.qpipe.source.databento_mbp10.level_fields;
    r:update action:`R, side:`N, price:0n, size:0 from 1#.qpipe.source.databento_mbp10.fixture[];
    r:![r;();0b;lv!{$[x like "*_px_*";0n;0]} each string lv];
    .qunit.assertEquals[count .qpipe.job.eq_orderbook_backfill.quality_check .qt.qpipe.job.eq_orderbook_backfill.book_of r;0;"all levels empty and a null price"]};

.qt.qpipe.job.eq_orderbook_backfill.test_a_crossed_book_still_fails:{[t]
    f:update bid_px_00:ask_px_00+1f from .qpipe.source.databento_mbp10.fixture[] where i=3;
    r:.qpipe.job.eq_orderbook_backfill.quality_check .qt.qpipe.job.eq_orderbook_backfill.book_of f;
    .qunit.assertEquals[r`check;enlist `crossed_top_of_book;"a bid above the ask is a mis-fold"]};

.qt.qpipe.job.eq_orderbook_backfill.test_a_nonpositive_price_still_fails:{[t]
    f:update price:0f from .qpipe.source.databento_mbp10.fixture[] where i=3;
    r:.qpipe.job.eq_orderbook_backfill.quality_check .qt.qpipe.job.eq_orderbook_backfill.book_of f;
    .qunit.assertEquals[r`check;enlist `nonpositive_price;"a printed price of zero is bad data"]};

/ A refill over live-written rows replaces them (#1282). The live job drops
/ `time` (the plant stamps receipt time, here ts_event + 3ms); the backfill
/ writes time = ts_event. Keyed on `time` nothing matched and upsert doubled
/ every event, so the key is the venue's ts_event.
.qt.qpipe.job.eq_orderbook_backfill.test_a_refill_over_live_rows_replaces_them:{[t]
    cfg:.qetl.job.bounded.def `eq_orderbook_backfill;
    live:update time:ts_event+0D00:00:00.003 from .qt.qpipe.job.eq_orderbook_backfill.book_of[.qpipe.source.databento_mbp10.fixture[]];
    refill:.qt.qpipe.job.eq_orderbook_backfill.book_of[.qpipe.source.databento_mbp10.fixture[]];
    opts:`on_conflict`row_key`time_column`range_from`range_to!(`upsert;cfg`target_key;cfg`window_column;2026.02.25D14:30:00.000000000;2026.02.25D14:40:00.000000000);
    `eq_orderbook_refilltest set 0#live;
    `eq_orderbook_refilltest insert live;
    .qetl.io.write_keyed[.qetl.io.memory;`eq_orderbook_refilltest;refill;opts];
    r:value `eq_orderbook_refilltest;
    delete eq_orderbook_refilltest from `.;
    .qunit.assertEquals[count r;count live;"four events in, four rows out"];
    .qunit.assertEquals[(exec max n from select n:count i by sym,ts_event,sequence from r);1;"no event twice"]};
