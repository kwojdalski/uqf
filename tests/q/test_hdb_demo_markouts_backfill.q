/ test_hdb_demo_markouts_backfill.q - the hdb_demo_markouts source and its bounded worker
/ (.qpipe.source.hdb_demo_markouts, .qpipe.job.hdb_demo_markouts_backfill).
/ .
/ No HDB is needed. The live path is driven through a stand-in handle that
/ evaluates each query against in-memory `trades` and `quote` tables, so what
/ is proven is everything on this side of the wire: which two queries a
/ window sends, over which bounds, and which rows come back. The scoring is
/ the worker's transform (#617), so it is asserted through that - the same
/ call a window makes. The full lifecycle (init, run, idle second run, dry
/ run) is test_every_worker_runs.q's, which drives this worker with every
/ other.


.qt.qpipe.job.hdb_demo_markouts_backfill.spec_for:{[version;from_ts;to_ts] `source_version`range_from`range_to!(version;from_ts;to_ts)}

/ Two inputs, scored as a window scores them.
.qt.qpipe.job.hdb_demo_markouts_backfill.scored:{[inputs] .qetl.transform.apply[`hdb_demo_markouts_score;inputs]}

/ The fixture's fills and quotes, scored.
.qt.qpipe.job.hdb_demo_markouts_backfill.scored_fixture:{[] .qt.qpipe.job.hdb_demo_markouts_backfill.scored .qpipe.source.hdb_demo_markouts.fixture[]}

/ The fixture's day: its fills are at 10:00:00-10:00:07 and 23:59:59.
.qt.qpipe.job.hdb_demo_markouts_backfill.day:{[] (2026.09.17D00:00:00.000000000;2026.09.18D00:00:00.000000000)}

.qt.qpipe.job.hdb_demo_markouts_backfill.beforeNamespace_isolate:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    system"mkdir -p build/test-status";
    }

.qt.qpipe.job.hdb_demo_markouts_backfill.setUp_fresh:{[]
    .qt.support.reset_coverage_ledger[];
    .qetl.cfg.reset[];
    .qetl.cfg.set_layers[()!();()!();()!()];
    setenv[`UQF_DRY_RUN;""];
    setenv[`UQF_SOURCE_CRED_HDB_DEMO_MARKOUTS;""];
    .qetl.job.bounded.state.release_lock `hdb_demo_markouts_backfill;
    .qetl.job.bounded.state.clear_checkpoint `hdb_demo_markouts_backfill;
    `demo_execution_quality set 0#.qetl.plant.shape `demo_execution_quality;
    }

.qt.qpipe.job.hdb_demo_markouts_backfill.tearDown_release:{[] .qpipe.job.hdb_demo_markouts_backfill.cleanup[];}

/ --- scoring --------------------------------------------------------------

