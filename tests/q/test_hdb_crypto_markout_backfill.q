/ test_hdb_crypto_markout_backfill.q - the hdb_crypto_markouts source and its bounded worker
/ (.qpipe.source.hdb_crypto_markouts, .qpipe.job.hdb_crypto_markout_backfill, #1936).
/ .
/ No HDB is needed. The live path is driven through a stand-in handle that evaluates each
/ query against in-memory `executions` and `market_data`, so what is proven is everything on
/ this side of the wire: which two queries a window sends, over which bounds, and which rows
/ come back. The scoring is the worker's transform, so it is asserted through that - the same
/ call a window makes. The live-versus-refill claim itself (a refilled row is the row the live
/ job publishes) is test_crypto_markout.q's.
/ .
/ The fixture's day (src/etl/sources/hdb_crypto_markouts.q): a BTC-USDT buy on the venue clock
/ (f1), an ETH-USDT sell with a base-currency fee (f2), a BTC-USDT buy on the RECEIPT clock
/ (f3, which the venue-clock books cannot price) and a EURUSD fill the core cannot route (f4).


.qt.qpipe.job.hdb_crypto_markout_backfill.spec_for:{[version;from_ts;to_ts] `source_version`range_from`range_to!(version;from_ts;to_ts)}

/ Two inputs, scored as a window scores them.
.qt.qpipe.job.hdb_crypto_markout_backfill.scored:{[inputs] .qetl.transform.apply[`hdb_crypto_markout_long;inputs]}

/ The fixture's day: its executions are at 10:00:00-10:00:22.
.qt.qpipe.job.hdb_crypto_markout_backfill.day:{[] (2026.09.17D00:00:00.000000000;2026.09.18D00:00:00.000000000)}

.qt.qpipe.job.hdb_crypto_markout_backfill.beforeNamespace_isolate:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    system"mkdir -p build/test-status";
    .qt.support.reset_snapshots[];
    }

.qt.qpipe.job.hdb_crypto_markout_backfill.setUp_fresh:{[]
    .qt.support.reset_coverage_ledger[];
    .qetl.cfg.reset[];
    .qetl.cfg.set_layers[()!();()!();()!()];
    setenv[`UQF_DRY_RUN;""];
    setenv[`UQF_SOURCE_CRED_HDB_CRYPTO_MARKOUTS;""];
    .qetl.job.bounded.state.release_lock `hdb_crypto_markout_backfill;
    .qetl.job.bounded.state.clear_checkpoint `hdb_crypto_markout_backfill;
    `crypto_markout set 0#.qetl.plant.shape `crypto_markout;
    .qt.support.reset_snapshots[];
    }

.qt.qpipe.job.hdb_crypto_markout_backfill.tearDown_release:{[] .qpipe.job.hdb_crypto_markout_backfill.cleanup[];}

/ --- the declaration ---------------------------------------------------------------

.qt.qpipe.job.hdb_crypto_markout_backfill.test_the_worker_is_keyed_and_windowed_so_a_refill_replaces_the_live_rows:{[t]
    d:.qetl.job.bounded.def `hdb_crypto_markout_backfill;
    .qunit.assertEquals[d`target_key;`venue`fill_id`horizon`kind`step;"the identity of a component"];
    .qunit.assertEquals[(d`window_column;d`on_conflict;d`dataset);(`source_time;`replace;`crypto_markout);"replaced on the execution's time, into the live job's table"]};

/ --- scoring ------------------------------------------------------------------------

.qt.qpipe.job.hdb_crypto_markout_backfill.test_the_scoring_is_a_verified_transform:{[t]
    .qunit.assertTrue[all exec passed from .qetl.transform.verify `hdb_crypto_markout_long;"the hand-worked example and the empty case"]};

