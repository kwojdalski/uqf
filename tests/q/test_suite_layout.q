/ test_suite_layout.q - how a suite is found, and the layout rules that stop a
/ renamed one silently ceasing to run (.qt.harness.layout, #1412).
/ .
/ tests/lib/discovery.q is the code under test. Its rule is "a namespace under
/ .qt that directly holds test* functions is a suite", and it replaced a hand-kept
/ list of namespaces held against the names the files declared. The tests here
/ are the cases that list could not express: where a suite sits, what is NOT
/ one, and what the loader must refuse rather than skip.
/ .
/ Scratch namespaces live under .qt.fixture.layout_probe - a reserved subtree,
/ so no real run ever finds them - and are removed after each test.

.qt.harness.layout.probe:`.qt.fixture.layout_probe

.qt.harness.layout.tearDown_probe:{[]
    if[`layout_probe in key `.qt.fixture; ![`.qt.fixture;();0b;enlist `layout_probe]];
    }

/ --- what is a suite -------------------------------------------------------

.qt.harness.layout.test_a_suite_is_a_namespace_that_holds_tests_wherever_it_sits:{[t]
    `.qt.fixture.layout_probe.alpha.test_one set {[t] 1};
    `.qt.fixture.layout_probe.alpha.helper set {[x] x};
    `.qt.fixture.layout_probe.alpha.beta.test_two set {[t] 2};
    `.qt.fixture.layout_probe.gamma.delta.test_three set {[t] 3};
    .qunit.assertEquals[.qt.support.suites_under .qt.harness.layout.probe;
        `.qt.fixture.layout_probe.alpha`.qt.fixture.layout_probe.alpha.beta`.qt.fixture.layout_probe.gamma.delta;
        "alpha and its child are suites, parent first; gamma holds only a namespace, so it is a container"]};

.qt.harness.layout.test_a_label_repeated_at_another_level_is_not_a_collision:{[t]
    `.qt.fixture.layout_probe.job.job.test_one set {[t] 1};
    `.qt.fixture.layout_probe.job.test_two set {[t] 2};
    .qunit.assertEquals[.qt.support.suites_under .qt.harness.layout.probe;
        `.qt.fixture.layout_probe.job`.qt.fixture.layout_probe.job.job;
        "job is a suite and holds a namespace called job"]};

.qt.harness.layout.test_siblings_run_alphabetically_whatever_order_they_were_defined_in:{[t]
    `.qt.fixture.layout_probe.zeta.test_one set {[t] 1};
    `.qt.fixture.layout_probe.beta.test_one set {[t] 1};
    `.qt.fixture.layout_probe.alpha.test_one set {[t] 1};
    .qunit.assertEquals[.qt.support.suites_under .qt.harness.layout.probe;
        `.qt.fixture.layout_probe.alpha`.qt.fixture.layout_probe.beta`.qt.fixture.layout_probe.zeta;
        "the order is a property of the names, not of the order the files loaded in"]};

.qt.harness.layout.test_a_namespace_with_helpers_and_no_test_is_refused_not_skipped:{[t]
    `.qt.fixture.layout_probe.alpha.helper set {[x] x};
    .qunit.assertThrows[.qt.support.suites_under;.qt.harness.layout.probe;
        ".qt.fixture.layout_probe.alpha holds functions but no test*";
        "a helper outside .qt.support and .qt.fixture would otherwise be skipped without a word"]};

.qt.harness.layout.test_a_test_that_takes_more_than_one_argument_is_refused:{[t]
    `.qt.fixture.layout_probe.alpha.test_two set {[a;b] 1};
    .qunit.assertThrows[.qt.support.suites_under;.qt.harness.layout.probe;
        ".qt.fixture.layout_probe.alpha.test_two is not a one-argument function*";
        "qUnit would report it as an error row with nothing in it"];
    `.qt.fixture.layout_probe.alpha.test_two set {[a;b;c] 1};
    .qunit.assertThrows[.qt.support.suites_under;.qt.harness.layout.probe;
        "*is not a one-argument function*";"three is no better"]};

.qt.harness.layout.test_a_test_that_is_not_a_function_at_all_is_not_a_test:{[t]
    / qUnit lists functions only, so a value called test_x is ignored by it -
    / and by discovery, which would otherwise call its namespace a suite.
    `.qt.fixture.layout_probe.alpha.test_data set 42;
    `.qt.fixture.layout_probe.alpha.test_real set {[t] 1};
    .qunit.assertEquals[.qt.support.tests_in `.qt.fixture.layout_probe.alpha;enlist `test_real;
        "only the function counts"]};

.qt.harness.layout.test_a_missing_root_is_an_error_not_an_empty_run:{[t]
    .qunit.assertThrows[.qt.support.suites_under;`.qt.fixture.layout_probe;
        "suites: there is no .qt.fixture.layout_probe namespace*";
        "a runner that found nothing must not be a runner that passed"]};

