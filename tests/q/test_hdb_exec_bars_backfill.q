/ test_hdb_exec_bars_backfill.q - the bounded exec-bars worker's window_column (#1143).
/ .
/ The output's `time` is the bar's END (bar_start+width). Without a
/ window_column, `replace cleared a window on `time`, so the window
/ [11:00;12:00) deleted the 10:59 bar (time 11:00) the previous window wrote.
/ The fixture is swapped for one fill in each of two windows.

\d .ebbtest

spec_for:{[version;from_ts;to_ts] `source_version`range_from`range_to!(version;from_ts;to_ts)}

/ One fill at 10:59:30 (window 10:00) and one at 11:00:30 (window 11:00).
two_window_fills:{[]
    ([] source_time:2026.10.09D10:59:30 2026.10.09D11:00:30; sym:`EURUSD`EURUSD;
        size:1e6 2e6; price:1.10 1.11)}

setUp_fresh:{[]
    .testutil.reset_coverage_ledger[];
    .qetl.cfg.reset[];
    .qetl.cfg.set_layers[(enlist `on_conflict)!enlist "replace";()!();()!()];
    setenv[`UQF_DRY_RUN;""];
    setenv[`UQF_SOURCE_CRED_HDB_EXECUTIONS;""];
    .qetl.job.bounded.state.release_lock `hdb_exec_bars_backfill;
    .qetl.job.bounded.state.clear_checkpoint `hdb_exec_bars_backfill;
    `exec_bar set 0#.qetl.plant.shape `exec_bar;
    `.ebbtest.orig set (.qetl.source.def `hdb_executions)`fixture;
    .testutil.set_field[`.qetl.source.sources;`hdb_executions;`fixture;.ebbtest.two_window_fills];
    }

tearDown_release:{[]
    .testutil.set_field[`.qetl.source.sources;`hdb_executions;`fixture;.ebbtest.orig];
    .qpipe.job.hdb_exec_bars_backfill.cleanup[];
    }

test_the_window_column_is_bar_start_not_the_bar_end:{[t]
    .qunit.assertEquals[(.qetl.job.bounded.def `hdb_exec_bars_backfill)`window_column;`bar_start;
        "windows are cleared on the bar's start"]};

test_replace_across_windows_keeps_the_previous_windows_last_bar:{[t]
    .qpipe.job.hdb_exec_bars_backfill.init[.ebbtest.spec_for[`v1;2026.10.09D10:00;2026.10.09D12:00]];
    r:.qpipe.job.hdb_exec_bars_backfill.run[];
    b:`bar_start xasc select from get `exec_bar;
    .qunit.assertEquals[(r`state;r`windows_completed);(`completed;2);"both windows ran"];
    .qunit.assertEquals[b`bar_start;2026.10.09D10:59 2026.10.09D11:00;"the 10:59 bar survives the 11:00 window's replace"];
    .qunit.assertEquals[b`volume;1e6 2e6;"each bar from its own fill"]};

\d .