.qt.qpipe.job.hdb_crypto_markout_backfill.test_the_twin_scores_what_the_live_transform_scores_and_keeps_the_cores_time:{[t]
    f:.qpipe.source.hdb_crypto_markouts.fixture[];
    r:.qt.qpipe.job.hdb_crypto_markout_backfill.scored[f];
    live:.qetl.transform.apply[`crypto_markout_score;f];
    .qunit.assertEquals[delete time from r;live;"the same rows"];
    .qunit.assertEquals[r`time;(r`source_time)+0D00:00:10;"time is source_time plus the furthest horizon"]};

.qt.qpipe.job.hdb_crypto_markout_backfill.test_the_fixtures_rows_are_the_hand_worked_ones:{[t]
    r:.qt.qpipe.job.hdb_crypto_markout_backfill.scored[.qpipe.source.hdb_crypto_markouts.fixture[]];
    .qunit.assertEquals[asc exec distinct fill_id from r;`f1`f2`f3;"the EURUSD fill cannot be routed by the crypto domain"];
    .qunit.assertEquals[count r;12;"three executions, two horizons, a total and an execution component each"];
    t1:select from r where kind=`total;
    .qt.support.assertApprox[exec markout_bps from t1 where fill_id=`f1,horizon=0D00:00:01;10000*6%62000;1e-9;"f1 at 1s: mid 62006 against 62000"];
    .qt.support.assertApprox[exec markout_bps from t1 where fill_id=`f1,horizon=0D00:00:10;10000*15%62000;1e-9;"f1 at 10s: b has gone quiet, a's mid is 62015"];
    .qt.support.assertApprox[exec markout_bps from t1 where fill_id=`f2,horizon=0D00:00:01;10000*2%3000;1e-9;"f2 is a SELL at 3000 into a mid of 2998: it gains"];
    .qt.support.assertApprox[exec markout_bps from t1 where fill_id=`f2,horizon=0D00:00:10;10000*9%3000;1e-9;"and 9 at 10s, 30 bps"];
    .qt.support.assertApprox[exec first fee_bps from t1 where fill_id=`f2;10f;1e-9;"0.002 ETH at 3000 is 6 USDT on a 6000 notional: 10 bps"];
    .qt.support.assertApprox[exec markout_net_bps from t1 where fill_id=`f1,horizon=0D00:00:10;(10000*15%62000)-10;1e-9;"net of the 15.5 USDT fee"]};

.qt.qpipe.job.hdb_crypto_markout_backfill.test_a_fill_on_the_receipt_clock_is_refused_not_scored_against_venue_books:{[t]
    r:.qt.qpipe.job.hdb_crypto_markout_backfill.scored[.qpipe.source.hdb_crypto_markouts.fixture[]];
    f3:select from r where fill_id=`f3;
    .qunit.assertEquals[distinct f3`status;enlist `clock_mismatch;"every row of f3 says why it is unpriced"];
    .qunit.assertTrue[all null f3`markout_bps;"and carries no bps from a clock it does not share"]};

.qt.qpipe.job.hdb_crypto_markout_backfill.test_no_executions_score_to_nothing_whatever_the_books_hold:{[t]
    inputs:@[.qpipe.source.hdb_crypto_markouts.fixture[];`executions;0#];
    .qunit.assertEquals[.qt.qpipe.job.hdb_crypto_markout_backfill.scored[inputs];0#.qetl.plant.shape `crypto_markout;"the primary input owns the window"]};

/ --- the quality gate and facts -----------------------------------------------------------

.qt.qpipe.job.hdb_crypto_markout_backfill.test_the_gate_passes_the_fixture_and_names_what_it_refuses:{[t]
    r:.qt.qpipe.job.hdb_crypto_markout_backfill.scored[.qpipe.source.hdb_crypto_markouts.fixture[]];
    f0:.qpipe.job.hdb_crypto_markout_backfill.quality_check r;
    .qunit.assertEquals[(f0`check;f0`status);(enlist `config_unpinned;enlist `fail);"rows naming a configuration nobody recorded are refused"];
    .qpipe.transform.crypto_markout_outputs.pin[];
    .qunit.assertEquals[count .qpipe.job.hdb_crypto_markout_backfill.quality_check r;0;"clean"];
    bad:update markout_bps:0w, markout_net_bps:0w from r where i=0;
    f:.qpipe.job.hdb_crypto_markout_backfill.quality_check bad;
    .qunit.assertEquals[(f`check;f`status);(enlist `infinite_bps;enlist `fail);"an infinite markout is refused by name"];
    off:update markout_net_bps:markout_net_bps+1 from r where kind=`total, not null markout_net_bps;
    .qunit.assertEquals[(.qpipe.job.hdb_crypto_markout_backfill.quality_check off)`check;enlist `net_is_not_gross_less_fee;"and so is a net figure that is not the gross one less the fee"]};

.qt.qpipe.job.hdb_crypto_markout_backfill.test_the_facts_count_executions_horizons_the_unpriced_and_the_wrong_clock:{[t]
    r:.qt.qpipe.job.hdb_crypto_markout_backfill.scored[.qpipe.source.hdb_crypto_markouts.fixture[]];
    .qunit.assertEquals[.qpipe.job.hdb_crypto_markout_backfill.facts r;`executions`horizons`unpriced`clock_mismatch`config_id!(3;2;0;2;enlist .qpipe.transform.crypto_markout_outputs.config_id);
        "three executions at two horizons; f3's two totals are on the wrong clock, none merely unpriced; one configuration"];
    .qunit.assertEquals[.qpipe.job.hdb_crypto_markout_backfill.facts 0#r;(enlist `span)!enlist "empty window";"and an empty window says so"]};

/ --- a run ----------------------------------------------------------------------------------

.qt.qpipe.job.hdb_crypto_markout_backfill.test_a_run_writes_every_component_into_crypto_markout:{[t]
    .qpipe.job.hdb_crypto_markout_backfill.init[.qt.qpipe.job.hdb_crypto_markout_backfill.spec_for[`v1;.qt.qpipe.job.hdb_crypto_markout_backfill.day[]0;.qt.qpipe.job.hdb_crypto_markout_backfill.day[]1]];
    r:.qpipe.job.hdb_crypto_markout_backfill.run[];
    .qunit.assertEquals[(r`state;count get `crypto_markout);(`completed;12);"every component of every routable execution lands"];
    .qunit.assertEquals[cols get `crypto_markout;cols .qetl.plant.shape `crypto_markout;"in crypto_markout's own columns, as the live job writes them"]};

.qt.qpipe.job.hdb_crypto_markout_backfill.test_a_restatement_replaces_rather_than_duplicates:{[t]
    .qpipe.job.hdb_crypto_markout_backfill.init[.qt.qpipe.job.hdb_crypto_markout_backfill.spec_for[`v1;.qt.qpipe.job.hdb_crypto_markout_backfill.day[]0;.qt.qpipe.job.hdb_crypto_markout_backfill.day[]1]];
    .qpipe.job.hdb_crypto_markout_backfill.run[];
    .qpipe.job.hdb_crypto_markout_backfill.cleanup[];
    .qpipe.job.hdb_crypto_markout_backfill.init[.qt.qpipe.job.hdb_crypto_markout_backfill.spec_for[`v2;.qt.qpipe.job.hdb_crypto_markout_backfill.day[]0;.qt.qpipe.job.hdb_crypto_markout_backfill.day[]1]];
    .qpipe.job.hdb_crypto_markout_backfill.run[];
    .qunit.assertEquals[count get `crypto_markout;12;"still one row per component"]};

/ --- the live path, against a stand-in HDB ----------------------------------------------------

.qt.qpipe.job.hdb_crypto_markout_backfill.sent:()

/ A handle that runs each query here, recording the bounds it was asked for.
.qt.qpipe.job.hdb_crypto_markout_backfill.hdb:{[msg] .qt.qpipe.job.hdb_crypto_markout_backfill.sent,:enlist 1_msg; value msg}

/ The HDB's two tables as it holds them: date-partitioned, market_data carrying its market.
.qt.qpipe.job.hdb_crypto_markout_backfill.with_hdb:{[f]
    had:{[nm] $[nm in key `.; (1b;get nm); (0b;::)]} each `executions`market_data;
    `executions set update date:`date$source_time from .qpipe.source.hdb_crypto_markouts.raw_executions[];
    `market_data set .qt.support.stand_in_market_data update market:`crypto from .qpipe.source.hdb_crypto_markouts.raw_market_data[];
    `.qt.qpipe.job.hdb_crypto_markout_backfill.sent set ();
    r:@[f;::;{[e] `threw,e}];
    {[nm;k] $[first k; nm set last k; ![`.;();0b;enlist nm]]}'[`executions`market_data;had];
    r}

.qt.qpipe.job.hdb_crypto_markout_backfill.test_a_window_sends_two_queries_reading_books_past_its_end_and_before_its_start:{[t]
    w:2026.09.17D10:00:00 2026.09.17D11:00:00;
    `.qt.qpipe.job.hdb_crypto_markout_backfill.w set w;
    r:.qt.qpipe.job.hdb_crypto_markout_backfill.with_hdb {.qpipe.source.hdb_crypto_markouts.query[.qt.qpipe.job.hdb_crypto_markout_backfill.hdb;.qt.qpipe.job.hdb_crypto_markout_backfill.w 0;.qt.qpipe.job.hdb_crypto_markout_backfill.w 1]};
    .qunit.assertEquals[count .qt.qpipe.job.hdb_crypto_markout_backfill.sent;2;"one query for the executions, one for the books"];
    .qunit.assertEquals[.qt.qpipe.job.hdb_crypto_markout_backfill.sent 0;w;"executions over the window itself"];
    a:first .qt.qpipe.job.hdb_crypto_markout_backfill.sent 1;
    spec:.qpipe.transform.crypto_markout_outputs.job_spec;
    .qunit.assertEquals[a`read_from;(w 0)-spec`max_age;"no book older than the reference's max_age before the window's start (the grid has no negative horizon)"];
    .qunit.assertEquals[(a`skew;a`venues);(1;`all);"a day either side, every venue"];
    .qunit.assertEquals[asc distinct (a`probes)`sym;asc `$("BTC-USDT";"ETH-USDT");"only the pairs the executions' routes need"];
    .qunit.assertEquals[(min (a`probes)`time;max (a`probes)`time);(2026.09.17D10:00:00;2026.09.17D10:00:31);"the instants scoring asks about: each execution's own and each horizon from it"];
    .qunit.assertEquals[key r;`executions`market_data;"both inputs, raw - the scoring is the transform's"];
    .qunit.assertEquals[count .qt.qpipe.job.hdb_crypto_markout_backfill.scored r;12;"the window's three crypto executions, scored"]};

.qt.qpipe.job.hdb_crypto_markout_backfill.test_the_live_path_scores_exactly_as_the_fixture_does:{[t]
    r:.qt.qpipe.job.hdb_crypto_markout_backfill.with_hdb {.qpipe.source.hdb_crypto_markouts.query[.qt.qpipe.job.hdb_crypto_markout_backfill.hdb;(.qt.qpipe.job.hdb_crypto_markout_backfill.day[])0;(.qt.qpipe.job.hdb_crypto_markout_backfill.day[])1]};
    .qunit.assertTrue[.qetl.source.validate[`hdb_crypto_markouts;r];"the live inputs satisfy both contracts"];
    .qunit.assertEquals[.qt.qpipe.job.hdb_crypto_markout_backfill.scored[r];.qt.qpipe.job.hdb_crypto_markout_backfill.scored[.qpipe.source.hdb_crypto_markouts.fixture[]];"live and fixture agree"]};

.qt.qpipe.job.hdb_crypto_markout_backfill.test_only_the_books_the_routes_need_are_read:{[t]
    / BTC and ETH fills need BTC-USDT and ETH-USDT; a SOL-USDT book exists and is not read
    extra:{
        `market_data set .qt.support.stand_in_market_data (update market:`crypto from .qpipe.source.hdb_crypto_markouts.raw_market_data[]),
            update market:`crypto, sym:`$"SOL-USDT" from 2#.qpipe.source.hdb_crypto_markouts.raw_market_data[];
        `executions set update date:`date$source_time from .qpipe.source.hdb_crypto_markouts.raw_executions[];
        .qpipe.source.hdb_crypto_markouts.query[.qt.qpipe.job.hdb_crypto_markout_backfill.hdb;2026.09.17D10:00:00;2026.09.17D11:00:00]};
    `.qt.qpipe.job.hdb_crypto_markout_backfill.extra set extra;
    r:.qt.qpipe.job.hdb_crypto_markout_backfill.with_hdb {.qt.qpipe.job.hdb_crypto_markout_backfill.extra[]};
    .qunit.assertFalse[(`$"SOL-USDT") in distinct (r`market_data)`sym;"a pair no execution's route uses"]};

.qt.qpipe.job.hdb_crypto_markout_backfill.test_an_execution_early_in_a_window_is_priced_by_the_book_just_before_it:{[t]
    / the BTC-USDT book live at 10:00:03 was set at 09:59:59 - before the window, within max_age
    f:{
        `executions set ([] date:enlist 2026.09.17; source_time:enlist 2026.09.17D10:00:03; venue:enlist `a; sym:enlist `$"BTC-USDT";
            side:enlist 1; size:enlist 1f; price:enlist 62000f; fee:enlist 0f; fee_ccy:enlist `USDT; fill_id:enlist `e1; clock:enlist `venue);
        `market_data set .qt.support.stand_in_market_data ([] source_time:2026.09.17D09:59:59 2026.09.17D11:30:00; market:2#`crypto;
            sym:2#`$"BTC-USDT"; source:2#`a; bid:62010 63000f; ask:62012 63002f; clock:2#`venue);
        .qpipe.source.hdb_crypto_markouts.query[.qt.qpipe.job.hdb_crypto_markout_backfill.hdb;2026.09.17D10:00:00;2026.09.17D11:00:00]};
    `.qt.qpipe.job.hdb_crypto_markout_backfill.early set f;
    r:.qt.qpipe.job.hdb_crypto_markout_backfill.with_hdb {.qt.qpipe.job.hdb_crypto_markout_backfill.early[]};
    s:select from .qt.qpipe.job.hdb_crypto_markout_backfill.scored r where kind=`execution, horizon=0D00:00:01;
    .qunit.assertEquals[s`ref_t0;enlist 62011f;"priced against the 09:59:59 mid (62011), not null"]};

.qt.qpipe.job.hdb_crypto_markout_backfill.test_the_book_before_a_window_is_bounded_by_the_references_max_age:{[t]
    f:{
        `executions set ([] date:enlist 2026.09.17; source_time:enlist 2026.09.17D10:00:03; venue:enlist `a; sym:enlist `$"BTC-USDT";
            side:enlist 1; size:enlist 1f; price:enlist 62000f; fee:enlist 0f; fee_ccy:enlist `USDT; fill_id:enlist `e1; clock:enlist `venue);
        `market_data set .qt.support.stand_in_market_data ([] source_time:enlist 2026.09.17D09:59:00; market:enlist `crypto;
            sym:enlist `$"BTC-USDT"; source:enlist `a; bid:enlist 62010f; ask:enlist 62012f; clock:enlist `venue);
        .qpipe.source.hdb_crypto_markouts.query[.qt.qpipe.job.hdb_crypto_markout_backfill.hdb;2026.09.17D10:00:00;2026.09.17D11:00:00]};
    `.qt.qpipe.job.hdb_crypto_markout_backfill.stale set f;
    r:.qt.qpipe.job.hdb_crypto_markout_backfill.with_hdb {.qt.qpipe.job.hdb_crypto_markout_backfill.stale[]};
    .qunit.assertEquals[count r`market_data;0;"a minute-old book is past the 5s max_age: scoring would ignore it, so it is not fetched"]};

.qt.qpipe.job.hdb_crypto_markout_backfill.test_a_window_without_executions_does_not_read_books:{[t]
    r:.qt.qpipe.job.hdb_crypto_markout_backfill.with_hdb {.qpipe.source.hdb_crypto_markouts.query[.qt.qpipe.job.hdb_crypto_markout_backfill.hdb;2026.09.17D12:00:00;2026.09.17D13:00:00]};
    .qunit.assertEquals[(count .qt.qpipe.job.hdb_crypto_markout_backfill.sent;count r`executions;count r`market_data);(1;0;0);"no executions, no book query"];
    .qunit.assertTrue[.qetl.source.validate[`hdb_crypto_markouts;r];"and both inputs still in their contracts' shape"]};

/ --- readiness: reference data that has not landed is not a scored window (#1927) ------------

/ Run the day with the source's fixture replaced by `f`, then put the real one back.
.qt.qpipe.job.hdb_crypto_markout_backfill.run_with:{[f]
    keep:(.qetl.source.def `hdb_crypto_markouts)`fixture;
    .[`.qetl.source.sources;(`hdb_crypto_markouts;`fixture);:;f];
    d:.qt.qpipe.job.hdb_crypto_markout_backfill.day[];
    .qpipe.job.hdb_crypto_markout_backfill.init[.qt.qpipe.job.hdb_crypto_markout_backfill.spec_for[`v1;d 0;d 1]];
    r:@[{.qpipe.job.hdb_crypto_markout_backfill.run[]};::;{[e] `threw,e}];
    .qpipe.job.hdb_crypto_markout_backfill.cleanup[];
    .[`.qetl.source.sources;(`hdb_crypto_markouts;`fixture);:;keep];
    if[`threw~first r; 'last r];
    r}

/ Executions with no books at all, as the HDB is before market_data lands.
.qt.qpipe.job.hdb_crypto_markout_backfill.no_books:{[]
    `executions`market_data!(.qpipe.source.hdb_crypto_markouts.raw_executions[];0#.qpipe.source.hdb_crypto_markouts.supporting`market_data)}

.qt.qpipe.job.hdb_crypto_markout_backfill.covered:{[]
    v:.qetl.source.fixture_version `v1;
    .qetl.coverage.is_covered[`crypto_markout;`;v;.z.p;2026.09.17D10:00:00;2026.09.17D11:00:00]}

.qt.qpipe.job.hdb_crypto_markout_backfill.test_a_window_with_executions_and_no_books_is_not_claimed_and_replans_once_they_land:{[t]
    r:.qt.qpipe.job.hdb_crypto_markout_backfill.run_with[.qt.qpipe.job.hdb_crypto_markout_backfill.no_books];
    .qunit.assertEquals[(r`state;r`windows_failed;count get `crypto_markout);(`partial;1;0);
        "the 10:00 window is refused, nothing is written, and the run says it is partial"];
    .qunit.assertFalse[.qt.qpipe.job.hdb_crypto_markout_backfill.covered[];"no coverage for the window that could not be scored"];
    r2:.qt.qpipe.job.hdb_crypto_markout_backfill.run_with[.qpipe.source.hdb_crypto_markouts.fixture];
    .qunit.assertEquals[(r2`state;r2`windows_completed;count get `crypto_markout);(`completed;1;12);
        "the books landed: only that window replans, at the same version, and is scored"];
    .qunit.assertEquals[count select from get `crypto_markout where status=`unavailable;0;
        "scored for real, not unavailable"];
    .qunit.assertTrue[.qt.qpipe.job.hdb_crypto_markout_backfill.covered[];"and now claimed"]};

.qt.qpipe.job.hdb_crypto_markout_backfill.test_a_window_with_no_executions_is_still_covered:{[t]
    none:{`executions`market_data!(0#.qpipe.source.hdb_crypto_markouts.raw_executions[];0#.qpipe.source.hdb_crypto_markouts.supporting`market_data)};
    r:.qt.qpipe.job.hdb_crypto_markout_backfill.run_with[none];
    .qunit.assertEquals[(r`state;r`windows_failed;count get `crypto_markout);(`completed;0;0);"nothing to score is not a missing reference"];
    .qunit.assertTrue[.qt.qpipe.job.hdb_crypto_markout_backfill.covered[];"and the empty window is claimed"]};

.qt.qpipe.job.hdb_crypto_markout_backfill.test_only_unroutable_executions_are_ready_without_books:{[t]
    b:.qt.qpipe.job.hdb_crypto_markout_backfill.no_books[];
    fx:b;
    fx[`executions]:select from b`executions where sym=`EURUSD;
    .qunit.assertEquals[.qpipe.source.hdb_crypto_markouts.ready[fx;2026.09.17D00:00;2026.09.18D00:00];();
        "no crypto route, no leg, nothing the books could be missing for"];
    .qunit.assertEquals[count .qpipe.source.hdb_crypto_markouts.ready[b;2026.09.17D00:00;2026.09.18D00:00];1;
        "routable executions and no books at all: one unmet requirement"]};

/ --- readiness: the window's end plus the furthest horizon must be inside the books -----

.qt.qpipe.job.hdb_crypto_markout_backfill.test_complete_through_is_the_latest_crypto_book_less_the_furthest_horizon:{[t]
    r:.qt.qpipe.job.hdb_crypto_markout_backfill.with_hdb {.qpipe.source.hdb_crypto_markouts.complete_through[.qt.qpipe.job.hdb_crypto_markout_backfill.hdb]};
    .qunit.assertEquals[r;2026.09.17D10:00:30-0D00:00:10;"the last book is at 10:00:30 and the furthest horizon is 10s"]};

.qt.qpipe.job.hdb_crypto_markout_backfill.test_a_newer_fx_book_does_not_vouch_for_crypto:{[t]
    f:{`market_data set .qt.support.stand_in_market_data (update market:`crypto from .qpipe.source.hdb_crypto_markouts.raw_market_data[]),
            ([] source_time:enlist 2026.09.17D12:00:00; market:enlist `fx; sym:enlist `EURUSD; source:enlist `a;
                bid:enlist 1.1; ask:enlist 1.2; clock:enlist `receipt);
        .qpipe.source.hdb_crypto_markouts.complete_through[.qt.qpipe.job.hdb_crypto_markout_backfill.hdb]};
    `.qt.qpipe.job.hdb_crypto_markout_backfill.fxnewer set f;
    r:.qt.qpipe.job.hdb_crypto_markout_backfill.with_hdb {.qt.qpipe.job.hdb_crypto_markout_backfill.fxnewer[]};
    .qunit.assertEquals[r;2026.09.17D10:00:30-0D00:00:10;"an FX book at noon says nothing about whether the crypto books have landed"]};

.qt.qpipe.job.hdb_crypto_markout_backfill.test_an_hdb_without_books_vouches_for_nothing:{[t]
    f:{`market_data set 0#.qt.support.stand_in_market_data update market:`crypto from .qpipe.source.hdb_crypto_markouts.raw_market_data[];
        .qpipe.source.hdb_crypto_markouts.complete_through[.qt.qpipe.job.hdb_crypto_markout_backfill.hdb]};
    `.qt.qpipe.job.hdb_crypto_markout_backfill.empty set f;
    r:.qt.qpipe.job.hdb_crypto_markout_backfill.with_hdb {.qt.qpipe.job.hdb_crypto_markout_backfill.empty[]};
    .qunit.assertEquals[r;-0Wp;"no book yet: nothing is complete, not a wrapped far-future instant"]};

/ A run pins the job spec's snapshot before it calculates, and every row names it (#1999).
.qt.qpipe.job.hdb_crypto_markout_backfill.test_a_run_pins_the_snapshot_its_rows_name:{[t]
    .qpipe.job.hdb_crypto_markout_backfill.init[.qt.qpipe.job.hdb_crypto_markout_backfill.spec_for[`v1;.qt.qpipe.job.hdb_crypto_markout_backfill.day[]0;.qt.qpipe.job.hdb_crypto_markout_backfill.day[]1]];
    r:.qpipe.job.hdb_crypto_markout_backfill.run[];
    id:.qpipe.transform.crypto_markout_outputs.config_id;
    .qunit.assertEquals[(r`state;distinct (get `crypto_markout)`config_id;(.qetl.snapshot.list[`])`snapshot_id);(`completed;enlist id;enlist id);"the one snapshot, recorded by the run, named by every row"]};