.qt.harness.layout.test_the_reserved_subtrees_are_never_suites:{[t]
    `.qt.fixture.layout_probe.alpha.test_one set {[t] 1};
    got:.qt.support.suites[];
    .qunit.assertEquals[got where got like ".qt.fixture*";`symbol$();
        "a double or fixture worker is not a suite, even holding a test*"];
    .qunit.assertEquals[got where got like ".qt.support*";`symbol$();
        "nor is the harness"]};

.qt.harness.layout.test_every_discovered_suite_holds_a_test_and_sits_under_qt:{[t]
    got:.qt.support.suites[];
    / At least one per file loaded: every suite file declares a suite, and a
    / run under -files loads only some of them.
    .qunit.assertTrue[(count got)>=count .qt.support.suite_files[];"the real tree's suites are found"];
    .qunit.assertTrue[all got like ".qt.*";"all under .qt"];
    .qunit.assertEquals[got where 0=count each .qt.support.tests_in each got;`symbol$();"each holds a test"];
    .qunit.assertEquals[count got;count distinct got;"each exactly once"]};

.qt.harness.layout.test_the_suites_are_found_by_what_they_hold_and_this_one_is_among_them:{[t]
    .qunit.assertTrue[`.qt.harness.layout in .qt.support.suites[];"this very suite"];
    .qunit.assertTrue[`.qt.qetl.coverage in .qt.support.suites[];"the issue's example for .qetl.coverage"];
    .qunit.assertTrue[`.qt.qetl.job.stream in .qt.support.suites[];"a suite that is also a parent"];
    .qunit.assertTrue[`.qt.qetl.job.stream.poll in .qt.support.suites[];"and its child"];
    .qunit.assertFalse[`.qt.qpipe.job in .qt.support.suites[];"the container of the job suites"]};

.qt.harness.layout.test_a_job_suite_mirrors_its_production_namespace:{[t]
    .qunit.assertEquals[.qt.support.job_suite `fx_markout;`.qt.qpipe.job.fx_markout;
        "the suite for .qpipe.job.fx_markout"];
    .qunit.assertTrue[(.qt.support.job_suite `fx_markout) in .qt.support.suites[];"and it is a real one"]};

/ --- what is not -----------------------------------------------------------

