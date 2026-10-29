/ test_complete_through.q - a source says how far it can vouch for its data
/ (.qetl.source.complete_through_of, clip_to_complete), and planning clips to it
/ (#1187). The crypto_market_data shard-glob bound (#1186) is tested with that
/ source, in the cryptorust bundle (sidecars/cryptorust).


/ The bound the test source reports; moved by a test to model a feed advancing.
.qt.qetl.source.complete_through.bound:0Wp

.qt.qetl.source.complete_through.beforeNamespace_isolate:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status"];
    system"mkdir -p build/test-status";
    }

.qt.qetl.source.complete_through.beforeNamespace_define:{[]
    .qetl.source.define[`ct_src;
        `source`table_name`time_column`row_key`columns`types`query`fixture`tz`complete_through!
        (`ct_src;`ct_events;`time;`time;`time`px;"pf";
         {[h;range_from;range_to]
            ([] time:range_from+0D01*til "j"$(range_to-range_from)%0D01; px:1f)};
         {[] ([] time:enlist 2026.09.01D00:00; px:enlist 0f)};
         `UTC;
         {[h] .qt.qetl.source.complete_through.bound})];
    .qetl.transform.define[`ct_passthrough;`inputs`output`fn`examples!(
        enlist[`batch]!enlist ([] time:`timestamp$(); px:`float$());
        ([] time:`timestamp$(); px:`float$());
        {[batch] batch};
        enlist `inputs`expected!(
            enlist[`batch]!enlist ([] time:enlist 2026.09.01D00:00; px:enlist 1f);
            ([] time:enlist 2026.09.01D00:00; px:enlist 1f)))];
    .qetl.job.bounded.define[`ct_backfill;
        `source`dataset`width`transform!(`ct_src;`ct_out;1D;`ct_passthrough)];
    .qetl.job.bounded.define[`ct_bar_backfill;
        `source`dataset`width`transform`align!(`ct_src;`ct_bar_out;1D;`ct_passthrough;0D01:00)];
    }

.qt.qetl.source.complete_through.setUp_fresh:{[]
    .qt.support.reset_coverage_ledger[];
    .qetl.cfg.reset[];
    .qetl.cfg.set_layers[()!();()!();()!()];
    setenv[`UQF_DRY_RUN;""];
    .qt.qetl.source.complete_through.bound:0Wp;
    .qetl.job.bounded.state.release_lock `ct_backfill;
    .qetl.job.bounded.state.clear_checkpoint `ct_backfill;
    `ct_out set ([] time:`timestamp$(); px:`float$());
    }

.qt.qetl.source.complete_through.tearDown_release:{[]
    .qetl.job.bounded.write_state[`ct_backfill;`handle;0Ni];
    .qpipe.job.ct_backfill.cleanup[];}

.qt.qetl.source.complete_through.spec:{[] `source_version`range_from`range_to!(`v1;2026.09.01D00:00;2026.09.03D00:00)}

