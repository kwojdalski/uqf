/ test_source_readiness.q - a source says what a window cannot be scored without, and a
/ window that lacks it is neither published nor claimed (#1927). The declaration is
/ validated when the source registers; the stage between fetch and transform is what
/ turns an unmet requirement into an unclaimed window.

/ A source declaration with `extra` appended to the required keys.
.qt.qetl.source.readiness.decl:{[extra]
    base:`source`table_name`time_column`row_key`columns`types`query`fixture`tz!
        (`rd_bad;`t;`time;`time;enlist `time;enlist "p";{[h;a;b] ()};{[] ()};`UTC);
    base,extra}

/ stage_ready's inputs for a source whose hook is `hook`: the window state a stage receives.
.qt.qetl.source.readiness.state_for:{[hook;batch]
    .qetl.source.define[`rd_src;
        `source`table_name`time_column`row_key`columns`types`query`fixture`tz`ready!
        (`rd_src;`t;`time;`time;enlist `time;enlist "p";{[h;a;b] ()};{[] ()};`UTC;hook)];
    (``cfg`window`batch)!(::;enlist[`source]!enlist `rd_src;`range_from`range_to!(2026.09.01D00:00;2026.09.01D01:00);batch)}

/ --- the declaration -------------------------------------------------------

.qt.qetl.source.readiness.test_the_default_hook_is_ready_for_everything:{[t]
    .qunit.assertEquals[((.qetl.source.def `demo_deals)`ready)[();2026.09.01D00:00;2026.09.01D01:00];();
        "a source that declares no hook has no unmet requirement"]};

.qt.qetl.source.readiness.test_a_ready_that_is_not_a_lambda_is_refused:{[t]
    .qunit.assertThrows[{.qetl.source.define[`rd_bad;x]};.qt.qetl.source.readiness.decl[enlist[`ready]!enlist 1b];
        "define: rd_bad's ready must be a lambda taking the fetched batch and the window's bounds and returning its unmet requirements";
        "a flag is not a hook"]};

.qt.qetl.source.readiness.test_a_ready_of_the_wrong_arity_is_refused:{[t]
    .qunit.assertThrows[{.qetl.source.define[`rd_bad;x]};.qt.qetl.source.readiness.decl[enlist[`ready]!enlist {[batch] ()}];
        "define: rd_bad's ready must take exactly three arguments, the batch and the window's range_from and range_to";
        "it needs the batch and both bounds"]};

.qt.qetl.source.readiness.test_the_markout_source_declares_a_hook:{[t]
    d:{(.qetl.source.def x)`ready} each enlist `hdb_fx_markouts;
    .qunit.assertEquals[all {not x~(.qetl.source.optional_declarations)`ready} each d;1b;"the markout source gates on its reference data"]};

/ --- the stage -------------------------------------------------------------

.qt.qetl.source.readiness.test_a_window_with_no_unmet_requirement_passes_through:{[t]
    s:.qt.qetl.source.readiness.state_for[{[b;f;t] ()};([] time:enlist 2026.09.01D00:00)];
    .qunit.assertEquals[.qetl.job.bounded.stage_ready[`w;s];s;"the state is returned untouched"]};

.qt.qetl.source.readiness.test_an_unmet_requirement_fails_the_window_and_names_it:{[t]
    s:.qt.qetl.source.readiness.state_for[{[b;f;t] enlist "reference data absent"};([] time:enlist 2026.09.01D00:00)];
    r:.qetl.job.bounded.stage_ready[`w;s];
    .qunit.assertEquals[r[`failed]`fields;enlist[`unmet]!enlist enlist "reference data absent";"the requirement is carried to the log"];
    .qunit.assertTrue[(r[`failed]`message) like "window not ready*";"and the window is told apart from a failed fetch"]};

.qt.qetl.source.readiness.test_a_hook_sees_the_batch_and_the_window:{[t]
    s:.qt.qetl.source.readiness.state_for[{[b;f;t] (string count b;string f;string t)};([] time:enlist 2026.09.01D00:00)];
    r:.qetl.job.bounded.stage_ready[`w;s];
    .qunit.assertEquals[(r[`failed]`fields)`unmet;(enlist "1";"2026.09.01D00:00:00.000000000";"2026.09.01D01:00:00.000000000");
        "batch, range_from and range_to, in that order"]};

.qt.qetl.source.readiness.test_a_bare_string_is_not_a_list_of_requirements:{[t]
    s:.qt.qetl.source.readiness.state_for[{[b;f;t] "absent"};([] time:enlist 2026.09.01D00:00)];
    r:.qetl.job.bounded.stage_ready[`w;s];
    .qunit.assertEquals[(r[`failed]`fields)`error;"ready must return a list of strings, one per unmet requirement, or ()";
        "a gate that answers in the wrong shape does not say yes"]};

.qt.qetl.source.readiness.test_a_hook_that_throws_fails_the_window_rather_than_passing_it:{[t]
    s:.qt.qetl.source.readiness.state_for[{[b;f;t] 'boom};([] time:enlist 2026.09.01D00:00)];
    r:.qetl.job.bounded.stage_ready[`w;s];
    .qunit.assertEquals[(r[`failed]`fields)`error;"boom";"the hook's own error is carried"]};