.qt.harness.layout.test_no_suite_is_declared_outside_qt:{[t]
    .qunit.assertEquals[.qt.support.outside_suites[];`symbol$();
        "a suite is a namespace under .qt - a root-level <thing>test holding test_ functions is the old way, and is refused"]};

.qt.harness.layout.test_a_suite_outside_qt_is_reported_by_the_layout_check:{[t]
    `.zzoutsidetest.test_one set {[t] 1};
    got:.qt.support.outside_suites[];
    / a root namespace cannot be deleted by name; emptying it is enough, as only a test makes it a suite
    ![`.zzoutsidetest;();0b;enlist `test_one];
    .qunit.assertEquals[got;enlist `.zzoutsidetest;"found, so layout_problems names it"]};

.qt.harness.layout.test_a_test_file_declares_no_context_switch:{[t]
    / q 4.0 rejects a nested working context, and a leading dot is absolute,
    / so a suite defines everything from the root by full path.
    bad:raze {[f]
        ls:read0 hsym `$f;
        sw:ls where ls like "\\d .?*";
        sw,:ls where ls like "system \"d *";
        (f,": "),/:sw} each .qt.support.layout_files[];
    .qunit.assertEquals[bad;();
        "no test or helper file enters a namespace: define .qt.<owner>.<suite>.<name> from the root"]};

/ --- the files, held against the tree ----------------------------------------

.qt.harness.layout.test_the_files_and_the_live_tree_agree_on_every_test:{[t]
    p:.qt.support.layout_problems[];
    .qunit.assertEquals[p;();"a test a file declares that did not load as a test, or one that exists with no declaration: ","; " sv p]};

.qt.harness.layout.test_a_path_defined_twice_is_a_collision:{[t]
    .qunit.assertEquals[.qt.support.collisions `.qt.a.x`.qt.b.y`.qt.a.x;
        enlist "defined twice: .qt.a.x";"the later definition would silently replace the earlier"]};

.qt.harness.layout.test_a_value_that_must_also_be_a_container_is_a_collision:{[t]
    .qunit.assertEquals[.qt.support.collisions `.qt.a.b`.qt.a.b.c;
        enlist "both a value and a namespace: .qt.a.b";
        "q replaces the container if the value is defined after it, and refuses if before"];
    .qunit.assertEquals[.qt.support.collisions `.qt.a.b.c`.qt.a.b;
        enlist "both a value and a namespace: .qt.a.b";"in either order of definition"]};

.qt.harness.layout.test_repeated_labels_and_near_names_are_not_collisions:{[t]
    .qunit.assertEquals[.qt.support.collisions `.qt.qetl.job.stream.test_x`.qt.qetl.job.stream.poll.test_y;();
        "a suite and its child suite"];
    .qunit.assertEquals[.qt.support.collisions `.qt.a.job.test_x`.qt.a.job.job.test_y;();"job under job"];
    .qunit.assertEquals[.qt.support.collisions `.qt.a.b`.qt.a.b2`.qt.a.b_c;();
        "a name that merely starts the same way is not beneath it"];
    .qunit.assertEquals[.qt.support.collisions `symbol$();();"nothing defined, nothing colliding"]};

.qt.harness.layout.test_only_a_definition_at_the_start_of_a_line_is_a_definition:{[t]
    .qunit.assertEquals[.qt.support.defined_path ".qt.a.b.test_x:{[t] 1}";`.qt.a.b.test_x;"a definition"];
    .qunit.assertEquals[.qt.support.defined_path ".qt.a.b.n:1";`.qt.a.b.n;"a value"];
    .qunit.assertEquals[.qt.support.defined_path "    .qt.a.b.n:1";`;"a continuation line"];
    .qunit.assertEquals[.qt.support.defined_path "/ .qt.a.b.n:1";`;"a comment"];
    .qunit.assertEquals[.qt.support.defined_path ".qetl.a.b:1";`;"another owner's"];
    .qunit.assertEquals[.qt.support.defined_path ".qt.a.b.f[1]";`;"a call, not a definition"];
    .qunit.assertEquals[.qt.support.defined_path ".qt.a.b-c:1";`;"not a name"]};

.qt.harness.layout.test_a_file_declares_its_tests_by_whole_path:{[t]
    lines:(".qt.a.b.helper:1";".qt.a.b.test_x:{[t] 1}";"/ .qt.a.b.test_y:{[t] 1}";
        "    .qt.a.b.test_z:{[t] 1}";"test_after:{[t] 1}");
    .qunit.assertEquals[.qt.support.declared_tests lines;enlist `.qt.a.b.test_x;
        "a definition at the start of a line; not the helper, the comment, the continuation or a bare name"];
    .qunit.assertEquals[.qt.support.declared_tests (".qt.support.test_names:1";".qt.fixture.x.test_a:1");`symbol$();
        "the harness and the fixtures are never suites, whatever they are called"]};

/ --- running a suite that is not at the root --------------------------------

.qt.harness.layout.test_a_nested_suite_runs_with_its_hooks_in_order:{[t]
    `.qt.fixture.layout_probe.nest.alpha.log set ();
    `.qt.fixture.layout_probe.nest.alpha.beforeNamespace_note set {`.qt.fixture.layout_probe.nest.alpha.log set .qt.fixture.layout_probe.nest.alpha.log,`before};
    `.qt.fixture.layout_probe.nest.alpha.setUp_note set {`.qt.fixture.layout_probe.nest.alpha.log set .qt.fixture.layout_probe.nest.alpha.log,`up};
    `.qt.fixture.layout_probe.nest.alpha.test_first set {[t] `.qt.fixture.layout_probe.nest.alpha.log set .qt.fixture.layout_probe.nest.alpha.log,`first; 1};
    `.qt.fixture.layout_probe.nest.alpha.test_second set {[t] `.qt.fixture.layout_probe.nest.alpha.log set .qt.fixture.layout_probe.nest.alpha.log,`second; 1};
    `.qt.fixture.layout_probe.nest.alpha.tearDown_note set {`.qt.fixture.layout_probe.nest.alpha.log set .qt.fixture.layout_probe.nest.alpha.log,`down};
    `.qt.fixture.layout_probe.nest.alpha.afterNamespace_note set {`.qt.fixture.layout_probe.nest.alpha.log set .qt.fixture.layout_probe.nest.alpha.log,`after};
    res:.qunit.runNsTests `.qt.fixture.layout_probe.nest.alpha;
    .qunit.assertEquals[res`name;`.qt.fixture.layout_probe.nest.alpha.test_first`.qt.fixture.layout_probe.nest.alpha.test_second;
        "both tests ran, under their full names"];
    .qunit.assertEquals[res`status;`pass`pass;"and passed"];
    .qunit.assertEquals[res`namespace;2#`.qt.fixture.layout_probe.nest.alpha;"in the namespace they were found in"];
    .qunit.assertEquals[.qt.fixture.layout_probe.nest.alpha.log;`before`up`first`down`up`second`down`after;
        "beforeNamespace once, setUp and tearDown round each test, afterNamespace once - found by name in a nested namespace"]};

.qt.harness.layout.test_a_namespace_that_does_not_exist_is_still_refused:{[t]
    .qunit.assertThrows[.qunit.runNsTests;`.qt.fixture.layout_probe.nothing;"nsNoExist";
        "the check the vendored runner made is kept, for the right reason"]};

.qt.harness.layout.test_the_vendored_runner_refuses_a_suite_below_the_root:{[t]
    / The reason the replacement exists: .qunit.runNsTests asks whether the
    / namespace is a key of the ROOT, so .qt.harness.layout is never one. If
    / this stops throwing, upstream was fixed and the replacement can go.
    .qunit.assertThrows[.qt.support.run_ns_tests_vendored;`.qt.harness.layout;"nsNoExist";
        "the vendored function cannot run any nested namespace"]};

.qt.harness.layout.test_a_suite_with_a_qunit_config_is_reported_because_it_would_be_ignored:{[t]
    `.qt.fixture.layout_probe.alpha.test_one set {[t] 1};
    `.qt.fixture.layout_probe.alpha.qunitConfig set `test_one`maxTime!(1;1);
    got:.qt.support.config_ignored enlist `.qt.fixture.layout_probe.alpha;
    .qunit.assertEquals[got;enlist `.qt.fixture.layout_probe.alpha;
        "qUnit reads qunitConfig through the first label of the test's name, so below the root it is dropped"]};