/ Init, then pretend a connection: the test has no credential, so init would
/ otherwise take the fixture path, which is never clipped and whose coverage
/ is recorded under `v1~fixture rather than `v1.
.qt.qetl.source.complete_through.init_live:{[]
    .qpipe.job.ct_backfill.init[.qt.qetl.source.complete_through.spec[]];
    .qetl.job.bounded.write_state[`ct_backfill;`source_version;`v1];
    .qetl.job.bounded.write_state[`ct_backfill;`handle;7i];
    .qetl.job.bounded.write_state[`ct_backfill;`live;1b]}

/ --- the clip --------------------------------------------------------------

.qt.qetl.source.complete_through.test_a_closed_source_is_never_clipped:{[t]
    .qunit.assertEquals[.qetl.source.clip_to_complete[`w;`ct_src;7i;1b;2026.09.03D00:00];2026.09.03D00:00;
        "an unbounded complete_through leaves the range alone"]};

.qt.qetl.source.complete_through.test_a_bound_inside_the_range_ends_it:{[t]
    .qt.qetl.source.complete_through.bound:2026.09.01D12:00;
    .qunit.assertEquals[.qetl.source.clip_to_complete[`w;`ct_src;7i;1b;2026.09.03D00:00];2026.09.01D12:00;
        "the range ends where the source can vouch"]};

.qt.qetl.source.complete_through.test_the_fixture_path_is_closed_data:{[t]
    .qt.qetl.source.complete_through.bound:2026.09.01D12:00;
    .qunit.assertEquals[.qetl.source.clip_to_complete[`w;`ct_src;0Ni;0b;2026.09.03D00:00];2026.09.03D00:00;
        "no handle, no source to be incomplete: the fixture is not clipped"]};

.qt.qetl.source.complete_through.test_a_null_bound_means_nothing_is_complete:{[t]
    .qt.qetl.source.complete_through.bound:0Np;
    .qunit.assertEquals[.qetl.source.clip_to_complete[`w;`ct_src;7i;1b;2026.09.03D00:00];-0Wp;
        "null is not unbounded"]};

.qt.qetl.source.complete_through.test_a_bound_that_is_not_a_timestamp_is_refused:{[t]
    .qt.qetl.source.complete_through.bound:5;
    .qunit.assertThrows[{.qetl.source.clip_to_complete[`w;`ct_src;7i;1b;x]};2026.09.03D00:00;
        "complete_through: ct_src returned type -7, not a timestamp atom";"named, not compared"]};

/ --- the declaration -------------------------------------------------------

.qt.qetl.source.complete_through.test_the_default_is_unbounded:{[t]
    .qunit.assertEquals[((.qetl.source.def `demo_deals)`complete_through) 0Ni;0Wp;"closed data is the default"]};

.qt.qetl.source.complete_through.test_a_complete_through_that_is_not_a_lambda_is_refused:{[t]
    d:`source`table_name`time_column`row_key`columns`types`query`fixture`tz`complete_through!
        (`ct_bad;`t;`time;`time;enlist `time;enlist "p";{[h;a;b] ()};{[] ()};`UTC;2026.09.01D00:00);
    .qunit.assertThrows[{.qetl.source.define[`ct_bad;x]};d;
        "define: ct_bad's complete_through must be a lambda*";"a timestamp is not a function"]};

