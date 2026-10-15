/ test_hdb_crypto_book_backfill.q - the bounded book-snapshot worker's window_column (#1143, #1222).
/ .
/ The output's `time` is the snapshot's END (bar_start+width). Without a
/ window_column, `replace cleared a window on `time`, so the window [11:00;12:00)
/ deleted the 10:59:59 snapshot (time 11:00:00) the previous window wrote.
/ The fixture is swapped for one update just inside each of two windows.


.qt.qpipe.job.hdb_crypto_book_backfill.spec_for:{[version;from_ts;to_ts] `source_version`range_from`range_to!(version;from_ts;to_ts)}

/ One update at 10:59:59.7 (window 10:00) and one at 11:00:00.1 (window 11:00).
.qt.qpipe.job.hdb_crypto_book_backfill.two_window_books:{[]
    ([] source_time:2026.10.09D10:59:59.700 2026.10.09D11:00:00.100; venue:`a`a; sym:`BTC`BTC;
        bid_prices:(62020 62019f;62030 62029f); bid_sizes:(1 1f;2 2f);
        ask_prices:(62021 62022f;62031 62032f); ask_sizes:(7 7f;8 8f))}

/ The lock and checkpoint files go under the status directory; with none
/ configured they went to /status and died, unless an earlier suite had set
/ one (#1325).
.qt.qpipe.job.hdb_crypto_book_backfill.beforeNamespace_isolate:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    system"mkdir -p build/test-status";
    }

.qt.qpipe.job.hdb_crypto_book_backfill.setUp_fresh:{[]
    .qt.support.reset_coverage_ledger[];
    .qetl.cfg.reset[];
    .qetl.cfg.set_layers[(enlist `on_conflict)!enlist "replace";()!();()!()];
    setenv[`UQF_DRY_RUN;""];
    setenv[`UQF_SOURCE_CRED_HDB_CRYPTO_BOOK;""];
    .qetl.job.bounded.state.release_lock `hdb_crypto_book_backfill;
    .qetl.job.bounded.state.clear_checkpoint `hdb_crypto_book_backfill;
    `crypto_book_1s set 0#.qetl.plant.shape `crypto_book_1s;
    `.qt.qpipe.job.hdb_crypto_book_backfill.orig set (.qetl.source.def `hdb_crypto_book)`fixture;
    .qt.support.set_field[`.qetl.source.sources;`hdb_crypto_book;`fixture;.qt.qpipe.job.hdb_crypto_book_backfill.two_window_books];
    }

.qt.qpipe.job.hdb_crypto_book_backfill.tearDown_release:{[]
    .qt.support.set_field[`.qetl.source.sources;`hdb_crypto_book;`fixture;.qt.qpipe.job.hdb_crypto_book_backfill.orig];
    .qpipe.job.hdb_crypto_book_backfill.cleanup[];
    }

/ A nested column is general in meta, so the source declares it " ": the worker's
/ contract builder must accept that (it used to reject " " as a type, so no
/ source with a vector column could be bounded).
.qt.qpipe.job.hdb_crypto_book_backfill.test_a_source_with_vector_columns_declares_them_general_and_defines:{[t]
    src:.qetl.source.def `hdb_crypto_book;
    .qunit.assertEquals[(src`types) 3 4 5 6;"    ";"the ladders are general columns"];
    .qunit.assertTrue[`hdb_crypto_book_backfill in .qetl.job.bounded.defined[];"and the worker over it is defined"]};

.qt.qpipe.job.hdb_crypto_book_backfill.test_the_window_column_is_bar_start_not_the_snapshot_end:{[t]
    d:.qetl.job.bounded.def `hdb_crypto_book_backfill;
    .qunit.assertEquals[d`window_column;`bar_start;"windows are cleared on the snapshot's start"];
    .qunit.assertEquals[d`target_key;`venue`sym`bar_start;"the live job's key"]};

.qt.qpipe.job.hdb_crypto_book_backfill.test_replace_across_windows_keeps_the_previous_windows_last_snapshot:{[t]
    .qpipe.job.hdb_crypto_book_backfill.init[.qt.qpipe.job.hdb_crypto_book_backfill.spec_for[`v1;2026.10.09D10:00;2026.10.09D12:00]];
    r:.qpipe.job.hdb_crypto_book_backfill.run[];
    b:`bar_start xasc select from get `crypto_book_1s;
    .qunit.assertEquals[(r`state;r`windows_completed);(`completed;2);"both windows ran"];
    .qunit.assertEquals[b`bar_start;2026.10.09D10:59:59 2026.10.09D11:00:00;"the 10:59:59 snapshot survives the 11:00 window's replace"];
    .qunit.assertEquals[first each b`bid_prices;62020 62030f;"each from its own update"]};

.qt.qpipe.job.hdb_crypto_book_backfill.test_a_rerun_replaces_the_live_jobs_rows_under_the_shared_key:{[t]
    / the live job already wrote 10:59:59 with a different book
    `crypto_book_1s insert (`time`venue`sym`bar_start`source_time`bid_prices`bid_sizes`ask_prices`ask_sizes)!(
        2026.10.09D11:00:00;`a;`BTC;2026.10.09D10:59:59;2026.10.09D10:59:59.500;1 1f;1 1f;2 2f;1 1f);
    .qpipe.job.hdb_crypto_book_backfill.init[.qt.qpipe.job.hdb_crypto_book_backfill.spec_for[`v1;2026.10.09D10:00;2026.10.09D12:00]];
    .qpipe.job.hdb_crypto_book_backfill.run[];
    b:select from get `crypto_book_1s where bar_start=2026.10.09D10:59:59;
    .qunit.assertEquals[(count b;first b`source_time);(1;2026.10.09D10:59:59.700);"replaced, not duplicated"]};

.qt.qpipe.job.hdb_crypto_book_backfill.test_the_quality_check_refuses_ragged_ladders:{[t]
    ok:.qpipe.job.hdb_crypto_book_backfill.snapshots .qt.qpipe.job.hdb_crypto_book_backfill.two_window_books[];
    .qunit.assertEquals[count .qpipe.job.hdb_crypto_book_backfill.quality_check ok;0;"a well-formed batch passes"];
    bad:update bid_sizes:enlist 1f from ok where bar_start=min bar_start;
    r:.qpipe.job.hdb_crypto_book_backfill.quality_check bad;
    .qunit.assertEquals[first r`check;`book_shape;"a ladder shorter than its sizes fails"]};

.qt.qpipe.job.hdb_crypto_book_backfill.test_an_unaligned_range_is_refused_before_a_straddling_bar_is_built_twice:{[t]
    msg:"init: hdb_crypto_book_backfill builds 0D00:00:01.000000000 bars - --from and --to must be multiples of it*";
    .qunit.assertThrows[{.qpipe.job.hdb_crypto_book_backfill.init x};
        .qt.qpipe.job.hdb_crypto_book_backfill.spec_for[`v1;2026.10.09D10:00:00.500;2026.10.09D12:00];msg;"an unaligned --from is refused"];
    .qunit.assertThrows[{.qpipe.job.hdb_crypto_book_backfill.init x};
        .qt.qpipe.job.hdb_crypto_book_backfill.spec_for[`v1;2026.10.09D10:00;2026.10.09D12:00:00.500];msg;"an unaligned --to is refused"]};

.qt.qpipe.job.hdb_crypto_book_backfill.test_the_declared_grain_is_the_live_bar_width:{[t]
    .qunit.assertEquals[(.qetl.job.bounded.def `hdb_crypto_book_backfill)`align;.qpipe.transform.crypto_book_1s.width;
        "the worker cuts to the live job's bar width"]};