/ --- ordering --------------------------------------------------------------

.qt.harness.layout.test_reverse_reverses_and_the_default_leaves_the_order:{[t]
    s:`.qt.a`.qt.b`.qt.c;
    .qunit.assertEquals[.qt.support.ordered[s;""];s;"unset: as discovered"];
    .qunit.assertEquals[.qt.support.ordered[s;"reverse"];reverse s;"reverse"]};

.qt.harness.layout.test_shuffle_is_a_permutation:{[t]
    s:`$"s",/:string til 40;
    got:.qt.support.ordered[s;"shuffle"];
    .qunit.assertEquals[asc got;asc s;"the same suites, once each"];
    .qunit.assertFalse[got~s;"in another order"]};

.qt.harness.layout.test_an_unknown_order_is_refused:{[t]
    .qunit.assertThrows[.qt.support.ordered[`.qt.a`.qt.b;];"sideways";
        "ordered: UQF_TEST_ORDER must be reverse, shuffle, or unset - not sideways";
        "a typo must not quietly run the default"]};

/ --- a subset: -files (#1690, #1694) ----------------------------------------
/ `q tests/run_tests.q -files a.q,b.q` loads every suite file and runs the
/ suites the named ones define - what scripts/dev/test_impact.py selects for a
/ change, and what a shard of the suite runs.

.qt.harness.layout.test_without_the_option_every_suite_runs:{[t]
    s:`.qt.a`.qt.b;
    .qunit.assertEquals[.qt.support.files_option ("-q";"-p";"5000");::;"not given"];
    .qunit.assertEquals[.qt.support.select_files[`test_a.q`test_b.q;::];`test_a.q`test_b.q;"every file"];
    .qunit.assertEquals[.qt.support.selected_suites[s;("-q";"-p";"5000")];s;"every suite"]};

.qt.harness.layout.test_the_option_is_read_from_the_command_line:{[t]
    .qunit.assertEquals[.qt.support.files_option ("-files";"test_c.q,tests/q/test_a.q");
        "test_c.q,tests/q/test_a.q";"one comma-separated argument"];
    .qunit.assertEquals[.qt.support.files_option ("-files";"test_c.q";"test_a.q");
        "test_c.q,test_a.q";"or several"];
    .qunit.assertEquals[.qt.support.files_option ("-files";"");"";"given, empty"]};

.qt.harness.layout.test_a_selection_keeps_the_suite_order_and_takes_paths_or_names:{[t]
    f:`test_a.q`test_b.q`test_c.q;
    .qunit.assertEquals[.qt.support.select_files[f;"test_c.q, tests/q/test_a.q"];`test_a.q`test_c.q;
        "a bare name and a tests/q/ path both name a file, and the order is the suite's"]};

.qt.harness.layout.test_a_selection_naming_no_suite_file_is_refused:{[t]
    .qunit.assertThrows[.qt.support.select_files[`test_a.q`test_b.q;];"test_a.q,test_gone.q";
        "support.select_files: -files names no suite file: test_gone.q";
        "an unknown file is refused, not skipped"];
    .qunit.assertThrows[.qt.support.selected_suites[.qt.support.suites[];];("-files";"test_gone.q");
        "support.select_files: -files names no suite file: test_gone.q";
        "and the runner's own call refuses it"]};

.qt.harness.layout.test_an_empty_selection_is_refused:{[t]
    msg:"support.select_files: -files names no file - give tests/q/test_*.q names, comma-separated";
    .qunit.assertThrows[.qt.support.select_files[`test_a.q`test_b.q;];"";msg;"empty"];
    .qunit.assertThrows[.qt.support.select_files[`test_a.q`test_b.q;];" , ";msg;"only separators"]};

.qt.harness.layout.test_a_selected_file_runs_its_suites_and_an_unselected_one_is_loaded_but_not_run:{[t]
    / Two real suite files: this one and test_seed.q. Naming this one runs
    / .qt.harness.layout and not .qt.harness.seed - whose definitions are
    / loaded all the same, so a suite leaning on them still finds them.
    all_suites:.qt.support.suites[];
    got:.qt.support.selected_suites[all_suites;("-files";"test_suite_layout.q")];
    .qunit.assertTrue[`.qt.harness.layout in got;"the selected file's suite runs"];
    .qunit.assertFalse[`.qt.harness.seed in got;"the unselected file's suite does not"];
    .qunit.assertEquals[type .qt.harness.seed.known;7h;"but what that file defines is loaded"];
    .qunit.assertEquals[.qt.support.selected_suites[all_suites;("-files";"test_seed.q,test_suite_layout.q")];
        all_suites where all_suites in `.qt.harness.layout`.qt.harness.seed;"two files, two suites, in run order"]};

/ --- coverage of a subset: -modules (#1719) --------------------------------
/ `q tests/q/run_coverage.q -files a.q -modules src/x.q` runs a's suites and
/ judges only what src/x.q defines; scripts/test.py q-coverage hands any
/ failure of it to a run of the whole suite.

.qt.harness.layout.test_the_modules_option_is_read_from_the_command_line:{[t]
    .qunit.assertEquals[.qt.support.modules_option ("-files";"test_a.q");::;"not given"];
    .qunit.assertEquals[.qt.support.modules_option ("-modules";"src/a.q, src/b.q");("src/a.q";"src/b.q");
        "comma-separated, trimmed"];
    .qunit.assertThrows[.qt.support.modules_option;("-modules";" , ");
        "support.modules_option: -modules names no file - give .q paths, comma-separated";
        "a subset judging nothing is refused, not run"]};

.qt.harness.layout.test_a_function_s_module_is_the_file_that_defined_it:{[t]
    .qunit.assertEquals[.qt.support.source_file `.qt.support.modules_option;"tests/lib/support.q";
        "relative to the repository root, where every runner starts"];
    .qunit.assertEquals[.qt.support.source_file `.qt.fixture.no_such_function;"";"no function, no file"];
    nms:`.qt.support.modules_option`.qt.support.suite_files`.qt.harness.layout.probe_name;
    .qunit.assertEquals[.qt.support.defined_in[nms;enlist "tests/lib/support.q"];
        `.qt.support.modules_option`.qt.support.suite_files;"only what the module defines"];
    .qunit.assertEquals[.qt.support.defined_in[nms;::];nms;"every function, without -modules"]};

/ The gate's two directions, each among the functions a subset judges - a
/ function it does not judge (`.o.g`, entered only by a suite outside the
/ subset) is not reported either way.
.qt.harness.layout.test_a_subset_still_fails_a_newly_uncovered_function_it_judges:{[t]
    known:`.m.f`.m.g`.o.g`.o.h;
    judged:`.m.f`.m.g;
    g:.qt.support.coverage_gate[enlist `.m.f;`symbol$();judged;known];
    .qunit.assertEquals[g`new_gaps;enlist `.m.f;"a function of a changed module nothing entered"];
    .qunit.assertEquals[g`closed;`symbol$();"and nothing closed"]};

.qt.harness.layout.test_a_subset_does_not_report_what_it_does_not_judge:{[t]
    known:`.m.f`.m.g`.o.g`.o.h;
    judged:`.m.f`.m.g;
    / .o.g is no probe here, so not cold; .o.h, in the baseline, was not
    / instrumented and so not cold either - neither is this run's to judge
    g:.qt.support.coverage_gate[`symbol$();enlist `.o.h;judged;known];
    .qunit.assertEquals[g`new_gaps;`symbol$();"nothing outside the judged modules is a new gap"];
    .qunit.assertEquals[g`closed;`symbol$();"nor a closed baseline entry"]};

.qt.harness.layout.test_a_subset_still_reports_a_closed_entry_it_judges_and_a_gone_one:{[t]
    known:`.m.f`.m.g`.o.g;
    g:.qt.support.coverage_gate[`symbol$();`.m.g`.gone.f`.o.g;`.m.f`.m.g;known];
    .qunit.assertEquals[g`closed;`.gone.f`.m.g;
        "a judged entry now entered, and one naming no function at all"]};

