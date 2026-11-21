/ test_hdb_exec_bars_backfill.q - the bounded exec-bars worker's window_column (#1143).
/ .
/ The output's `time` is the bar's END (bar_start+width). Without a
/ window_column, `replace cleared a window on `time`, so the window
/ [11:00;12:00) deleted the 10:59 bar (time 11:00) the previous window wrote.
/ The fixture is swapped for one fill in each of two windows.


/ Its own status directory: the runs here write status and outcome files, and
/ without this the suite borrowed the one an earlier suite had set - so run
/ alone (a selected subset, #1690) it wrote to "/status" and failed.
.qt.qpipe.job.hdb_exec_bars_backfill.beforeNamespace_status_dir:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    system"mkdir -p build/test-status";
    }

.qt.qpipe.job.hdb_exec_bars_backfill.spec_for:{[version;from_ts;to_ts] `source_version`range_from`range_to!(version;from_ts;to_ts)}

/ One fill at 10:59:30 (window 10:00) and one at 11:00:30 (window 11:00).
.qt.qpipe.job.hdb_exec_bars_backfill.two_window_fills:{[]
    ([] source_time:2026.10.09D10:59:30 2026.10.09D11:00:30; sym:`EURUSD`EURUSD;
        size:1e6 2e6; price:1.10 1.11)}

.qt.qpipe.job.hdb_exec_bars_backfill.setUp_fresh:{[]
    .qt.support.reset_coverage_ledger[];
    .qetl.cfg.reset[];
    .qetl.cfg.set_layers[(enlist `on_conflict)!enlist "replace";()!();()!()];
    setenv[`UQF_DRY_RUN;""];
    setenv[`UQF_SOURCE_CRED_HDB_EXECUTIONS;""];
    .qetl.job.bounded.state.release_lock `hdb_exec_bars_backfill;
    .qetl.job.bounded.state.clear_checkpoint `hdb_exec_bars_backfill;
    `exec_bar set 0#.qetl.plant.shape `exec_bar;
    `.qt.qpipe.job.hdb_exec_bars_backfill.orig set (.qetl.source.def `hdb_executions)`fixture;
    .qt.support.set_field[`.qetl.source.sources;`hdb_executions;`fixture;.qt.qpipe.job.hdb_exec_bars_backfill.two_window_fills];
    }

.qt.qpipe.job.hdb_exec_bars_backfill.tearDown_release:{[]
    .qt.support.set_field[`.qetl.source.sources;`hdb_executions;`fixture;.qt.qpipe.job.hdb_exec_bars_backfill.orig];
    .qpipe.job.hdb_exec_bars_backfill.cleanup[];
    }

.qt.qpipe.job.hdb_exec_bars_backfill.test_the_window_column_is_bar_start_not_the_bar_end:{[t]
    .qunit.assertEquals[(.qetl.job.bounded.def `hdb_exec_bars_backfill)`window_column;`bar_start;
        "windows are cleared on the bar's start"]};

.qt.qpipe.job.hdb_exec_bars_backfill.test_replace_across_windows_keeps_the_previous_windows_last_bar:{[t]
    .qpipe.job.hdb_exec_bars_backfill.init[.qt.qpipe.job.hdb_exec_bars_backfill.spec_for[`v1;2026.10.09D10:00;2026.10.09D12:00]];
    r:.qpipe.job.hdb_exec_bars_backfill.run[];
    b:`bar_start xasc select from get `exec_bar;
    .qunit.assertEquals[(r`state;r`windows_completed);(`completed;2);"both windows ran"];
    .qunit.assertEquals[b`bar_start;2026.10.09D10:59 2026.10.09D11:00;"the 10:59 bar survives the 11:00 window's replace"];
    .qunit.assertEquals[b`volume;1e6 2e6;"each bar from its own fill"]};

.qt.qpipe.job.hdb_exec_bars_backfill.test_an_unaligned_range_is_refused_before_a_straddling_bar_is_built_twice:{[t]
    msg:"init: hdb_exec_bars_backfill builds 0D00:01:00.000000000 bars - --from and --to must be multiples of it*";
    .qunit.assertThrows[{.qpipe.job.hdb_exec_bars_backfill.init x};
        .qt.qpipe.job.hdb_exec_bars_backfill.spec_for[`v1;2026.10.09D10:00:30;2026.10.09D12:00];msg;"an unaligned --from is refused"];
    .qunit.assertThrows[{.qpipe.job.hdb_exec_bars_backfill.init x};
        .qt.qpipe.job.hdb_exec_bars_backfill.spec_for[`v1;2026.10.09D10:00;2026.10.09D12:00:30];msg;"an unaligned --to is refused"]};

.qt.qpipe.job.hdb_exec_bars_backfill.test_the_declared_grain_is_the_live_bar_width:{[t]
    .qunit.assertEquals[(.qetl.job.bounded.def `hdb_exec_bars_backfill)`align;.qpipe.transform.exec_bars.width;
        "the worker cuts to the live job's bar width"]};

/ #2881: the quality check is the worker's gate. A bar with no volume, or a
/ high below its low, is refused as bar_range, each on its own; a sound bar,
/ and a one-fill bar whose high equals its low, pass.
.qt.qpipe.job.hdb_exec_bars_backfill.sound_bar:{[]
    .qpipe.job.hdb_exec_bars_backfill.bars .qt.qpipe.job.hdb_exec_bars_backfill.two_window_fills[]}

.qt.qpipe.job.hdb_exec_bars_backfill.test_the_check_passes_sound_bars:{[t]
    .qunit.assertEquals[count .qpipe.job.hdb_exec_bars_backfill.quality_check .qt.qpipe.job.hdb_exec_bars_backfill.sound_bar[];0;
        "two one-fill bars, each with volume and high = low: no failure"]};

.qt.qpipe.job.hdb_exec_bars_backfill.test_the_check_refuses_a_bar_with_no_volume:{[t]
    b:update volume:0 1e6 from .qt.qpipe.job.hdb_exec_bars_backfill.sound_bar[];
    .qunit.assertEquals[.qpipe.job.hdb_exec_bars_backfill.quality_check b;
        ([] check:enlist `bar_range; status:enlist `fail; detail:enlist "1 bar(s) with no volume or a high below the low");
        "a zero-volume bar is refused, and counted"]};

.qt.qpipe.job.hdb_exec_bars_backfill.test_the_check_refuses_a_bar_whose_high_is_below_its_low:{[t]
    b:update high:low-0.01 from .qt.qpipe.job.hdb_exec_bars_backfill.sound_bar[];
    .qunit.assertEquals[.qpipe.job.hdb_exec_bars_backfill.quality_check b;
        ([] check:enlist `bar_range; status:enlist `fail; detail:enlist "2 bar(s) with no volume or a high below the low");
        "a high below the low is refused, with volume present"]};
