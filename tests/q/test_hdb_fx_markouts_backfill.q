/ test_hdb_fx_markouts_backfill.q - the hdb_fx_markouts source and its bounded worker
/ (.qpipe.source.hdb_fx_markouts, .qpipe.job.hdb_fx_markouts_backfill, #1395).
/ .
/ No HDB is needed. The live path is driven through a stand-in handle that evaluates
/ each query against in-memory `executions` and `market_data`, so what is proven is
/ everything on this side of the wire: which two queries a window sends, over which
/ bounds, and which rows come back. The scoring is the worker's transform, so it is
/ asserted through that - the same call a window makes. The live-versus-refill claim
/ itself (a refilled row is the row the live job publishes) is test_fx_markout.q's.


.qt.qpipe.job.hdb_fx_markouts_backfill.spec_for:{[version;from_ts;to_ts] `source_version`range_from`range_to!(version;from_ts;to_ts)}

/ Two inputs, scored as a window scores them.
.qt.qpipe.job.hdb_fx_markouts_backfill.scored:{[inputs] .qetl.transform.apply[`hdb_fx_markouts_long;inputs]}

/ The fixture's day: its executions are at 10:00:00-10:00:21.
.qt.qpipe.job.hdb_fx_markouts_backfill.day:{[] (2026.09.17D00:00:00.000000000;2026.09.18D00:00:00.000000000)}

.qt.qpipe.job.hdb_fx_markouts_backfill.beforeNamespace_isolate:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    system"mkdir -p build/test-status";
    }

.qt.qpipe.job.hdb_fx_markouts_backfill.setUp_fresh:{[]
    .qt.support.reset_coverage_ledger[];
    .qetl.cfg.reset[];
    .qetl.cfg.set_layers[()!();()!();()!()];
    setenv[`UQF_DRY_RUN;""];
    setenv[`UQF_SOURCE_CRED_HDB_FX_MARKOUTS;""];
    .qetl.job.bounded.state.release_lock `hdb_fx_markouts_backfill;
    .qetl.job.bounded.state.clear_checkpoint `hdb_fx_markouts_backfill;
    `fx_markout set 0#.qetl.plant.shape `fx_markout;
    }

.qt.qpipe.job.hdb_fx_markouts_backfill.tearDown_release:{[] .qpipe.job.hdb_fx_markouts_backfill.cleanup[];}

/ --- the declaration ---------------------------------------------------------------

.qt.qpipe.job.hdb_fx_markouts_backfill.test_the_worker_is_keyed_and_windowed_so_a_refill_replaces_the_live_rows:{[t]
    d:.qetl.job.bounded.def `hdb_fx_markouts_backfill;
    .qunit.assertEquals[d`target_key;`venue`fill_id`horizon`kind`step;"the identity of a component"];
    .qunit.assertEquals[(d`window_column;d`on_conflict;d`dataset);(`source_time;`replace;`fx_markout);"replaced on the execution's time, into the live job's table"]};

/ --- scoring ------------------------------------------------------------------------

.qt.qpipe.job.hdb_fx_markouts_backfill.test_the_scoring_is_a_verified_transform:{[t]
    .qunit.assertTrue[all exec passed from .qetl.transform.verify `hdb_fx_markouts_long;"the hand-worked example and the empty case"]};