.qt.harness.layout.test_a_whole_run_is_judged_as_before:{[t]
    known:`.m.f`.m.g`.o.g;
    g:.qt.support.coverage_gate[`.m.f`.o.g;`.o.g`.m.g;known;known];
    .qunit.assertEquals[g`new_gaps;enlist `.m.f;"cold, not in the baseline"];
    .qunit.assertEquals[g`closed;enlist `.m.g;"in the baseline, entered"]};

/ --- the coverage map: which suite files enter each function (#1719) -------

.qt.harness.layout.test_the_map_option_is_read_from_the_command_line:{[t]
    .qunit.assertEquals[.qt.support.map_option ("-files";"test_a.q");::;"not given"];
    .qunit.assertEquals[.qt.support.map_option ("-map";"tests/q/coverage_map.txt");"tests/q/coverage_map.txt";"a path"];
    .qunit.assertThrows[.qt.support.map_option;enlist "-map";
        "support.map_option: -map names no file - give the path to write";"no path is refused"]};

/ Three functions whose probes start at 100: the first has two (100 101),
/ the second none - nothing can enter it - and the third three (102 103 104).
/ Suite .s1 raised 100 and 101, .s2 raised 103 only (the third's second
/ statement, not its first: not entered) and .s3 raised 102.
.qt.harness.layout.test_the_coverage_map_names_the_files_whose_suites_enter_each_function:{[t]
    nms:`.qt.support.map_option`.qt.support.suite_files`.qt.support.select_files;
    res:([] name:nms; lines:(100 101;`long$();102 103 104));
    hits:`.s1`.s2`.s3!(100 101;enlist 103;enlist 102);
    files:`test_a.q`test_b.q`test_c.q!(enlist `.s1;`.s1`.s3;enlist `.s2);
    m:.qt.support.coverage_map[res;100;hits;files];
    .qunit.assertEquals[m`function;`.qt.support.map_option`.qt.support.select_files;
        "the functions some suite entered, sorted - not the one with no statement"];
    .qunit.assertEquals[m`module;2#`$"tests/lib/support.q";"by the file that defined them"];
    .qunit.assertEquals[m`files;(`test_a.q`test_b.q;enlist `test_b.q);
        "every file declaring a suite that entered it - and not .s2's, which ran a later statement only"]};

