/ test_hdb_demo_markouts_backfill.q - the hdb_demo_markouts source and its bounded worker
/ (.qpipe.source.hdb_demo_markouts, .qpipe.job.hdb_demo_markouts_backfill).
/ .
/ No HDB is needed. The live path is driven through a stand-in handle that
/ evaluates each query against in-memory `trades` and `quote` tables, so what
/ is proven is everything on this side of the wire: which two queries a
/ window sends, over which bounds, and how their rows are scored. The full
/ lifecycle (init, run, idle second run, dry run) is test_every_worker_runs.q's,
/ which drives this worker with every other.

\d .mbftest

spec_for:{[version;from_ts;to_ts] `source_version`range_from`range_to!(version;from_ts;to_ts)}

/ The fixture's day: its fills are at 10:00:00-10:00:07 and 23:59:59.
day:{[] (2026.09.17D00:00:00.000000000;2026.09.18D00:00:00.000000000)}

beforeNamespace_isolate:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    system"mkdir -p build/test-status";
    }

setUp_fresh:{[]
    .testutil.reset_coverage_ledger[];
    .qetl.cfg.reset[];
    .qetl.cfg.set_layers[()!();()!();()!()];
    setenv[`UQF_DRY_RUN;""];
    setenv[`UQF_SOURCE_CRED_HDB_MARKOUTS;""];
    .qetl.job.bounded.state.release_lock `hdb_demo_markouts_backfill;
    .qetl.job.bounded.state.clear_checkpoint `hdb_demo_markouts_backfill;
    `demo_execution_quality set 0#.qetl.plant.shape `demo_execution_quality;
    }

tearDown_release:{[] .qpipe.job.hdb_demo_markouts_backfill.cleanup[];}

/ --- scoring --------------------------------------------------------------