.qt.qpipe.job.hdb_fx_markouts_backfill.test_the_twin_scores_what_the_live_transform_scores_and_stamps_a_time:{[t]
    f:.qpipe.source.hdb_fx_markouts.fixture[];
    r:.qt.qpipe.job.hdb_fx_markouts_backfill.scored f;
    live:.qetl.transform.apply[`fx_markout_score_long;f];
    .qunit.assertEquals[delete time from r;live;"the same rows"];
    .qunit.assertEquals[r`time;(r`source_time)+0D00:00:10;"time is source_time plus the furthest horizon"]};

.qt.qpipe.job.hdb_fx_markouts_backfill.test_the_fixtures_unroutable_fill_is_excluded_and_the_rest_scored:{[t]
    r:.qt.qpipe.job.hdb_fx_markouts_backfill.scored .qpipe.source.hdb_fx_markouts.fixture[];
    .qunit.assertEquals[asc exec distinct fill_id from r;`x1`y1;"the crypto fill cannot be routed"];
    .qunit.assertEquals[count r;36;"x1: four horizons of four components; y1: four of five"]};

.qt.qpipe.job.hdb_fx_markouts_backfill.test_no_executions_score_to_nothing_whatever_the_books_hold:{[t]
    inputs:@[.qpipe.source.hdb_fx_markouts.fixture[];`executions;0#];
    .qunit.assertEquals[.qt.qpipe.job.hdb_fx_markouts_backfill.scored inputs;0#.qetl.plant.shape `fx_markout;"the primary input owns the window"]};

/ --- the quality gate and facts -----------------------------------------------------------

.qt.qpipe.job.hdb_fx_markouts_backfill.test_the_gate_passes_the_fixture_and_names_what_it_refuses:{[t]
    r:.qt.qpipe.job.hdb_fx_markouts_backfill.scored .qpipe.source.hdb_fx_markouts.fixture[];
    .qunit.assertEquals[count .qpipe.job.hdb_fx_markouts_backfill.quality_check r;0;"clean"];
    bad:update pips:0w from r where i=0;
    f:.qpipe.job.hdb_fx_markouts_backfill.quality_check bad;
    .qunit.assertEquals[(f`check;f`status);(enlist `infinite_pips;enlist `fail);"an infinite markout is refused by name"];
    unrec:update status:`unreconciled from r where kind=`residual;
    .qunit.assertEquals[(.qpipe.job.hdb_fx_markouts_backfill.quality_check unrec)`check;enlist `unreconciled_residual;"and so is a leg allocation that does not add up"]};

.qt.qpipe.job.hdb_fx_markouts_backfill.test_the_facts_count_executions_horizons_and_the_unpriced:{[t]
    r:.qt.qpipe.job.hdb_fx_markouts_backfill.scored .qpipe.source.hdb_fx_markouts.fixture[];
    .qunit.assertEquals[.qpipe.job.hdb_fx_markouts_backfill.facts r;`executions`horizons`unpriced!(2;4;0);"two executions at four horizons, all priced"];
    .qunit.assertEquals[.qpipe.job.hdb_fx_markouts_backfill.facts 0#r;(enlist `span)!enlist "empty window";"and an empty window says so"]};

/ --- a run ----------------------------------------------------------------------------------

.qt.qpipe.job.hdb_fx_markouts_backfill.test_a_run_writes_every_component_into_fx_markout:{[t]
    .qpipe.job.hdb_fx_markouts_backfill.init[.qt.qpipe.job.hdb_fx_markouts_backfill.spec_for[`v1;.qt.qpipe.job.hdb_fx_markouts_backfill.day[]0;.qt.qpipe.job.hdb_fx_markouts_backfill.day[]1]];
    r:.qpipe.job.hdb_fx_markouts_backfill.run[];
    .qunit.assertEquals[(r`state;count get `fx_markout);(`completed;36);"every component of every priced execution lands"];
    .qunit.assertEquals[cols get `fx_markout;cols .qetl.plant.shape `fx_markout;"in fx_markout's own columns, as the live job writes them"]};

.qt.qpipe.job.hdb_fx_markouts_backfill.test_a_restatement_replaces_rather_than_duplicates:{[t]
    .qpipe.job.hdb_fx_markouts_backfill.init[.qt.qpipe.job.hdb_fx_markouts_backfill.spec_for[`v1;.qt.qpipe.job.hdb_fx_markouts_backfill.day[]0;.qt.qpipe.job.hdb_fx_markouts_backfill.day[]1]];
    .qpipe.job.hdb_fx_markouts_backfill.run[];
    .qpipe.job.hdb_fx_markouts_backfill.cleanup[];
    .qpipe.job.hdb_fx_markouts_backfill.init[.qt.qpipe.job.hdb_fx_markouts_backfill.spec_for[`v2;.qt.qpipe.job.hdb_fx_markouts_backfill.day[]0;.qt.qpipe.job.hdb_fx_markouts_backfill.day[]1]];
    .qpipe.job.hdb_fx_markouts_backfill.run[];
    .qunit.assertEquals[count get `fx_markout;36;"still one row per component"]};

/ --- the live path, against a stand-in HDB ----------------------------------------------------

.qt.qpipe.job.hdb_fx_markouts_backfill.sent:()

/ A handle that runs each query here, recording the bounds it was asked for.
.qt.qpipe.job.hdb_fx_markouts_backfill.hdb:{[msg] .qt.qpipe.job.hdb_fx_markouts_backfill.sent,:enlist 1_msg; value msg}

.qt.qpipe.job.hdb_fx_markouts_backfill.with_hdb:{[f]
    had:{[nm] $[nm in key `.; (1b;get nm); (0b;::)]} each `executions`market_data;
    `executions set update date:`date$source_time from .qpipe.source.hdb_fx_markouts.raw_executions[];
    `market_data set update date:`date$source_time from .qpipe.source.hdb_fx_markouts.raw_market_data[];
    `.qt.qpipe.job.hdb_fx_markouts_backfill.sent set ();
    r:@[f;::;{[e] `threw,e}];
    {[nm;k] $[first k; nm set last k; ![`.;();0b;enlist nm]]}'[`executions`market_data;had];
    r}

.qt.qpipe.job.hdb_fx_markouts_backfill.test_a_window_sends_two_queries_reading_books_past_its_end_and_before_its_start:{[t]
    w:2026.09.17D10:00:00 2026.09.17D11:00:00;
    `.qt.qpipe.job.hdb_fx_markouts_backfill.w set w;
    r:.qt.qpipe.job.hdb_fx_markouts_backfill.with_hdb {.qpipe.source.hdb_fx_markouts.query[.qt.qpipe.job.hdb_fx_markouts_backfill.hdb;.qt.qpipe.job.hdb_fx_markouts_backfill.w 0;.qt.qpipe.job.hdb_fx_markouts_backfill.w 1]};
    .qunit.assertEquals[count .qt.qpipe.job.hdb_fx_markouts_backfill.sent;2;"one query for the executions, one for the books"];
    .qunit.assertEquals[.qt.qpipe.job.hdb_fx_markouts_backfill.sent 0;w;"executions over the window itself"];
    .qunit.assertEquals[.qt.qpipe.job.hdb_fx_markouts_backfill.sent 1;((w 0)-0D00:00:01;(w 1)+0D00:00:10);"books from the window's start less the job's lookback, to its end plus the furthest horizon"];
    .qunit.assertEquals[key r;`executions`market_data;"both inputs, raw - the scoring is the transform's"];
    .qunit.assertEquals[count .qt.qpipe.job.hdb_fx_markouts_backfill.scored r;36;"the window's two routable executions, scored"]};

.qt.qpipe.job.hdb_fx_markouts_backfill.test_the_live_path_scores_exactly_as_the_fixture_does:{[t]
    r:.qt.qpipe.job.hdb_fx_markouts_backfill.with_hdb {.qpipe.source.hdb_fx_markouts.query[.qt.qpipe.job.hdb_fx_markouts_backfill.hdb;(.qt.qpipe.job.hdb_fx_markouts_backfill.day[])0;(.qt.qpipe.job.hdb_fx_markouts_backfill.day[])1]};
    .qunit.assertTrue[.qetl.source.validate[`hdb_fx_markouts;r];"the live inputs satisfy both contracts"];
    .qunit.assertEquals[.qt.qpipe.job.hdb_fx_markouts_backfill.scored r;.qt.qpipe.job.hdb_fx_markouts_backfill.scored .qpipe.source.hdb_fx_markouts.fixture[];"live and fixture agree"]};

.qt.qpipe.job.hdb_fx_markouts_backfill.test_only_the_books_the_routes_need_are_read:{[t]
    / USDPLN needs EURUSD and EURPLN; a GBPJPY book exists and is not read
    extra:{
        `market_data set (update date:`date$source_time from .qpipe.source.hdb_fx_markouts.raw_market_data[]),
            update date:`date$source_time, sym:`GBPJPY from 2#.qpipe.source.hdb_fx_markouts.raw_market_data[];
        `executions set update date:`date$source_time from .qpipe.source.hdb_fx_markouts.raw_executions[];
        .qpipe.source.hdb_fx_markouts.query[.qt.qpipe.job.hdb_fx_markouts_backfill.hdb;2026.09.17D10:00:00;2026.09.17D11:00:00]};
    `.qt.qpipe.job.hdb_fx_markouts_backfill.extra set extra;
    r:.qt.qpipe.job.hdb_fx_markouts_backfill.with_hdb {.qt.qpipe.job.hdb_fx_markouts_backfill.extra[]};
    .qunit.assertFalse[`GBPJPY in distinct (r`market_data)`sym;"a pair no execution's route uses"]};

.qt.qpipe.job.hdb_fx_markouts_backfill.test_an_execution_early_in_a_window_is_priced_by_the_book_before_it:{[t]
    / the EURUSD book live at 10:00:03 was set at 09:50:00 - before the window
    f:{
        `executions set ([] date:enlist 2026.09.17; source_time:enlist 2026.09.17D10:00:03; venue:enlist `a; sym:enlist `EURUSD;
            side:enlist 1; size:enlist 1e6; price:enlist 1.1; fill_id:enlist `e1);
        `market_data set ([] date:2026.09.17 2026.09.17; source_time:2026.09.17D09:50:00 2026.09.17D11:30:00;
            sym:2#`EURUSD; source:2#`a; bid_prices:(enlist 1.1;enlist 1.2); bid_sizes:2#enlist enlist 1e6;
            ask_prices:(enlist 1.1002;enlist 1.2002); ask_sizes:2#enlist enlist 1e6);
        .qpipe.source.hdb_fx_markouts.query[.qt.qpipe.job.hdb_fx_markouts_backfill.hdb;2026.09.17D10:00:00;2026.09.17D11:00:00]};
    `.qt.qpipe.job.hdb_fx_markouts_backfill.early set f;
    r:.qt.qpipe.job.hdb_fx_markouts_backfill.with_hdb {.qt.qpipe.job.hdb_fx_markouts_backfill.early[]};
    s:select from .qt.qpipe.job.hdb_fx_markouts_backfill.scored r where kind=`execution;
    .qunit.assertTrue[all 1e-9>=abs (s`price_t1)-1.1001;"priced against the 09:50 mid (1.1001), not null"]};

.qt.qpipe.job.hdb_fx_markouts_backfill.test_the_book_before_a_window_is_bounded_by_the_lookback:{[t]
    f:{
        `executions set ([] date:enlist 2026.09.17; source_time:enlist 2026.09.17D10:00:03; venue:enlist `a; sym:enlist `EURUSD;
            side:enlist 1; size:enlist 1e6; price:enlist 1.1; fill_id:enlist `e1);
        `market_data set ([] date:enlist 2026.09.01; source_time:enlist 2026.09.01D10:00; sym:enlist `EURUSD; source:enlist `a;
            bid_prices:enlist enlist 1.1; bid_sizes:enlist enlist 1e6; ask_prices:enlist enlist 1.1002; ask_sizes:enlist enlist 1e6);
        .qpipe.source.hdb_fx_markouts.query[.qt.qpipe.job.hdb_fx_markouts_backfill.hdb;2026.09.17D10:00:00;2026.09.17D11:00:00]};
    `.qt.qpipe.job.hdb_fx_markouts_backfill.stale set f;
    r:.qt.qpipe.job.hdb_fx_markouts_backfill.with_hdb {.qt.qpipe.job.hdb_fx_markouts_backfill.stale[]};
    .qunit.assertEquals[count r`market_data;0;"sixteen days back is past the 7-day lookback"]};

.qt.qpipe.job.hdb_fx_markouts_backfill.test_a_window_without_executions_does_not_read_books:{[t]
    r:.qt.qpipe.job.hdb_fx_markouts_backfill.with_hdb {.qpipe.source.hdb_fx_markouts.query[.qt.qpipe.job.hdb_fx_markouts_backfill.hdb;2026.09.17D12:00:00;2026.09.17D13:00:00]};
    .qunit.assertEquals[(count .qt.qpipe.job.hdb_fx_markouts_backfill.sent;count r`executions;count r`market_data);(1;0;0);"no executions, no book query"];
    .qunit.assertTrue[.qetl.source.validate[`hdb_fx_markouts;r];"and both inputs still in their contracts' shape"]};