.qt.harness.layout.test_the_coverage_map_of_a_run_nothing_entered_is_empty:{[t]
    res:([] name:enlist `.qt.support.map_option; lines:enlist 100 101);
    m:.qt.support.coverage_map[res;100;(enlist `.s1)!enlist `long$();(enlist `test_a.q)!enlist enlist `.s1];
    .qunit.assertEquals[count m;0;"no row"];
    .qunit.assertEquals[cols m;`module`function`files;"the same columns"]};

.qt.harness.layout.test_the_coverage_map_is_written_one_tab_separated_line_per_function:{[t]
    m:([] module:enlist `$"src/a.q"; function:enlist `.a.f; files:enlist `test_a.q`test_b.q);
    lines:.qt.support.map_lines m;
    .qunit.assertEquals[last lines;"src/a.q\t.a.f\ttest_a.q,test_b.q";"module, function, files"];
    .qunit.assertTrue[all "#"=first each -1_lines;"a commented header before it"];
    .qunit.assertEquals[distinct type each lines;enlist 10h;"every line a string, as 0: needs"]};

.qt.harness.layout.test_a_suite_split_over_files_is_found_from_each:{[t]
    / A file declaring tests in a parent and a child suite selects both. The
    / file is only read, never loaded, so its names are not reserved ones.
    system"mkdir -p build/test-status";
    f:"build/test-status/split_suite.q";
    (hsym `$f) 0: (".qt.splitprobe.test_one:{[t] 1}";".qt.splitprobe.child.test_two:{[t] 2}";
        ".qt.splitprobe.helper:{[] 3}");
    s:`.qt.splitprobe`.qt.splitprobe.child`.qt.otherprobe;
    .qunit.assertEquals[.qt.support.suites_of[s;enlist f];2#s;
        "both namespaces that hold a declared test, and not the other"]};

.qt.harness.layout.test_every_file_is_loaded_whatever_runs:{[t]
    / The load does not read -files: every suite file is loaded in every run.
    .qunit.assertEquals[.qt.support.suite_files[];asc f where (f:key `:tests/q) like "test_*.q";
        "every suite file"]};

/ --- the library does not carry the harness --------------------------------

.qt.harness.layout.test_a_library_scan_drops_the_harness_and_nothing_else:{[t]
    nss:`.q`.qunit`.qt`.qt.support`.qt.qetl.io`.qetl.io`.qpipe.job.x`.qtorq;
    .qunit.assertEquals[.qt.support.library_only nss;`.qetl.io`.qpipe.job.x`.qtorq;
        ".qf.util.owned lists every root namespace starting with q, which includes .qt"]};