.qt.qpipe.job.hdb_demo_markouts_backfill.test_every_fill_is_scored_at_every_live_horizon:{[t]
    f:.qt.qpipe.job.hdb_demo_markouts_backfill.scored_fixture[];
    .qunit.assertEquals[(count f;asc distinct f`horizon);(4*count .qpipe.transform.demo_markouts.horizons;asc .qpipe.transform.demo_markouts.horizons);
        "four fills, each at the live job's horizons"]};

.qt.qpipe.job.hdb_demo_markouts_backfill.test_the_horizons_are_the_shared_transforms_not_a_copy:{[t]
    / Change the shared transform's horizons and the backfill follows - the
    / live job reads the same ones, so the two cannot drift apart (#884).
    keep:.qpipe.transform.demo_markouts.horizons;
    .qpipe.transform.demo_markouts.horizons:enlist 0D00:00:01;
    n:count .qt.qpipe.job.hdb_demo_markouts_backfill.scored_fixture[];
    .qpipe.transform.demo_markouts.horizons:keep;
    .qunit.assertEquals[n;4;"one horizon, one row per fill"]};

.qt.qpipe.job.hdb_demo_markouts_backfill.test_a_markout_is_the_live_functions_answer:{[t]
    / EURUSD bought at 1.1001; the 1s mid is (1.1004+1.1006)/2 = 1.1005, so +4 pips.
    f:.qt.qpipe.job.hdb_demo_markouts_backfill.scored_fixture[];
    r:first select from f where sym=`EURUSD, trade_time=2026.09.17D10:00:00, horizon=0D00:00:01;
    .qunit.assertEquals[(r`ref_price;r`markout_pips);(1.1005;4f);"a buy marked against the later mid"]};

.qt.qpipe.job.hdb_demo_markouts_backfill.test_the_scoring_is_a_verified_transform:{[t]
    / #617: the markout is no longer hidden in the source's query, where the
    / fixture path skipped it - it is a transform whose examples run here.
    .qunit.assertTrue[all exec passed from .qetl.transform.verify `hdb_demo_markouts_score;
        "every example, and the empty case, give what was worked by hand"]};

.qt.qpipe.job.hdb_demo_markouts_backfill.test_no_fills_score_to_nothing_whatever_the_quotes_hold:{[t]
    inputs:@[.qpipe.source.hdb_demo_markouts.fixture[];`trades;0#];
    .qunit.assertEquals[.qt.qpipe.job.hdb_demo_markouts_backfill.scored[inputs];0#.qetl.plant.shape `demo_execution_quality;
        "the primary input owns the window: quotes alone publish nothing"]};

.qt.qpipe.job.hdb_demo_markouts_backfill.test_the_fixture_is_cut_to_the_window_but_its_quotes_are_not:{[t]
    / Fills at 10:00:00-10:00:07 and 23:59:59; quotes at 09:59:59-10:00:20.
    got:last .qetl.source.fetch_window[`hdb_demo_markouts;0Ni;2026.09.17D10:00:00;2026.09.17D10:00:06];
    .qunit.assertEquals[(count got`trades;count got`quote);(2;6);
        "two fills in [10:00:00;10:00:06), and every quote, including 09:59:59's before the window"]};

.qt.qpipe.job.hdb_demo_markouts_backfill.test_a_fill_with_no_later_quote_keeps_a_null_markout:{[t]
    / As the live job does: the gap shows instead of the fill vanishing.
    f:.qt.qpipe.job.hdb_demo_markouts_backfill.scored_fixture[];
    .qunit.assertEquals[exec count i from f where sym=`GBPUSD, null markout_pips;2;
        "both GBPUSD horizons are null, and present"]};

/ --- a run ----------------------------------------------------------------

.qt.qpipe.job.hdb_demo_markouts_backfill.test_a_run_writes_every_markout_into_execution_quality:{[t]
    .qpipe.job.hdb_demo_markouts_backfill.init[.qt.qpipe.job.hdb_demo_markouts_backfill.spec_for[`v1;.qt.qpipe.job.hdb_demo_markouts_backfill.day[]0;.qt.qpipe.job.hdb_demo_markouts_backfill.day[]1]];
    r:.qpipe.job.hdb_demo_markouts_backfill.run[];
    / get, not the bare name: inside .qt.qpipe.job.hdb_demo_markouts_backfill that would mean .qt.qpipe.job.hdb_demo_markouts_backfill.demo_execution_quality.
    .qunit.assertEquals[(r`state;count get `demo_execution_quality);(`completed;8);
        "all eight markouts land, including the fill whose horizons cross midnight"];
    .qunit.assertEquals[cols get `demo_execution_quality;cols .qetl.plant.shape `demo_execution_quality;
        "in demo_execution_quality's own columns, as the live job writes them"]};

.qt.qpipe.job.hdb_demo_markouts_backfill.test_a_restatement_replaces_rather_than_duplicates:{[t]
    / The target key is (sym;trade_time;horizon): a second version over the
    / same window - or the live job's rows for the same fills - is replaced.
    .qpipe.job.hdb_demo_markouts_backfill.init[.qt.qpipe.job.hdb_demo_markouts_backfill.spec_for[`v1;.qt.qpipe.job.hdb_demo_markouts_backfill.day[]0;.qt.qpipe.job.hdb_demo_markouts_backfill.day[]1]];
    .qpipe.job.hdb_demo_markouts_backfill.run[];
    .qpipe.job.hdb_demo_markouts_backfill.cleanup[];
    .qpipe.job.hdb_demo_markouts_backfill.init[.qt.qpipe.job.hdb_demo_markouts_backfill.spec_for[`v2;.qt.qpipe.job.hdb_demo_markouts_backfill.day[]0;.qt.qpipe.job.hdb_demo_markouts_backfill.day[]1]];
    .qpipe.job.hdb_demo_markouts_backfill.run[];
    .qunit.assertEquals[count get `demo_execution_quality;8;"still one row per fill per horizon"]};

.qt.qpipe.job.hdb_demo_markouts_backfill.test_replace_across_windows_keeps_the_previous_windows_late_markouts:{[t]
    / #972: the 23:59:59 fill's markouts are timed after midnight (trade_time+
    / horizon), but belong to the 23:00 window. The 00:00 window's replace used
    / to clear them as its own; the window is cleared on trade_time now.
    .qetl.cfg.set_layers[(enlist `on_conflict)!enlist "replace";()!();()!()];
    .qpipe.job.hdb_demo_markouts_backfill.init[.qt.qpipe.job.hdb_demo_markouts_backfill.spec_for[`v1;2026.09.17D23:00;2026.09.18D01:00]];
    r:.qpipe.job.hdb_demo_markouts_backfill.run[];
    late:select from get `demo_execution_quality where trade_time=2026.09.17D23:59:59;
    .qunit.assertEquals[(r`state;r`windows_completed);(`completed;2);"both windows ran"];
    .qunit.assertEquals[(count late;asc late`horizon);(2;asc .qpipe.transform.demo_markouts.horizons);
        "one markout per horizon survives the next window's replace"];
    .qunit.assertTrue[all late[`time]>=2026.09.18D00:00;"and they are the ones timed past midnight"]};

.qt.qpipe.job.hdb_demo_markouts_backfill.test_replace_still_restates_a_window_on_its_window_column:{[t]
    .qetl.cfg.set_layers[(enlist `on_conflict)!enlist "replace";()!();()!()];
    .qpipe.job.hdb_demo_markouts_backfill.init[.qt.qpipe.job.hdb_demo_markouts_backfill.spec_for[`v1;.qt.qpipe.job.hdb_demo_markouts_backfill.day[]0;.qt.qpipe.job.hdb_demo_markouts_backfill.day[]1]];
    .qpipe.job.hdb_demo_markouts_backfill.run[];
    .qpipe.job.hdb_demo_markouts_backfill.cleanup[];
    .qpipe.job.hdb_demo_markouts_backfill.init[.qt.qpipe.job.hdb_demo_markouts_backfill.spec_for[`v2;.qt.qpipe.job.hdb_demo_markouts_backfill.day[]0;.qt.qpipe.job.hdb_demo_markouts_backfill.day[]1]];
    .qpipe.job.hdb_demo_markouts_backfill.run[];
    .qunit.assertEquals[count get `demo_execution_quality;8;"a second version replaces, never doubles"]};

/ --- the live path, against a stand-in HDB ---------------------------------

/ A handle that runs each query here, against `trades` and `quote` shaped as
/ the HDB holds them (date-partitioned, so they carry a `date`), recording
/ the bounds it was asked for. The real tables of these names, if this
/ process has them, are put back after.
.qt.qpipe.job.hdb_demo_markouts_backfill.hdb:{[msg] .qt.qpipe.job.hdb_demo_markouts_backfill.sent,:enlist 1_msg; value msg}

.qt.qpipe.job.hdb_demo_markouts_backfill.with_hdb:{[f]
    had:{[nm] $[nm in key `.; (1b;get nm); (0b;::)]} each `trades`quote;
    `trades set update date:`date$time from .qpipe.source.hdb_demo_markouts.raw_fills[];
    `quote set update date:`date$time from .qpipe.source.hdb_demo_markouts.raw_quotes[];
    `.qt.qpipe.job.hdb_demo_markouts_backfill.sent set ();
    r:@[f;::;{[e] `threw,e}];
    {[nm;k] $[first k; nm set last k; ![`.;();0b;enlist nm]]}'[`trades`quote;had];
    r}

.qt.qpipe.job.hdb_demo_markouts_backfill.test_a_window_sends_two_queries_reading_quotes_past_its_end:{[t]
    w:2026.09.17D10:00:00 2026.09.17D11:00:00;
    / The window through a global: `with_hdb {..}[w]` would run the query
    / before with_hdb has put the stand-in tables in place.
    `.qt.qpipe.job.hdb_demo_markouts_backfill.w set w;
    r:.qt.qpipe.job.hdb_demo_markouts_backfill.with_hdb {.qpipe.source.hdb_demo_markouts.query[.qt.qpipe.job.hdb_demo_markouts_backfill.hdb;.qt.qpipe.job.hdb_demo_markouts_backfill.w 0;.qt.qpipe.job.hdb_demo_markouts_backfill.w 1]};
    .qunit.assertEquals[count .qt.qpipe.job.hdb_demo_markouts_backfill.sent;2;"one query for the fills, one for the quotes"];
    .qunit.assertEquals[.qt.qpipe.job.hdb_demo_markouts_backfill.sent 0;w;"fills over the window itself"];
    .qunit.assertEquals[.qt.qpipe.job.hdb_demo_markouts_backfill.sent 1;(w 0;(w 1)+.qpipe.transform.demo_markouts.max_horizon);
        "quotes on to the window's end plus the longest horizon"];
    .qunit.assertEquals[key r;`trades`quote;"both inputs, raw - the scoring is the transform's"];
    .qunit.assertEquals[count .qt.qpipe.job.hdb_demo_markouts_backfill.scored r;6;"the window's three fills, scored at both horizons"]};

.qt.qpipe.job.hdb_demo_markouts_backfill.test_the_live_path_scores_exactly_as_the_fixture_does:{[t]
    / The fixture is the same rows, so the live path over the fixture's day
    / must score to the same table.
    r:.qt.qpipe.job.hdb_demo_markouts_backfill.with_hdb {.qpipe.source.hdb_demo_markouts.query[.qt.qpipe.job.hdb_demo_markouts_backfill.hdb;.qt.qpipe.job.hdb_demo_markouts_backfill.day[]0;.qt.qpipe.job.hdb_demo_markouts_backfill.day[]1]};
    .qunit.assertTrue[.qetl.source.validate[`hdb_demo_markouts;r];"the live inputs satisfy both contracts"];
    .qunit.assertEquals[.qt.qpipe.job.hdb_demo_markouts_backfill.scored[r];.qt.qpipe.job.hdb_demo_markouts_backfill.scored_fixture[];"live and fixture agree"]};

.qt.qpipe.job.hdb_demo_markouts_backfill.test_a_fill_early_in_a_window_is_priced_by_the_quote_before_it:{[t]
    / The quote live at 10:00:03 was set at 09:59:50 - before the window.
    / Reading quotes only from the window's start priced this fill null, and
    / the target key then replaced the live job's correct row with that null.
    `.qt.qpipe.job.hdb_demo_markouts_backfill.early set {
        `trades set ([] date:enlist 2026.09.17; time:enlist 2026.09.17D10:00:03; sym:enlist `EURUSD;
            side:enlist 1; trade_price:enlist 1.1; pip_factor:enlist 10000; fill_id:enlist `f1);
        `quote set ([] date:2026.09.17 2026.09.17; time:2026.09.17D09:59:50 2026.09.17D11:30:00;
            sym:`EURUSD`EURUSD; bid:1.1 1.2; ask:1.1002 1.2002);
        .qpipe.source.hdb_demo_markouts.query[.qt.qpipe.job.hdb_demo_markouts_backfill.hdb;2026.09.17D10:00;2026.09.17D11:00]};
    r:.qt.qpipe.job.hdb_demo_markouts_backfill.with_hdb {.qt.qpipe.job.hdb_demo_markouts_backfill.early[]};
    .qunit.assertEquals[exec markout_pips from .qt.qpipe.job.hdb_demo_markouts_backfill.scored r;1 1f;
        "both horizons priced against the 09:59:50 mid (1.1001), not null"]};

.qt.qpipe.job.hdb_demo_markouts_backfill.test_the_quote_before_a_window_is_bounded_by_the_lookback:{[t]
    / A pair quoted only long before the window stays unpriced, as the live
    / job would leave it after a restart - the lookback is a bound, not all history.
    `.qt.qpipe.job.hdb_demo_markouts_backfill.stale set {
        `trades set ([] date:enlist 2026.09.17; time:enlist 2026.09.17D10:00:03; sym:enlist `EURUSD;
            side:enlist 1; trade_price:enlist 1.1; pip_factor:enlist 10000; fill_id:enlist `f1);
        `quote set ([] date:enlist 2026.09.01; time:enlist 2026.09.01D10:00; sym:enlist `EURUSD;
            bid:enlist 1.1; ask:enlist 1.1002);
        .qpipe.source.hdb_demo_markouts.query[.qt.qpipe.job.hdb_demo_markouts_backfill.hdb;2026.09.17D10:00;2026.09.17D11:00]};
    r:.qt.qpipe.job.hdb_demo_markouts_backfill.with_hdb {.qt.qpipe.job.hdb_demo_markouts_backfill.stale[]};
    .qunit.assertTrue[all null exec markout_pips from .qt.qpipe.job.hdb_demo_markouts_backfill.scored r;"sixteen days back is past the 7-day lookback"]};

.qt.qpipe.job.hdb_demo_markouts_backfill.test_a_window_without_fills_does_not_read_quotes:{[t]
    r:.qt.qpipe.job.hdb_demo_markouts_backfill.with_hdb {.qpipe.source.hdb_demo_markouts.query[.qt.qpipe.job.hdb_demo_markouts_backfill.hdb;2026.09.17D12:00:00;2026.09.17D13:00:00]};
    .qunit.assertEquals[(count .qt.qpipe.job.hdb_demo_markouts_backfill.sent;count r`trades;count r`quote);(1;0;0);
        "no fills, no quote query"];
    .qunit.assertTrue[.qetl.source.validate[`hdb_demo_markouts;r];"and both inputs still in their contracts' shape"]};


/ --- a worker on two inputs (#617) -----------------------------------------

.qt.qpipe.job.hdb_demo_markouts_backfill.test_a_worker_on_two_inputs_needs_a_transform_that_reads_both:{[t]
    / demo_deals_passthrough reads one input; this source hands over two.
    err:@[{.qetl.job.bounded.define[`mbftest_one_input;x]; ""};
        `source`dataset`width`transform!(`hdb_demo_markouts;`mbftest_ds;1D;`demo_deals_passthrough);{x}];
    .qunit.assertTrue[err like "*must read source hdb_demo_markouts's inputs trades, quote*";
        "refused at declaration, naming the inputs it must read"]};

.qt.qpipe.job.hdb_demo_markouts_backfill.test_each_input_is_checked_against_its_own_contract:{[t]
    / The transform's quote input lacks ask, which the source's quote contract has.
    .qetl.transform.define[`mbftest_short_quote;`inputs`output`fn`examples!(
        `trades`quote!((.qetl.transform.def `hdb_demo_markouts_score)[`inputs]`trades;
            ([] time:`timestamp$(); sym:`symbol$(); bid:`float$()));
        .qetl.plant.shape `demo_execution_quality;
        {[f;q] 0#.qetl.plant.shape `demo_execution_quality};
        enlist `inputs`expected!(
            `trades`quote!(0#(.qetl.transform.def `hdb_demo_markouts_score)[`inputs]`trades;
                ([] time:enlist 2026.09.17D10:00; sym:enlist `EURUSD; bid:enlist 1.1));
            .qetl.plant.shape `demo_execution_quality))];
    err:@[{.qetl.job.bounded.define[`mbftest_short;x]; ""};
        `source`dataset`width`transform!(`hdb_demo_markouts;`mbftest_ds;1D;`mbftest_short_quote);{x}];
    .qt.support.drop_rows[`.qetl.transform.registry;`mbftest_short_quote];
    .qunit.assertTrue[err like "*does not read source hdb_demo_markouts's contracts: quote: *";
        "the quote input is held to the quote contract, by name"]};

/ The window's facts, on the fixture's scored rows. facts counted distinct
/ fills with `flip` in front of the take, and `distinct` of a dictionary
/ throws 'type - logged as "facts function failed" on every window, which
/ no test looked at until .qt.qpipe.worker_runs's runs did.
.qt.qpipe.job.hdb_demo_markouts_backfill.test_the_facts_describe_a_scored_window:{[t]
    b:.qpipe.job.hdb_demo_markouts_backfill.score[.qpipe.source.hdb_demo_markouts.raw_fills[];.qpipe.source.hdb_demo_markouts.raw_quotes[]];
    f:.qpipe.job.hdb_demo_markouts_backfill.facts b;
    .qunit.assertEquals[f`scored_fills`horizons`unpriced;4 2 2;
        "four fills, two horizons, and the unpriced rows counted"]};

/ #1281: the output's key leaves `time` out and its `time` is trade_time+horizon,
/ so a fill in the last seconds of a day has its live rows in the NEXT day's
/ partition and its twin's 1s row in this one. Only `replace clears across that
/ boundary (io_hdb's spill) - an upsert would leave both rows. So the worker
/ must DECLARE replace, or a plain refill duplicates fills near midnight.
.qt.qpipe.job.hdb_demo_markouts_backfill.test_a_refill_replaces_so_the_live_copy_across_midnight_is_cleared:{[t]
    .qunit.assertEquals[.qetl.job.bounded.on_conflict `hdb_demo_markouts_backfill;`replace;
        "declared replace: upsert does not clear the next day's live copy of a key"];
    .qunit.assertEquals[`time in (),.qetl.job.bounded.def[`hdb_demo_markouts_backfill]`target_key;0b;
        "the spill only applies while time is out of the key"]};


/ --- fill identity (#1080) -----------------------------------------------

/ Two fills of one pair that share a time, a price and a side: only their
/ ids tell them apart. `ids` is the fill_id column.
.qt.qpipe.job.hdb_demo_markouts_backfill.twin_fills:{[ids]
    inputs:.qpipe.source.hdb_demo_markouts.fixture[];
    inputs[`trades]:([] time:2#2026.09.17D10:00:00; sym:2#`EURUSD; side:2#1; trade_price:2#1.1001;
        pip_factor:2#.qccy.pip_factor `EURUSD; fill_id:ids);
    inputs}

.qt.qpipe.job.hdb_demo_markouts_backfill.test_scoring_carries_each_fills_id_onto_its_horizons:{[t]
    f:.qt.qpipe.job.hdb_demo_markouts_backfill.scored[.qt.qpipe.job.hdb_demo_markouts_backfill.twin_fills[`fxa`fxb]];
    .qunit.assertEquals[f`fill_id;`fxa`fxa`fxb`fxb;"fill i's id on its rows, one per horizon"]};

.qt.qpipe.job.hdb_demo_markouts_backfill.test_the_target_key_names_the_fill_so_twins_are_not_collapsed:{[t]
    / On master the key was (sym;trade_time;horizon): the two fills' markouts
    / collapsed to one row per horizon, 2 rows, losing a fill.
    key_cols:.qetl.job.bounded.def[`hdb_demo_markouts_backfill]`target_key;
    f:.qt.qpipe.job.hdb_demo_markouts_backfill.scored[.qt.qpipe.job.hdb_demo_markouts_backfill.twin_fills[`fxa`fxb]];
    `mbf_twin set 0#f;
    .qetl.io.write_keyed[.qetl.io.memory;`mbf_twin;f;`on_conflict`row_key!(`upsert;key_cols)];
    .qetl.io.write_keyed[.qetl.io.memory;`mbf_twin;f;`on_conflict`row_key!(`upsert;key_cols)];
    .qunit.assertEquals[(count get `mbf_twin;asc distinct (get `mbf_twin)`fill_id);(4;`fxa`fxb);
        "two fills x two horizons survive, and a re-run still replaces rather than duplicates"]};

.qt.qpipe.job.hdb_demo_markouts_backfill.test_a_legacy_fill_without_an_id_still_scores_and_keys:{[t]
    / A partition written before the column holds null ids (hdb-check --fix):
    / the row scores, keyed as it was by sym and time.
    f:.qt.qpipe.job.hdb_demo_markouts_backfill.scored[.qt.qpipe.job.hdb_demo_markouts_backfill.twin_fills[2#`]];
    .qunit.assertEquals[(count f;distinct f`fill_id);(4;enlist `);"null ids pass through, unmapped"]};

.qt.qpipe.job.hdb_demo_markouts_backfill.test_score_carries_each_fills_own_id_with_shuffled_fills:{[t]
    / fills out of time order: fill_id must come from the row, not from position
    shuffled:([] time:2026.09.17D10:00:09 2026.09.17D10:00:00 2026.09.17D10:00:04; sym:`EURUSD`GBPUSD`EURUSD;
        side:1 1 1; trade_price:1.1 1.3 1.2; pip_factor:10000 10000 10000; fill_id:`f9`f0`f4);
    quotes:([] time:enlist 2026.09.17D10:00:00; sym:enlist `EURUSD; bid:enlist 1.1; ask:enlist 1.1);
    r:.qpipe.transform.demo_markouts.score[shuffled;quotes];
    pairs_in:.qsynth.pairs;
    keep:shuffled where shuffled[`sym] in pairs_in;
    .qunit.assertEquals[r`fill_id;raze (count .qpipe.transform.demo_markouts.horizons)#'keep`fill_id;"each row carries its own fill's id"];
    .qunit.assertEquals[r`trade_price;raze (count .qpipe.transform.demo_markouts.horizons)#'keep`trade_price;"and its own price"]};