test_every_fill_is_scored_at_every_live_horizon:{[t]
    f:.qpipe.source.hdb_demo_markouts.fixture[];
    .qunit.assertEquals[(count f;asc distinct f`horizon);(4*count .qpipe.job.demo_markout.horizons;asc .qpipe.job.demo_markout.horizons);
        "four fills, each at the live job's horizons"]};

test_the_horizons_are_the_live_jobs_not_a_copy:{[t]
    / Change the live job's horizons and the backfill follows - the two
    / cannot drift apart.
    keep:.qpipe.job.demo_markout.horizons;
    .qpipe.job.demo_markout.horizons:enlist 0D00:00:01;
    n:count .qpipe.source.hdb_demo_markouts.fixture[];
    .qpipe.job.demo_markout.horizons:keep;
    .qunit.assertEquals[n;4;"one horizon, one row per fill"]};

test_a_markout_is_the_live_functions_answer:{[t]
    / EURUSD bought at 1.1001; the 1s mid is (1.1004+1.1006)/2 = 1.1005, so +4 pips.
    f:.qpipe.source.hdb_demo_markouts.fixture[];
    r:first select from f where sym=`EURUSD, trade_time=2026.09.17D10:00:00, horizon=0D00:00:01;
    .qunit.assertEquals[(r`ref_price;r`markout_pips);(1.1005;4f);"a buy marked against the later mid"]};

test_a_fill_with_no_later_quote_keeps_a_null_markout:{[t]
    / As the live job does: the gap shows instead of the fill vanishing.
    f:.qpipe.source.hdb_demo_markouts.fixture[];
    .qunit.assertEquals[exec count i from f where sym=`GBPUSD, null markout_pips;2;
        "both GBPUSD horizons are null, and present"]};

/ --- a run ----------------------------------------------------------------

test_a_run_writes_every_markout_into_execution_quality:{[t]
    .qpipe.job.hdb_demo_markouts_backfill.init[.mbftest.spec_for[`v1;.mbftest.day[]0;.mbftest.day[]1]];
    r:.qpipe.job.hdb_demo_markouts_backfill.run[];
    / get, not the bare name: inside .mbftest that would mean .mbftest.demo_execution_quality.
    .qunit.assertEquals[(r`state;count get `demo_execution_quality);(`completed;8);
        "all eight markouts land, including the fill whose horizons cross midnight"];
    .qunit.assertEquals[cols get `demo_execution_quality;cols .qetl.plant.shape `demo_execution_quality;
        "in demo_execution_quality's own columns, as the live job writes them"]};

test_a_restatement_replaces_rather_than_duplicates:{[t]
    / The target key is (sym;trade_time;horizon): a second version over the
    / same window - or the live job's rows for the same fills - is replaced.
    .qpipe.job.hdb_demo_markouts_backfill.init[.mbftest.spec_for[`v1;.mbftest.day[]0;.mbftest.day[]1]];
    .qpipe.job.hdb_demo_markouts_backfill.run[];
    .qpipe.job.hdb_demo_markouts_backfill.cleanup[];
    .qpipe.job.hdb_demo_markouts_backfill.init[.mbftest.spec_for[`v2;.mbftest.day[]0;.mbftest.day[]1]];
    .qpipe.job.hdb_demo_markouts_backfill.run[];
    .qunit.assertEquals[count get `demo_execution_quality;8;"still one row per fill per horizon"]};

/ --- the live path, against a stand-in HDB ---------------------------------

/ A handle that runs each query here, against `trades` and `quote` shaped as
/ the HDB holds them (date-partitioned, so they carry a `date`), recording
/ the bounds it was asked for. The real tables of these names, if this
/ process has them, are put back after.
hdb:{[msg] .mbftest.sent,:enlist 1_msg; value msg}

with_hdb:{[f]
    had:{[nm] $[nm in key `.; (1b;get nm); (0b;::)]} each `trades`quote;
    `trades set update date:`date$time from .qpipe.source.hdb_demo_markouts.raw_fills[];
    `quote set update date:`date$time from .qpipe.source.hdb_demo_markouts.raw_quotes[];
    `.mbftest.sent set ();
    r:@[f;::;{[e] `threw,e}];
    {[nm;k] $[first k; nm set last k; ![`.;();0b;enlist nm]]}'[`trades`quote;had];
    r}

test_a_window_sends_two_queries_reading_quotes_past_its_end:{[t]
    w:2026.09.17D10:00:00 2026.09.17D11:00:00;
    / The window through a global: `with_hdb {..}[w]` would run the query
    / before with_hdb has put the stand-in tables in place.
    `.mbftest.w set w;
    r:.mbftest.with_hdb {.qpipe.source.hdb_demo_markouts.query[.mbftest.hdb;.mbftest.w 0;.mbftest.w 1]};
    .qunit.assertEquals[count .mbftest.sent;2;"one query for the fills, one for the quotes"];
    .qunit.assertEquals[.mbftest.sent 0;w;"fills over the window itself"];
    .qunit.assertEquals[.mbftest.sent 1;(w 0;(w 1)+max .qpipe.job.demo_markout.horizons);
        "quotes on to the window's end plus the longest horizon"];
    .qunit.assertEquals[count r;6;"the window's three fills, scored at both horizons"]};

test_the_live_path_scores_exactly_as_the_fixture_does:{[t]
    / The fixture is the same scoring over the same rows, so the live path
    / over the fixture's day must give the same table.
    r:.mbftest.with_hdb {.qpipe.source.hdb_demo_markouts.query[.mbftest.hdb;.mbftest.day[]0;.mbftest.day[]1]};
    .qunit.assertEquals[`trade_time xasc r;.qpipe.source.hdb_demo_markouts.fixture[];"live and fixture agree"]};

test_a_fill_early_in_a_window_is_priced_by_the_quote_before_it:{[t]
    / The quote live at 10:00:03 was set at 09:59:50 - before the window.
    / Reading quotes only from the window's start priced this fill null, and
    / the target key then replaced the live job's correct row with that null.
    `.mbftest.early set {
        `trades set ([] date:enlist 2026.09.17; time:enlist 2026.09.17D10:00:03; sym:enlist `EURUSD;
            side:enlist 1; trade_price:enlist 1.1; pip_factor:enlist 10000);
        `quote set ([] date:2026.09.17 2026.09.17; time:2026.09.17D09:59:50 2026.09.17D11:30:00;
            sym:`EURUSD`EURUSD; bid:1.1 1.2; ask:1.1002 1.2002);
        .qpipe.source.hdb_demo_markouts.query[.mbftest.hdb;2026.09.17D10:00;2026.09.17D11:00]};
    r:.mbftest.with_hdb {.mbftest.early[]};
    .qunit.assertEquals[exec markout_pips from r;1 1f;
        "both horizons priced against the 09:59:50 mid (1.1001), not null"]};

test_the_quote_before_a_window_is_bounded_by_the_lookback:{[t]
    / A pair quoted only long before the window stays unpriced, as the live
    / job would leave it after a restart - the lookback is a bound, not all history.
    `.mbftest.stale set {
        `trades set ([] date:enlist 2026.09.17; time:enlist 2026.09.17D10:00:03; sym:enlist `EURUSD;
            side:enlist 1; trade_price:enlist 1.1; pip_factor:enlist 10000);
        `quote set ([] date:enlist 2026.09.01; time:enlist 2026.09.01D10:00; sym:enlist `EURUSD;
            bid:enlist 1.1; ask:enlist 1.1002);
        .qpipe.source.hdb_demo_markouts.query[.mbftest.hdb;2026.09.17D10:00;2026.09.17D11:00]};
    r:.mbftest.with_hdb {.mbftest.stale[]};
    .qunit.assertTrue[all null exec markout_pips from r;"sixteen days back is past the 7-day lookback"]};

test_a_window_without_fills_does_not_read_quotes:{[t]
    r:.mbftest.with_hdb {.qpipe.source.hdb_demo_markouts.query[.mbftest.hdb;2026.09.17D12:00:00;2026.09.17D13:00:00]};
    .qunit.assertEquals[(count .mbftest.sent;count r;cols r);(1;0;cols .qpipe.source.hdb_demo_markouts.fixture[]);
        "no fills, no quote query - and an empty table of the right shape"]};

\d .
