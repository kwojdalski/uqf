/ test_bounded_batching.q - compute batching inside a bounded window (.qetl.job.bounded.batching, #1921).
/ .
/ Three things are proven. The PLANNER is pure: whole groups, in key order, never split, refused
/ when one is over the limit. The DECLARATION is refused by name when it is wrong. And a WINDOW
/ run batch by batch writes what an unbatched run writes - for FX markouts, a multi-leg
/ decomposition and a missing reference included - and cannot leave half a window behind.
/ .
/ The markout worker ships unbatched; these tests switch the declaration on for one test and back
/ off, so they say what batching must do to that worker without changing what it does.

.qt.qetl.job.bounded.batching.worker:`hdb_fx_markouts_backfill

.qt.qetl.job.bounded.batching.spec:{[mr;mg] `group_by`max_rows`max_groups`context!(enlist `sym;mr;mg;(::))}

/ The one markout window: the fixture's day.
.qt.qetl.job.bounded.batching.day:{[] (2026.09.17D00:00:00.000000000;2026.09.18D00:00:00.000000000)}

/ A worker's declaration as define would validate it, over a source: the error text, or the
/ normalised dictionary.
.qt.qetl.job.bounded.batching.required:{[source;b]
    @[{.qetl.job.bounded.batching.require[`probe;x]};`source`batching!(source;b);{[e] e}]}

/ Turn the markout worker's batching on (a normalised dictionary) or off ((::)), as its config
/ stores it.
.qt.qetl.job.bounded.batching.set_batching:{[v]
    d:.qetl.job.bounded.def `hdb_fx_markouts_backfill;
    d[`batching]:v;
    `.qetl.job.bounded.worker_cfg upsert (`hdb_fx_markouts_backfill,d cols value .qetl.job.bounded.worker_cfg);
    v}

/ The books a batch's executions need: their legs, as the source's own query derives them.
.qt.qetl.job.bounded.batching.legs_context:{[sup;rows]
    pairs:distinct raze .qpipe.transform.fx_markout.legs_of[.qpipe.transform.fx_markout_outputs.job_spec;rows];
    enlist[`market_data]!enlist select from sup`market_data where sym in pairs}

/ What each batch was given, for the tests that look.
.qt.qetl.job.bounded.batching.seen:()
.qt.qetl.job.bounded.batching.recording_context:{[sup;rows]
    r:.qt.qetl.job.bounded.batching.legs_context[sup;rows];
    .qt.qetl.job.bounded.batching.seen,:enlist (asc distinct rows`sym;count (r`market_data));
    r}

.qt.qetl.job.bounded.batching.failing_context:{[sup;rows]
    if[`USDPLN in rows`sym; 'boom];
    .qt.qetl.job.bounded.batching.legs_context[sup;rows]}

/ The worker's own fetch, kept so a test that feeds the window by hand can put it back.
.qt.qetl.job.bounded.batching.fetch_kept:()

/ Make the markout worker's fetch return `inputs` as the window (what a live read would), with the
/ source's own readiness stage still between fetch and transform.
.qt.qetl.job.bounded.batching.feed:{[inputs]
    if[0=count .qt.qetl.job.bounded.batching.fetch_kept;
        `.qt.qetl.job.bounded.batching.fetch_kept set enlist get `.qpipe.job.hdb_fx_markouts_backfill.fetch];
    / the executions are cut to the window as a read cuts them; the books are context and are not
    `.qpipe.job.hdb_fx_markouts_backfill.fetch set {[inputs;from_ts;to_ts]
        w:@[inputs;`executions;{[e;a;b] select from e where source_time>=a, source_time<b}[;from_ts;to_ts]];
        `state`result`attempts!(`ok;w;1)}[inputs];
    inputs}

/ Has the ledger claimed the hour that holds the fixture's executions (10:00-11:00)? Asked of the
/ ledger itself: coverage is recorded under <version>~fixture here, and the other hours hold no
/ executions and are claimed as empty, so "any row" would say nothing.
.qt.qetl.job.bounded.batching.claimed:{[]
    0<count select from .qetl.coverage.ledger[] where dataset=`fx_markout,
        range_from=2026.09.17D10:00:00.000000000, range_to=2026.09.17D11:00:00.000000000}

/ One run of the markout worker over the day; its published table, sorted by identity.
.qt.qetl.job.bounded.batching.run_day:{[]
    d:.qt.qetl.job.bounded.batching.day[];
    .qpipe.job.hdb_fx_markouts_backfill.init[`source_version`range_from`range_to!(`v1;d 0;d 1)];
    r:.qpipe.job.hdb_fx_markouts_backfill.run[];
    r}

.qt.qetl.job.bounded.batching.published:{[]
    `venue`fill_id`horizon`kind`step xasc get `fx_markout}

.qt.qetl.job.bounded.batching.beforeNamespace_isolate:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    system"mkdir -p build/test-status";
    }

.qt.qetl.job.bounded.batching.setUp_fresh:{[]
    .qt.support.reset_coverage_ledger[];
    .qetl.cfg.reset[];
    .qetl.cfg.set_layers[()!();()!();()!()];
    setenv[`UQF_DRY_RUN;""];
    setenv[`UQF_SOURCE_CRED_HDB_FX_MARKOUTS;""];
    .qetl.job.bounded.state.release_lock `hdb_fx_markouts_backfill;
    .qetl.job.bounded.state.clear_checkpoint `hdb_fx_markouts_backfill;
    `fx_markout set 0#.qetl.plant.shape `fx_markout;
    `.qt.qetl.job.bounded.batching.seen set ();
    }

.qt.qetl.job.bounded.batching.tearDown_restore:{[]
    .qpipe.job.hdb_fx_markouts_backfill.cleanup[];
    if[count .qt.qetl.job.bounded.batching.fetch_kept;
        `.qpipe.job.hdb_fx_markouts_backfill.fetch set first .qt.qetl.job.bounded.batching.fetch_kept;
        `.qt.qetl.job.bounded.batching.fetch_kept set ()];
    .qt.qetl.job.bounded.batching.set_batching[(::)];
    }

/ --- the planner -----------------------------------------------------------------------------

.qt.qetl.job.bounded.batching.test_a_plan_packs_whole_groups_in_key_order:{[t]
    p:.qetl.job.bounded.batching.plan[.qt.qetl.job.bounded.batching.spec[3;0W];([] sym:`b`a`b`a`c)];
    .qunit.assertEquals[p;(1 3;0 2 4);"a (two rows) fits; b would overrun three rows, so it starts the next batch and c joins it"]};

.qt.qetl.job.bounded.batching.test_one_group_per_batch_when_max_groups_is_one:{[t]
    p:.qetl.job.bounded.batching.plan[.qt.qetl.job.bounded.batching.spec[100;1];([] sym:`b`a`b`a`c)];
    .qunit.assertEquals[p;(1 3;0 2;enlist 4);"a batch per group, in key order"]};

.qt.qetl.job.bounded.batching.test_max_groups_caps_the_groups_in_a_batch_whatever_max_rows_allows:{[t]
    p:.qetl.job.bounded.batching.plan[.qt.qetl.job.bounded.batching.spec[100;2];([] sym:`b`a`b`a`c)];
    .qunit.assertEquals[p;(0 1 2 3;enlist 4);"two groups, then the third"]};

.qt.qetl.job.bounded.batching.test_every_row_lands_in_exactly_one_batch_in_the_windows_order:{[t]
    rows:([] sym:`c`a`b`a`c`b`a);
    p:.qetl.job.bounded.batching.plan[.qt.qetl.job.bounded.batching.spec[3;0W];rows];
    .qunit.assertEquals[asc raze p;til count rows;"nothing dropped, nothing twice"];
    .qunit.assertTrue[all {x~asc x} each p;"each batch keeps the order the window returned its rows in"]};

.qt.qetl.job.bounded.batching.test_a_group_is_never_split_across_batches:{[t]
    rows:([] sym:`a`b`c`a`b`c`a`b`c`a);
    p:.qetl.job.bounded.batching.plan[.qt.qetl.job.bounded.batching.spec[5;0W];rows];
    homes:{[rows;p;s] where {[rows;s;b] any s=rows[`sym] b}[rows;s] each p}[rows;p] each `a`b`c;
    .qunit.assertTrue[all 1=count each homes;"every symbol sits in one batch"]};

.qt.qetl.job.bounded.batching.test_the_same_groups_plan_the_same_whatever_order_the_rows_arrive:{[t]
    a:([] sym:`b`a`c`a`b`c; n:til 6);
    b:reverse a;
    shape:{[spec;rows] asc {asc distinct x`sym} each rows (.qetl.job.bounded.batching.plan[spec;rows])};
    s:.qt.qetl.job.bounded.batching.spec[2;0W];
    .qunit.assertEquals[shape[s;a];shape[s;b];"a plan is the groups', not the arrival order's"]};

