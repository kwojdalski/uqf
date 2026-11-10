/ test_hdb_eq_markout_backfill.q - the hdb_eq_markouts source and its bounded worker
/ (.qpipe.source.hdb_eq_markouts, .qpipe.job.hdb_eq_markout_backfill, #1981).
/ .
/ No HDB is needed. The live path is driven through a stand-in handle that evaluates each
/ query against in-memory `executions` and `market_data`, so what is proven is everything on
/ this side of the wire: which two queries a window sends, over which bounds, and which rows
/ come back. The scoring is the worker's transform, so it is asserted through that - the same
/ call a window makes. The live-versus-refill claim itself (a refilled row is the row the live
/ job publishes) is test_eq_markout.q's.
/ .
/ The fixture's day (src/etl/sources/hdb_eq_markouts.q), 2026.02.25, a winter day whose session is
/ [14:30, 21:00) UTC: an AAPL buy on the venue clock (e1), a META sell ten seconds before the close
/ (e3), an AAPL buy on the RECEIPT clock (e2, which the venue-clock books cannot price) and a
/ EURUSD fill on another venue, which is not an equity fill and is never read (e4).


.qt.qpipe.job.hdb_eq_markout_backfill.spec_for:{[version;from_ts;to_ts] `source_version`range_from`range_to!(version;from_ts;to_ts)}

/ Two inputs, scored as a window scores them.
.qt.qpipe.job.hdb_eq_markout_backfill.scored:{[inputs] .qetl.transform.apply[`hdb_eq_markout_long;inputs]}

/ The fixture's day.
.qt.qpipe.job.hdb_eq_markout_backfill.day:{[] (2026.02.25D00:00:00.000000000;2026.02.26D00:00:00.000000000)}

.qt.qpipe.job.hdb_eq_markout_backfill.beforeNamespace_isolate:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    system"mkdir -p build/test-status";
    .qt.support.reset_snapshots[];
    }

.qt.qpipe.job.hdb_eq_markout_backfill.setUp_fresh:{[]
    .qt.support.reset_coverage_ledger[];
    .qetl.cfg.reset[];
    .qetl.cfg.set_layers[()!();()!();()!()];
    setenv[`UQF_DRY_RUN;""];
    setenv[`UQF_SOURCE_CRED_HDB_EQ_MARKOUTS;""];
    .qetl.job.bounded.state.release_lock `hdb_eq_markout_backfill;
    .qetl.job.bounded.state.clear_checkpoint `hdb_eq_markout_backfill;
    `eq_markout set 0#.qetl.plant.shape `eq_markout;
    .qt.support.reset_snapshots[];
    }

.qt.qpipe.job.hdb_eq_markout_backfill.tearDown_release:{[] .qpipe.job.hdb_eq_markout_backfill.cleanup[];}

/ --- the declaration ---------------------------------------------------------------

.qt.qpipe.job.hdb_eq_markout_backfill.test_the_worker_is_keyed_and_windowed_so_a_refill_replaces_the_live_rows:{[t]
    d:.qetl.job.bounded.def `hdb_eq_markout_backfill;
    .qunit.assertEquals[d`target_key;`venue`fill_id`horizon`kind`step;"the identity of a component"];
    .qunit.assertEquals[(d`window_column;d`on_conflict;d`dataset);(`source_time;`replace;`eq_markout);"replaced on the execution's time, into the live job's table"]};

/ --- scoring ------------------------------------------------------------------------

.qt.qpipe.job.hdb_eq_markout_backfill.test_the_scoring_is_a_verified_transform:{[t]
    .qunit.assertTrue[all exec passed from .qetl.transform.verify `hdb_eq_markout_long;"the hand-worked example and the empty case"]};