.qt.qetl.source.complete_through.test_a_complete_through_of_the_wrong_arity_is_refused:{[t]
    d:`source`table_name`time_column`row_key`columns`types`query`fixture`tz`complete_through!
        (`ct_bad;`t;`time;`time;enlist `time;enlist "p";{[h;a;b] ()};{[] ()};`UTC;{[] 0Wp});
    .qunit.assertThrows[{.qetl.source.define[`ct_bad;x]};d;
        "define: ct_bad's complete_through must take exactly one argument*";"it needs the handle"]};

/ --- planning --------------------------------------------------------------

.qt.qetl.source.complete_through.test_planning_stops_at_the_bound:{[t]
    .qt.qetl.source.complete_through.bound:2026.09.01D12:00;
    .qt.qetl.source.complete_through.init_live[];
    w:.qpipe.job.ct_backfill.plan 0Np;
    .qunit.assertEquals[(w`range_from;w`range_to);(enlist 2026.09.01D00:00;enlist 2026.09.01D12:00);
        "one window, cut at the bound"]};

.qt.qetl.source.complete_through.test_an_aligned_worker_is_clipped_to_a_bar_edge:{[t]
    / #1477: a bound inside a bar would build that bar from part of its fills now and the rest later.
    .qt.qetl.source.complete_through.bound:2026.09.01D12:30;
    .qpipe.job.ct_bar_backfill.init[.qt.qetl.source.complete_through.spec[]];
    .qetl.job.bounded.write_state[`ct_bar_backfill;`source_version;`v1];
    .qetl.job.bounded.write_state[`ct_bar_backfill;`handle;7i];
    .qetl.job.bounded.write_state[`ct_bar_backfill;`live;1b];
    w:.qpipe.job.ct_bar_backfill.plan 0Np;
    .qetl.job.bounded.write_state[`ct_bar_backfill;`handle;0Ni];
    .qpipe.job.ct_bar_backfill.cleanup[];
    .qunit.assertEquals[(w`range_from;w`range_to);(enlist 2026.09.01D00:00;enlist 2026.09.01D12:00);
        "the partial hour stays uncovered until the source vouches for all of it"]};

.qt.qetl.source.complete_through.test_a_bound_behind_the_range_plans_nothing:{[t]
    .qt.qetl.source.complete_through.bound:2026.08.31D00:00;
    .qt.qetl.source.complete_through.init_live[];
    .qunit.assertEquals[count .qpipe.job.ct_backfill.plan 0Np;0;"nothing is complete yet"]};

.qt.qetl.source.complete_through.test_a_window_past_the_bound_is_never_covered:{[t]
    .qt.qetl.source.complete_through.bound:2026.09.01D12:00;
    .qt.qetl.source.complete_through.init_live[];
    r:.qpipe.job.ct_backfill.run[];
    .qunit.assertEquals[(r`state;r`windows_completed);(`completed;1);"the clipped window ran"];
    .qunit.assertTrue[.qetl.coverage.is_covered[`ct_out;`;`v1;.z.p;2026.09.01D00:00;2026.09.01D12:00];
        "the part before the bound is covered"];
    .qunit.assertEquals[.qetl.coverage.is_covered[`ct_out;`;`v1;.z.p;2026.09.01D00:00;2026.09.01D12:00:00.000000001];0b;
        "and not one nanosecond past it"];
    .qunit.assertEquals[.qetl.coverage.is_covered[`ct_out;`;`v1;.z.p;2026.09.01D00:00;2026.09.03D00:00];0b;
        "the requested range is not claimed"]};

.qt.qetl.source.complete_through.test_the_next_run_after_the_bound_advances_covers_the_rest:{[t]
    .qt.qetl.source.complete_through.bound:2026.09.01D12:00;
    .qt.qetl.source.complete_through.init_live[];
    .qpipe.job.ct_backfill.run[];
    .qetl.job.bounded.write_state[`ct_backfill;`handle;0Ni];
    .qpipe.job.ct_backfill.cleanup[];
    .qt.qetl.source.complete_through.bound:0Wp;
    .qt.qetl.source.complete_through.init_live[];
    r:.qpipe.job.ct_backfill.run[];
    .qunit.assertEquals[(r`state;r`windows_completed);(`completed;2);
        "the clipped remainder and the second day"];
    .qunit.assertTrue[.qetl.coverage.is_covered[`ct_out;`;`v1;.z.p;2026.09.01D00:00;2026.09.03D00:00];
        "the whole range, composed from the two runs"];
    .qunit.assertEquals[count value `ct_out;48;"every hour of the two days exactly once, none refetched"]};

/ --- plan mode agrees with the run (#1201) ------------------------------------

/ Run niladic `f` as a live worker would see it: a credential in the
/ environment and an opener that hands back a fake handle.
.qt.qetl.source.complete_through.with_live_credential:{[f]
    envname:.qetl.source.credential_var `ct_src;
    keep:.qetl.job.bounded.connect;
    setenv[`$envname;"fake:1"];
    `.qetl.job.bounded.connect set {[w] 7i};
    r:@[f;::;{[e] (`failed;e)}];
    `.qetl.job.bounded.connect set keep;
    setenv[`$envname;""];
    if[(0h=type r) and `failed~first r; 'last r];
    r}

.qt.qetl.source.complete_through.test_plan_only_clips_to_the_bound_exactly_as_a_run_does:{[t]
    .qt.qetl.source.complete_through.bound:2026.09.01D12:00;
    p:.qt.qetl.source.complete_through.with_live_credential[{.qetl.job.bounded.plan_only[`ct_backfill;.qt.qetl.source.complete_through.spec[]]}];
    .qunit.assertEquals[(p[`plan]`range_from;p[`plan]`range_to);(enlist 2026.09.01D00:00;enlist 2026.09.01D12:00);
        "plan mode lists the clipped window, not the whole requested day"];
    .qunit.assertEquals[null .qetl.job.bounded.read_state[`ct_backfill;`handle];1b;
        "the source it opened to ask is closed again"];
    .qt.qetl.source.complete_through.init_live[];
    r:.qpipe.job.ct_backfill.run[];
    .qunit.assertEquals[p`planned;r`windows_completed;"the run does exactly the windows plan listed"]};

.qt.qetl.source.complete_through.test_plan_only_on_the_fixture_is_not_clipped:{[t]
    .qt.qetl.source.complete_through.bound:2026.09.01D12:00;
    p:.qetl.job.bounded.plan_only[`ct_backfill;.qt.qetl.source.complete_through.spec[]];
    .qunit.assertEquals[(p[`plan]`range_from;p[`plan]`range_to);
        (2026.09.01D00:00 2026.09.02D00:00;2026.09.02D00:00 2026.09.03D00:00);
        "closed fixture data is complete however far the live bound says"]};

.qt.qetl.source.complete_through.test_a_live_run_with_no_handle_is_refused_not_read_as_closed:{[t]
    .qunit.assertThrows[{.qetl.source.complete_through_of[`ct_src;0Ni;1b]};(::);
        "complete_through: ct_src is live but has no open handle to ask";"null is not closed data"]};