.qt.qetl.job.bounded.batching.test_a_composite_key_is_one_group_when_every_column_agrees:{[t]
    rows:([] sym:`a`a`b`a; venue:`x`y`x`x);
    spec:`group_by`max_rows`max_groups`context!(`sym`venue;2;0W;(::));
    .qunit.assertEquals[.qetl.job.bounded.batching.plan[spec;rows];(0 3;1 2);"(a;x) is rows 0 and 3 and stays together"]};

.qt.qetl.job.bounded.batching.test_keys_that_would_join_to_one_string_stay_distinct:{[t]
    rows:([] x:`$("a|b";"a"); y:`$("c";"b|c"));
    spec:`group_by`max_rows`max_groups`context!(`x`y;1;0W;(::));
    .qunit.assertEquals[count .qetl.job.bounded.batching.plan[spec;rows];2;"two groups, not one that a delimiter merged"]};

.qt.qetl.job.bounded.batching.test_a_window_with_no_rows_plans_no_batches:{[t]
    .qunit.assertEquals[.qetl.job.bounded.batching.plan[.qt.qetl.job.bounded.batching.spec[3;0W];([] sym:`symbol$())];();"nothing to batch"]};

.qt.qetl.job.bounded.batching.test_a_group_over_max_rows_is_refused_naming_the_group_and_the_ways_out:{[t]
    .qunit.assertThrows[.qetl.job.bounded.batching.plan[.qt.qetl.job.bounded.batching.spec[1;0W];];([] sym:`b`a`b`a`c);
        "batching: the group sym=`a has 2 rows, over max_rows 1 - a group cannot be split: raise max_rows, shorten the worker's width*";"it is not split, and not silently run over the limit"]};

/ --- the declaration ---------------------------------------------------------------------------

.qt.qetl.job.bounded.batching.test_a_declaration_is_normalised:{[t]
    r:.qt.qetl.job.bounded.batching.required[`hdb_fx_markouts;`group_by`max_rows!(`sym;500)];
    .qunit.assertEquals[r;`group_by`max_rows`max_groups`context!(enlist `sym;500;0W;(::));"one column becomes a vector, max_groups defaults to no limit, no context"]};

.qt.qetl.job.bounded.batching.test_a_malformed_declaration_is_refused_by_name:{[t]
    req:.qt.qetl.job.bounded.batching.required;
    .qunit.assertTrue[(req[`hdb_fx_markouts;`sym]) like "define: probe's batching must be a dictionary*";"not a dictionary"];
    .qunit.assertTrue[(req[`hdb_fx_markouts;`group_by`max_rows`speed!(`sym;5;1)]) like "*has speed, which it does not take*";"an unknown key"];
    .qunit.assertTrue[(req[`hdb_fx_markouts;enlist[`group_by]!enlist `sym]) like "*needs max_rows";"max_rows is required"];
    .qunit.assertTrue[(req[`hdb_fx_markouts;`group_by`max_rows!(`nope;5)]) like "*group_by names nope, which source hdb_fx_markouts does not return*";"a column the source does not return"];
    .qunit.assertTrue[(req[`hdb_fx_markouts;`group_by`max_rows!(`sym`sym;5)]) like "*names a column more than once";"a repeated column"];
    .qunit.assertTrue[(req[`hdb_fx_markouts;`group_by`max_rows!(`sym;0)]) like "*max_rows must be a positive, finite long*";"zero rows"];
    .qunit.assertTrue[(req[`hdb_fx_markouts;`group_by`max_rows!(`sym;0W)]) like "*max_rows must be a positive, finite long*";"no limit is not a limit"];
    .qunit.assertTrue[(req[`hdb_fx_markouts;`group_by`max_rows!(`sym;5i)]) like "*max_rows must be a positive, finite long*";"an int"];
    .qunit.assertTrue[(req[`hdb_fx_markouts;`group_by`max_rows`max_groups!(`sym;5;0)]) like "*max_groups must be a positive long*";"zero groups"]};

.qt.qetl.job.bounded.batching.test_a_context_is_for_a_source_with_supporting_inputs_and_takes_two_arguments:{[t]
    req:.qt.qetl.job.bounded.batching.required;
    ok:`group_by`max_rows`context!(`sym;5;.qt.qetl.job.bounded.batching.legs_context);
    .qunit.assertTrue[99h=type req[`hdb_fx_markouts;ok];"the markout source has supporting inputs"];
    .qunit.assertTrue[(req[`demo_deals;`group_by`max_rows`context!(`sym;5;{[s;r] s})]) like "*context narrows supporting inputs, and source demo_deals declares none";"a source with none has nothing to narrow"];
    .qunit.assertTrue[(req[`hdb_fx_markouts;`group_by`max_rows`context!(`sym;5;{[s] s})]) like "*context must take exactly two arguments*";"one argument"];
    .qunit.assertTrue[(req[`hdb_fx_markouts;`group_by`max_rows`context!(`sym;5;"x")]) like "*context must be a function*";"not a function"]};

/ A declaration read back with .qetl.job.bounded.def carries (::) for batching, and defining a worker
/ from it - as the tests of other workers do, to make a variant - is not a malformed declaration.
.qt.qetl.job.bounded.batching.test_a_stored_declaration_can_define_another_worker:{[t]
    d:@[.qetl.job.bounded.def `demo_deals_backfill;`dataset;:;`batching_roundtrip];
    .qetl.job.bounded.define[`batching_roundtrip_worker;(`ns`procname`note) _ d];
    .qunit.assertEquals[(.qetl.job.bounded.def `batching_roundtrip_worker)`batching;(::);"still off"];
    on:@[d;`batching;:;.qt.qetl.job.bounded.batching.spec[500;2]];
    on:@[on;`dataset;:;`batching_roundtrip_on];
    .qetl.job.bounded.define[`batching_roundtrip_on_worker;(`ns`procname`note) _ on];
    stored:(.qetl.job.bounded.def `batching_roundtrip_on_worker)`batching;
    .qunit.assertEquals[(stored`group_by;stored`max_rows;stored`max_groups);(enlist `sym;500;2);"declared, it is validated and normalised before it is stored"]};

.qt.qetl.job.bounded.batching.test_a_worker_that_declares_nothing_is_stored_without_batching:{[t]
    .qunit.assertEquals[(.qetl.job.bounded.def `hdb_fx_markouts_backfill)`batching;(::);"batching is off unless declared"];
    .qunit.assertEquals[(.qetl.job.bounded.def `demo_deals_backfill)`batching;(::);"for every shipped worker"]};

/ --- a window, batch by batch ------------------------------------------------------------------

.qt.qetl.job.bounded.batching.test_a_batched_run_publishes_what_an_unbatched_run_publishes:{[t]
    r0:.qt.qetl.job.bounded.batching.run_day[];
    plain:.qt.qetl.job.bounded.batching.published[];
    .qpipe.job.hdb_fx_markouts_backfill.cleanup[];
    `fx_markout set 0#.qetl.plant.shape `fx_markout;
    .qt.support.reset_coverage_ledger[];
    .qt.qetl.job.bounded.batching.set_batching[.qt.qetl.job.bounded.batching.spec[1000;1]];
    r1:.qt.qetl.job.bounded.batching.run_day[];
    .qunit.assertEquals[(r0`state;r1`state);`completed`completed;"both complete"];
    .qunit.assertEquals[.qt.qetl.job.bounded.batching.published[];plain;"the same rows by identity, one batch per execution symbol"];
    .qunit.assertEquals[count plain;36;"including USDPLN's legs: EURUSD inverted and EURPLN"]};

.qt.qetl.job.bounded.batching.test_grouped_symbols_publish_the_same_rows:{[t]
    .qt.qetl.job.bounded.batching.run_day[];
    plain:.qt.qetl.job.bounded.batching.published[];
    .qpipe.job.hdb_fx_markouts_backfill.cleanup[];
    `fx_markout set 0#.qetl.plant.shape `fx_markout;
    .qt.support.reset_coverage_ledger[];
    .qt.qetl.job.bounded.batching.set_batching[.qt.qetl.job.bounded.batching.spec[1000;2]];
    .qt.qetl.job.bounded.batching.run_day[];
    .qunit.assertEquals[.qt.qetl.job.bounded.batching.published[];plain;"two symbols a batch: the same rows"]};

.qt.qetl.job.bounded.batching.test_each_batch_is_given_only_the_books_its_executions_need:{[t]
    spec:.qt.qetl.job.bounded.batching.spec[1000;1];
    spec[`context]:.qt.qetl.job.bounded.batching.recording_context;
    .qt.qetl.job.bounded.batching.set_batching[spec];
    .qt.qetl.job.bounded.batching.run_day[];
    seen:.qt.qetl.job.bounded.batching.seen;
    .qunit.assertEquals[count seen;3;"EURUSD, USDPLN and the unroutable crypto fill: a batch each"];
    .qunit.assertEquals[seen[;1];0 9 12;"the crypto fill needs no book, EURUSD only its own, USDPLN both of its legs' - of the window's 12"]};

.qt.qetl.job.bounded.batching.test_narrowed_batches_publish_the_same_rows_as_an_unbatched_run:{[t]
    .qt.qetl.job.bounded.batching.run_day[];
    plain:.qt.qetl.job.bounded.batching.published[];
    .qpipe.job.hdb_fx_markouts_backfill.cleanup[];
    `fx_markout set 0#.qetl.plant.shape `fx_markout;
    .qt.support.reset_coverage_ledger[];
    spec:.qt.qetl.job.bounded.batching.spec[1000;1];
    spec[`context]:.qt.qetl.job.bounded.batching.legs_context;
    .qt.qetl.job.bounded.batching.set_batching[spec];
    .qt.qetl.job.bounded.batching.run_day[];
    .qunit.assertEquals[.qt.qetl.job.bounded.batching.published[];plain;"narrowing a batch's books to its legs changes nothing it publishes"]};

.qt.qetl.job.bounded.batching.test_a_missing_reference_is_scored_the_same_batched_or_not:{[t]
    / the EURPLN book is gone: USDPLN cannot be priced through it, however it is batched
    inputs:.qpipe.source.hdb_fx_markouts.fixture[];
    inputs[`market_data]:select from inputs`market_data where not sym=`EURPLN;
    plain:.qetl.job.bounded.transform_batch[`hdb_fx_markouts_backfill;inputs];
    .qt.qetl.job.bounded.batching.set_batching[.qt.qetl.job.bounded.batching.spec[1000;1]];
    batched:.qetl.job.bounded.batching.apply[`hdb_fx_markouts_backfill;inputs];
    k:`venue`fill_id`horizon`kind`step;
    .qunit.assertEquals[k xasc batched;k xasc plain;"unavailable in both, and the rows that are priced are untouched"];
    .qunit.assertTrue[0<exec sum status=`unavailable from batched where kind=`total;"the gap is reported, not dropped"]};

.qt.qetl.job.bounded.batching.test_a_window_with_no_executions_is_transformed_as_one_and_keeps_the_outputs_shape:{[t]
    inputs:@[.qpipe.source.hdb_fx_markouts.fixture[];`executions;0#];
    .qt.qetl.job.bounded.batching.set_batching[.qt.qetl.job.bounded.batching.spec[1000;1]];
    .qunit.assertEquals[.qetl.job.bounded.batching.apply[`hdb_fx_markouts_backfill;inputs];.qetl.job.bounded.transform_batch[`hdb_fx_markouts_backfill;inputs];"as an unbatched window's empty output"]};

/ --- readiness (#1927) ---------------------------------------------------------------------------
/ The source says whether the window can be scored, between fetch and transform, over the WHOLE window.
/ Batches run inside the transform, so none starts on a window that is not ready, and a window is
/ claimed only after all of its batches.

.qt.qetl.job.bounded.batching.test_a_window_with_no_books_is_not_ready_so_no_batch_runs_and_nothing_is_claimed:{[t]
    inputs:.qpipe.source.hdb_fx_markouts.fixture[];
    inputs[`market_data]:0#inputs`market_data;
    .qt.qetl.job.bounded.batching.feed[inputs];
    spec:.qt.qetl.job.bounded.batching.spec[1000;1];
    spec[`context]:.qt.qetl.job.bounded.batching.recording_context;
    .qt.qetl.job.bounded.batching.set_batching[spec];
    r:.qt.qetl.job.bounded.batching.run_day[];
    .qunit.assertEquals[r`state;`partial;"the run ends partial, as an unbatched one does"];
    .qunit.assertEquals[count .qt.qetl.job.bounded.batching.seen;0;"no batch was started: the window failed its readiness check first"];
    .qunit.assertEquals[count get `fx_markout;0;"nothing was published"];
    .qunit.assertEquals[.qt.qetl.job.bounded.batching.claimed[];0b;"and the window was not claimed, so the next run plans it again"]};

.qt.qetl.job.bounded.batching.test_a_window_missing_one_legs_book_is_ready_and_claimed_as_an_unbatched_one_is:{[t]
    inputs:.qpipe.source.hdb_fx_markouts.fixture[];
    inputs[`market_data]:select from inputs`market_data where not sym=`EURPLN;
    .qt.qetl.job.bounded.batching.feed[inputs];
    r0:.qt.qetl.job.bounded.batching.run_day[];
    plain:.qt.qetl.job.bounded.batching.published[];
    plain_claimed:.qt.qetl.job.bounded.batching.claimed[];
    .qpipe.job.hdb_fx_markouts_backfill.cleanup[];
    `fx_markout set 0#.qetl.plant.shape `fx_markout;
    .qt.support.reset_coverage_ledger[];
    .qt.qetl.job.bounded.batching.set_batching[.qt.qetl.job.bounded.batching.spec[1000;1]];
    r1:.qt.qetl.job.bounded.batching.run_day[];
    .qunit.assertEquals[(r0`state;r1`state);`completed`completed;"a missing leg is an unavailable row, not an unready window, batched or not"];
    .qunit.assertEquals[.qt.qetl.job.bounded.batching.published[];plain;"the same rows, the unavailable ones included"];
    .qunit.assertEquals[(plain_claimed;.qt.qetl.job.bounded.batching.claimed[]);11b;"and claimed once all its batches passed - which also shows the ledger check can say yes"]};

/ --- failure -----------------------------------------------------------------------------------

.qt.qetl.job.bounded.batching.test_a_failing_batch_names_itself:{[t]
    spec:.qt.qetl.job.bounded.batching.spec[1000;1];
    spec[`context]:.qt.qetl.job.bounded.batching.failing_context;
    .qt.qetl.job.bounded.batching.set_batching[spec];
    .qunit.assertThrows[.qetl.job.bounded.batching.apply[`hdb_fx_markouts_backfill;];.qpipe.source.hdb_fx_markouts.fixture[];
        "batch * of 3 (1 rows): boom";"which batch, how many, how big, and why"]};

.qt.qetl.job.bounded.batching.test_a_failing_batch_fails_the_window_before_anything_is_written_or_covered:{[t]
    spec:.qt.qetl.job.bounded.batching.spec[1000;1];
    spec[`context]:.qt.qetl.job.bounded.batching.failing_context;
    .qt.qetl.job.bounded.batching.set_batching[spec];
    r:.qt.qetl.job.bounded.batching.run_day[];
    .qunit.assertTrue[not `completed~r`state;"the run did not complete"];
    .qunit.assertEquals[count get `fx_markout;0;"the batches that succeeded wrote nothing beside the one that did not"];
    .qunit.assertEquals[.qt.qetl.job.bounded.batching.claimed[];0b;"and the window is not covered"]};

.qt.qetl.job.bounded.batching.test_a_retry_after_a_failed_batch_writes_each_row_once:{[t]
    spec:.qt.qetl.job.bounded.batching.spec[1000;1];
    spec[`context]:.qt.qetl.job.bounded.batching.failing_context;
    .qt.qetl.job.bounded.batching.set_batching[spec];
    .qt.qetl.job.bounded.batching.run_day[];
    .qpipe.job.hdb_fx_markouts_backfill.cleanup[];
    spec[`context]:.qt.qetl.job.bounded.batching.legs_context;
    .qt.qetl.job.bounded.batching.set_batching[spec];
    r:.qt.qetl.job.bounded.batching.run_day[];
    .qunit.assertEquals[(r`state;count get `fx_markout);(`completed;36);"the retry completes and nothing is doubled"];
    .qunit.assertEquals[.qt.qetl.job.bounded.batching.claimed[];1b;"and now the window is claimed"]};

.qt.qetl.job.bounded.batching.test_a_group_over_the_limit_fails_the_window_with_the_refusal:{[t]
    .qt.qetl.job.bounded.batching.set_batching[.qt.qetl.job.bounded.batching.spec[1;1]];
    .qunit.assertThrows[.qetl.job.bounded.batching.apply[`hdb_fx_markouts_backfill;];
        enlist[`executions]!enlist .qpipe.source.hdb_fx_markouts.raw_executions[] , .qpipe.source.hdb_fx_markouts.raw_executions[];
        "batching: the group sym=`BTC-USDT has 2 rows, over max_rows 1*";"too big to batch: refused, not run over the limit"]};

/ --- the shipped workers stay as they were -------------------------------------------------------

.qt.qetl.job.bounded.batching.test_an_unbatched_worker_transforms_through_the_one_primitive:{[t]
    f:.qpipe.source.hdb_fx_markouts.fixture[];
    .qunit.assertEquals[.qetl.job.bounded.transform_window[`hdb_fx_markouts_backfill;f];.qetl.job.bounded.transform_batch[`hdb_fx_markouts_backfill;f];"no batching declared, no change"]};