.qt.qpipe.job.hdb_eq_markout_backfill.test_the_twin_scores_what_the_live_transform_scores_and_keeps_the_cores_time:{[t]
    f:.qpipe.source.hdb_eq_markouts.fixture[];
    r:.qt.qpipe.job.hdb_eq_markout_backfill.scored[f];
    live:.qetl.transform.apply[`eq_markout_score;f];
    .qunit.assertEquals[delete time from r;live;"the same rows"];
    .qunit.assertEquals[r`time;(r`source_time)+0D00:01:00;"time is source_time plus the furthest horizon"]};

.qt.qpipe.job.hdb_eq_markout_backfill.test_the_fixtures_rows_are_the_hand_worked_ones:{[t]
    r:.qt.qpipe.job.hdb_eq_markout_backfill.scored[.qpipe.source.hdb_eq_markouts.fixture[]];
    .qunit.assertEquals[asc exec distinct fill_id from r;`e1`e2`e3;"the EURUSD fill is another venue's"];
    .qunit.assertEquals[count r;18;"three executions, three horizons, a total and an execution component each"];
    t1:select from r where kind=`total;
    / e1: a buy at 271.50. The mids are 271.375 at the fill, then 271.625, 271.875 and 272.125.
    .qunit.assertEquals[exec move from t1 where fill_id=`e1;0.125 0.375 0.625;"e1's moves at 5s, 30s and 1min"];
    .qunit.assertEquals[exec markout_bps from t1 where fill_id=`e1;10000*0.125 0.375 0.625%271.5;"in bps of the price"];
    .qunit.assertEquals[exec report_amount from t1 where fill_id=`e1;12.5 37.5 62.5;"times 100 shares, in USD"];
    .qunit.assertEquals[exec move from r where fill_id=`e1,kind=`execution;3#-0.125;"the fill itself paid 0.125 over the mid"];
    / e3: a sell at 643.25 ten seconds before the close, mid 643.00 at the fill and 642.50 at 20:59:55.
    .qunit.assertEquals[exec status from t1 where fill_id=`e3;`ok`unavailable`unavailable;"+5s is inside the session, +30s and +1min are after the close"];
    .qunit.assertEquals[exec move from t1 where fill_id=`e3;0.75,2#0n;"the sell gained 0.75 by 20:59:55, and has no figure across the close"];
    .qunit.assertEquals[exec move from r where fill_id=`e3,kind=`execution,horizon=0D00:00:05;enlist 0.25;"and 0.25 against the mid at the fill"];
    .qunit.assertTrue[all null exec report_amount from t1 where fill_id=`e3,status=`unavailable;"nothing worth is reported for the pair across the close"]};

.qt.qpipe.job.hdb_eq_markout_backfill.test_a_horizon_that_lands_exactly_on_the_close_is_unavailable:{[t]
    / a sell at 20:59:55 with a 5s horizon lands on 21:00:00, the instant the session ends
    f:.qpipe.source.hdb_eq_markouts.fixture[];
    e:update source_time:2026.02.25D20:59:55, fill_id:`edge from select from f[`executions] where fill_id=`e3;
    r:.qt.qpipe.job.hdb_eq_markout_backfill.scored[`executions`market_data!(e;f`market_data)];
    t1:select from r where kind=`total;
    .qunit.assertEquals[exec status from t1;`unavailable`unavailable`unavailable;"[open, close): the close instant is not in the session"];
    .qunit.assertTrue[all null exec move from t1;"and none has a figure"]};

.qt.qpipe.job.hdb_eq_markout_backfill.test_a_fill_on_the_receipt_clock_is_refused_not_scored_against_venue_books:{[t]
    r:.qt.qpipe.job.hdb_eq_markout_backfill.scored[.qpipe.source.hdb_eq_markouts.fixture[]];
    f3:select from r where fill_id=`e2;
    .qunit.assertEquals[distinct f3`status;enlist `clock_mismatch;"every row of e2 says why it is unpriced"];
    .qunit.assertTrue[all null f3`markout_bps;"and carries no bps from a clock it does not share"]};

.qt.qpipe.job.hdb_eq_markout_backfill.test_a_fill_of_another_venue_is_not_scored_even_if_handed_over:{[t]
    f:.qpipe.source.hdb_eq_markouts.fixture[];
    mixed:`executions`market_data!((f`executions),select source_time, venue, sym, side, size, price, fill_id, clock from .qpipe.source.hdb_eq_markouts.raw_executions[] where venue=`a;f`market_data);
    .qunit.assertEquals[asc exec distinct fill_id from .qt.qpipe.job.hdb_eq_markout_backfill.scored[mixed];`e1`e2`e3;"the transform itself leaves the FX fill out"]};

.qt.qpipe.job.hdb_eq_markout_backfill.test_no_executions_score_to_nothing_whatever_the_books_hold:{[t]
    inputs:@[.qpipe.source.hdb_eq_markouts.fixture[];`executions;0#];
    .qunit.assertEquals[.qt.qpipe.job.hdb_eq_markout_backfill.scored[inputs];0#.qetl.plant.shape `eq_markout;"the primary input owns the window"]};

/ --- the quality gate and facts -----------------------------------------------------------

.qt.qpipe.job.hdb_eq_markout_backfill.test_the_gate_passes_the_fixture_and_names_what_it_refuses:{[t]
    r:.qt.qpipe.job.hdb_eq_markout_backfill.scored[.qpipe.source.hdb_eq_markouts.fixture[]];
    f0:.qpipe.job.hdb_eq_markout_backfill.quality_check r;
    .qunit.assertEquals[(f0`check;f0`status);(enlist `config_unpinned;enlist `fail);"rows naming a configuration nobody recorded are refused"];
    .qpipe.transform.eq_markout_outputs.pin[];
    .qunit.assertEquals[count .qpipe.job.hdb_eq_markout_backfill.quality_check r;0;"clean"];
    bad:update markout_bps:0w from r where i=0;
    f:.qpipe.job.hdb_eq_markout_backfill.quality_check bad;
    .qunit.assertEquals[(f`check;f`status);(enlist `infinite_bps;enlist `fail);"an infinite markout is refused by name"]};

.qt.qpipe.job.hdb_eq_markout_backfill.test_the_facts_count_executions_horizons_the_unpriced_and_the_wrong_clock:{[t]
    r:.qt.qpipe.job.hdb_eq_markout_backfill.scored[.qpipe.source.hdb_eq_markouts.fixture[]];
    .qunit.assertEquals[.qpipe.job.hdb_eq_markout_backfill.facts r;`executions`horizons`unpriced`clock_mismatch`config_id!(3;3;2;3;enlist .qpipe.transform.eq_markout_outputs.config_id);
        "three executions at three horizons; e3's two totals across the close are unpriced; e2's three totals are on the wrong clock; one configuration"];
    .qunit.assertEquals[.qpipe.job.hdb_eq_markout_backfill.facts 0#r;(enlist `span)!enlist "empty window";"and an empty window says so"]};

/ --- a run ----------------------------------------------------------------------------------

.qt.qpipe.job.hdb_eq_markout_backfill.test_a_run_writes_every_component_into_eq_markout:{[t]
    .qpipe.job.hdb_eq_markout_backfill.init[.qt.qpipe.job.hdb_eq_markout_backfill.spec_for[`v1;.qt.qpipe.job.hdb_eq_markout_backfill.day[]0;.qt.qpipe.job.hdb_eq_markout_backfill.day[]1]];
    r:.qpipe.job.hdb_eq_markout_backfill.run[];
    .qunit.assertEquals[(r`state;count get `eq_markout);(`completed;18);"every component of every equity execution lands"];
    .qunit.assertEquals[cols get `eq_markout;cols .qetl.plant.shape `eq_markout;"in eq_markout's own columns, as the live job writes them"]};

.qt.qpipe.job.hdb_eq_markout_backfill.test_a_restatement_replaces_rather_than_duplicates:{[t]
    .qpipe.job.hdb_eq_markout_backfill.init[.qt.qpipe.job.hdb_eq_markout_backfill.spec_for[`v1;.qt.qpipe.job.hdb_eq_markout_backfill.day[]0;.qt.qpipe.job.hdb_eq_markout_backfill.day[]1]];
    .qpipe.job.hdb_eq_markout_backfill.run[];
    .qpipe.job.hdb_eq_markout_backfill.cleanup[];
    .qpipe.job.hdb_eq_markout_backfill.init[.qt.qpipe.job.hdb_eq_markout_backfill.spec_for[`v2;.qt.qpipe.job.hdb_eq_markout_backfill.day[]0;.qt.qpipe.job.hdb_eq_markout_backfill.day[]1]];
    .qpipe.job.hdb_eq_markout_backfill.run[];
    .qunit.assertEquals[count get `eq_markout;18;"still one row per component"]};

/ --- the live path, against a stand-in HDB ----------------------------------------------------

.qt.qpipe.job.hdb_eq_markout_backfill.sent:()

/ A handle that runs each query here, recording the bounds it was asked for.
.qt.qpipe.job.hdb_eq_markout_backfill.hdb:{[msg] .qt.qpipe.job.hdb_eq_markout_backfill.sent,:enlist 1_msg; value msg}

/ The HDB's two tables as it holds them: date-partitioned, market_data carrying its market.
.qt.qpipe.job.hdb_eq_markout_backfill.with_hdb:{[f]
    had:{[nm] $[nm in key `.; (1b;get nm); (0b;::)]} each `executions`market_data;
    `executions set update date:`date$source_time from .qpipe.source.hdb_eq_markouts.raw_executions[];
    `market_data set .qt.support.stand_in_market_data .qpipe.source.hdb_eq_markouts.raw_market_data[];
    `.qt.qpipe.job.hdb_eq_markout_backfill.sent set ();
    r:@[f;::;{[e] `threw,e}];
    {[nm;k] $[first k; nm set last k; ![`.;();0b;enlist nm]]}'[`executions`market_data;had];
    r}

.qt.qpipe.job.hdb_eq_markout_backfill.test_a_window_sends_two_queries_reading_books_past_its_end:{[t]
    w:2026.02.25D14:00:00 2026.02.25D21:00:00;
    `.qt.qpipe.job.hdb_eq_markout_backfill.w set w;
    r:.qt.qpipe.job.hdb_eq_markout_backfill.with_hdb {.qpipe.source.hdb_eq_markouts.query[.qt.qpipe.job.hdb_eq_markout_backfill.hdb;.qt.qpipe.job.hdb_eq_markout_backfill.w 0;.qt.qpipe.job.hdb_eq_markout_backfill.w 1]};
    .qunit.assertEquals[count .qt.qpipe.job.hdb_eq_markout_backfill.sent;2;"one query for the executions, one for the books"];
    .qunit.assertEquals[.qt.qpipe.job.hdb_eq_markout_backfill.sent 0;w;"executions over the window itself"];
    a:first .qt.qpipe.job.hdb_eq_markout_backfill.sent 1;
    spec:.qpipe.transform.eq_markout_outputs.job_spec;
    .qunit.assertEquals[a`read_from;(w 0)-spec`max_age;"no book older than the reference's max_age before the window's start (the grid has no negative horizon)"];
    .qunit.assertEquals[(a`skew;a`venues);(1;.qetl.venue.venues_of `equity);"a day either side, the equity venues only"];
    .qunit.assertEquals[asc distinct (a`probes)`sym;`AAPL`META;"only the symbols the executions trade"];
    .qunit.assertEquals[key r;`executions`market_data;"both inputs, raw - the scoring is the transform's"];
    .qunit.assertEquals[asc exec distinct fill_id from r`executions;`e1`e2`e3;"the equity venue's fills only: the FX fill of the shared table is not read"];
    .qunit.assertEquals[asc distinct (r`market_data)`source;enlist `XNAS;"and its books only"];
    .qunit.assertEquals[count .qt.qpipe.job.hdb_eq_markout_backfill.scored r;18;"the window's three equity executions, scored"]};

.qt.qpipe.job.hdb_eq_markout_backfill.test_the_live_path_scores_exactly_as_the_fixture_does:{[t]
    r:.qt.qpipe.job.hdb_eq_markout_backfill.with_hdb {.qpipe.source.hdb_eq_markouts.query[.qt.qpipe.job.hdb_eq_markout_backfill.hdb;(.qt.qpipe.job.hdb_eq_markout_backfill.day[])0;(.qt.qpipe.job.hdb_eq_markout_backfill.day[])1]};
    .qunit.assertTrue[.qetl.source.validate[`hdb_eq_markouts;r];"the live inputs satisfy both contracts"];
    .qunit.assertEquals[.qt.qpipe.job.hdb_eq_markout_backfill.scored[r];.qt.qpipe.job.hdb_eq_markout_backfill.scored[.qpipe.source.hdb_eq_markouts.fixture[]];"live and fixture agree"]};

.qt.qpipe.job.hdb_eq_markout_backfill.test_only_the_books_the_symbols_need_are_read:{[t]
    / AAPL and META fills need AAPL and META; an MSFT book on the same venue exists and is not read
    extra:{
        `market_data set .qt.support.stand_in_market_data (.qpipe.source.hdb_eq_markouts.raw_market_data[]),
            update sym:`MSFT from 2#.qpipe.source.hdb_eq_markouts.raw_market_data[];
        `executions set update date:`date$source_time from .qpipe.source.hdb_eq_markouts.raw_executions[];
        .qpipe.source.hdb_eq_markouts.query[.qt.qpipe.job.hdb_eq_markout_backfill.hdb;2026.02.25D14:00:00;2026.02.25D21:00:00]};
    `.qt.qpipe.job.hdb_eq_markout_backfill.extra set extra;
    r:.qt.qpipe.job.hdb_eq_markout_backfill.with_hdb {.qt.qpipe.job.hdb_eq_markout_backfill.extra[]};
    .qunit.assertFalse[`MSFT in distinct (r`market_data)`sym;"a symbol no execution trades"]};

/ One AAPL buy at 15:00:03 and the books given: the stand-in HDB's two tables, then the query over 15:00 to 16:00.
.qt.qpipe.job.hdb_eq_markout_backfill.early_query:{[times]
    `executions set ([] date:enlist 2026.02.25; source_time:enlist 2026.02.25D15:00:03; venue:enlist `XNAS; sym:enlist `AAPL;
        side:enlist 1; size:enlist 100f; price:enlist 271.5; fill_id:enlist `n1; clock:enlist `venue);
    `market_data set .qt.support.stand_in_market_data ([] source_time:times; market:(count times)#`equity;
        sym:(count times)#`AAPL; source:(count times)#`XNAS; bid:(count times)#271.25; ask:(count times)#271.5; clock:(count times)#`venue);
    .qpipe.source.hdb_eq_markouts.query[.qt.qpipe.job.hdb_eq_markout_backfill.hdb;2026.02.25D15:00:00;2026.02.25D16:00:00]}

.qt.qpipe.job.hdb_eq_markout_backfill.test_an_execution_early_in_a_window_is_priced_by_the_book_just_before_it:{[t]
    / the AAPL book live at 15:00:03 was set at 14:59:59 - before the window, within max_age
    `.qt.qpipe.job.hdb_eq_markout_backfill.times set 2026.02.25D14:59:59 2026.02.25D15:30:00;
    r:.qt.qpipe.job.hdb_eq_markout_backfill.with_hdb {.qt.qpipe.job.hdb_eq_markout_backfill.early_query[.qt.qpipe.job.hdb_eq_markout_backfill.times]};
    s:select from .qt.qpipe.job.hdb_eq_markout_backfill.scored r where kind=`execution, horizon=0D00:00:05;
    .qunit.assertEquals[s`ref_t0;enlist 271.375;"priced against the 14:59:59 mid (271.375), not null"]};

.qt.qpipe.job.hdb_eq_markout_backfill.test_the_book_before_a_window_is_bounded_by_the_references_max_age:{[t]
    `.qt.qpipe.job.hdb_eq_markout_backfill.times set enlist 2026.02.25D14:58:00;
    r:.qt.qpipe.job.hdb_eq_markout_backfill.with_hdb {.qt.qpipe.job.hdb_eq_markout_backfill.early_query[.qt.qpipe.job.hdb_eq_markout_backfill.times]};
    .qunit.assertEquals[count r`market_data;0;"a book two minutes before the window is past the minute max_age: scoring would ignore it, so it is not fetched"]};

.qt.qpipe.job.hdb_eq_markout_backfill.test_a_window_without_executions_does_not_read_books:{[t]
    r:.qt.qpipe.job.hdb_eq_markout_backfill.with_hdb {.qpipe.source.hdb_eq_markouts.query[.qt.qpipe.job.hdb_eq_markout_backfill.hdb;2026.02.25D12:00:00;2026.02.25D13:00:00]};
    .qunit.assertEquals[(count .qt.qpipe.job.hdb_eq_markout_backfill.sent;count r`executions;count r`market_data);(1;0;0);"no executions, no book query"];
    .qunit.assertTrue[.qetl.source.validate[`hdb_eq_markouts;r];"and both inputs still in their contracts' shape"]};

/ --- readiness: reference data that has not landed is not a scored window (#1927) ------------

/ Run the day with the source's fixture replaced by `f`, then put the real one back.
.qt.qpipe.job.hdb_eq_markout_backfill.run_with:{[f]
    keep:(.qetl.source.def `hdb_eq_markouts)`fixture;
    .[`.qetl.source.sources;(`hdb_eq_markouts;`fixture);:;f];
    d:.qt.qpipe.job.hdb_eq_markout_backfill.day[];
    .qpipe.job.hdb_eq_markout_backfill.init[.qt.qpipe.job.hdb_eq_markout_backfill.spec_for[`v1;d 0;d 1]];
    r:@[{.qpipe.job.hdb_eq_markout_backfill.run[]};::;{[e] `threw,e}];
    .qpipe.job.hdb_eq_markout_backfill.cleanup[];
    .[`.qetl.source.sources;(`hdb_eq_markouts;`fixture);:;keep];
    if[`threw~first r; 'last r];
    r}

/ Executions with no books at all, as the HDB is before market_data lands.
.qt.qpipe.job.hdb_eq_markout_backfill.no_books:{[]
    `executions`market_data!((.qpipe.source.hdb_eq_markouts.fixture[])`executions;0#.qpipe.source.hdb_eq_markouts.supporting`market_data)}

.qt.qpipe.job.hdb_eq_markout_backfill.covered:{[]
    v:.qetl.source.fixture_version `v1;
    .qetl.coverage.is_covered[`eq_markout;`;v;.z.p;2026.02.25D15:00:00;2026.02.25D16:00:00]}

.qt.qpipe.job.hdb_eq_markout_backfill.test_a_window_with_executions_and_no_books_is_not_claimed_and_replans_once_they_land:{[t]
    r:.qt.qpipe.job.hdb_eq_markout_backfill.run_with[.qt.qpipe.job.hdb_eq_markout_backfill.no_books];
    .qunit.assertEquals[(r`state;r`windows_failed;count get `eq_markout);(`partial;2;0);
        "the 15:00 and 20:00 windows, the two with fills, are refused, nothing is written, and the run says it is partial"];
    .qunit.assertFalse[.qt.qpipe.job.hdb_eq_markout_backfill.covered[];"no coverage for the window that could not be scored"];
    r2:.qt.qpipe.job.hdb_eq_markout_backfill.run_with[.qpipe.source.hdb_eq_markouts.fixture];
    .qunit.assertEquals[(r2`state;r2`windows_completed;count get `eq_markout);(`completed;2;18);
        "the books landed: only those two windows replan, at the same version, and are scored"];
    .qunit.assertEquals[count select from get `eq_markout where status=`unavailable,kind=`total;2;
        "scored for real: only e3's two totals across the close are unavailable"]};

.qt.qpipe.job.hdb_eq_markout_backfill.test_a_window_with_no_executions_is_still_covered:{[t]
    none:{`executions`market_data!(0#((.qpipe.source.hdb_eq_markouts.fixture[])`executions);0#.qpipe.source.hdb_eq_markouts.supporting`market_data)};
    r:.qt.qpipe.job.hdb_eq_markout_backfill.run_with[none];
    .qunit.assertEquals[(r`state;r`windows_failed;count get `eq_markout);(`completed;0;0);"nothing to score is not a missing reference"]};

.qt.qpipe.job.hdb_eq_markout_backfill.test_executions_with_no_books_are_not_ready:{[t]
    b:.qt.qpipe.job.hdb_eq_markout_backfill.no_books[];
    .qunit.assertEquals[count .qpipe.source.hdb_eq_markouts.ready[b;2026.02.25D00:00;2026.02.26D00:00];1;
        "equity executions and no books at all: one unmet requirement"];
    .qunit.assertEquals[.qpipe.source.hdb_eq_markouts.ready[.qpipe.source.hdb_eq_markouts.fixture[];2026.02.25D00:00;2026.02.26D00:00];();
        "and with the books, nothing is unmet"]};

/ --- readiness: the window's end plus the furthest horizon must be inside the books -----

.qt.qpipe.job.hdb_eq_markout_backfill.test_complete_through_is_the_latest_equity_book_less_the_furthest_horizon:{[t]
    r:.qt.qpipe.job.hdb_eq_markout_backfill.with_hdb {.qpipe.source.hdb_eq_markouts.complete_through[.qt.qpipe.job.hdb_eq_markout_backfill.hdb]};
    .qunit.assertEquals[r;2026.02.25D21:00:20-0D00:01:00;"the last equity book is at 21:00:20 and the furthest horizon is a minute"]};

.qt.qpipe.job.hdb_eq_markout_backfill.test_a_newer_fx_book_does_not_vouch_for_equities:{[t]
    f:{`market_data set .qt.support.stand_in_market_data (.qpipe.source.hdb_eq_markouts.raw_market_data[]),
            ([] source_time:enlist 2026.02.25D23:00:00; market:enlist `fx; sym:enlist `EURUSD; source:enlist `a;
                bid:enlist 1.1; ask:enlist 1.2; clock:enlist `receipt);
        .qpipe.source.hdb_eq_markouts.complete_through[.qt.qpipe.job.hdb_eq_markout_backfill.hdb]};
    `.qt.qpipe.job.hdb_eq_markout_backfill.fxnewer set f;
    r:.qt.qpipe.job.hdb_eq_markout_backfill.with_hdb {.qt.qpipe.job.hdb_eq_markout_backfill.fxnewer[]};
    .qunit.assertEquals[r;2026.02.25D21:00:20-0D00:01:00;"an FX book at 23:00 says nothing about whether the equity books have landed"]};

.qt.qpipe.job.hdb_eq_markout_backfill.test_an_hdb_without_equity_books_vouches_for_nothing:{[t]
    f:{`market_data set 0#.qt.support.stand_in_market_data .qpipe.source.hdb_eq_markouts.raw_market_data[];
        .qpipe.source.hdb_eq_markouts.complete_through[.qt.qpipe.job.hdb_eq_markout_backfill.hdb]};
    `.qt.qpipe.job.hdb_eq_markout_backfill.empty set f;
    r:.qt.qpipe.job.hdb_eq_markout_backfill.with_hdb {.qt.qpipe.job.hdb_eq_markout_backfill.empty[]};
    .qunit.assertEquals[r;-0Wp;"no book yet: nothing is complete, not a wrapped far-future instant"]};

/ A run pins the job spec's snapshot before it calculates, and every row names it (#1999).
.qt.qpipe.job.hdb_eq_markout_backfill.test_a_run_pins_the_snapshot_its_rows_name:{[t]
    .qpipe.job.hdb_eq_markout_backfill.init[.qt.qpipe.job.hdb_eq_markout_backfill.spec_for[`v1;.qt.qpipe.job.hdb_eq_markout_backfill.day[]0;.qt.qpipe.job.hdb_eq_markout_backfill.day[]1]];
    r:.qpipe.job.hdb_eq_markout_backfill.run[];
    id:.qpipe.transform.eq_markout_outputs.config_id;
    .qunit.assertEquals[(r`state;distinct (get `eq_markout)`config_id;(.qetl.snapshot.list[`])`snapshot_id);(`completed;enlist id;enlist id);"the one snapshot, recorded by the run, named by every row"]};
