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
    .qunit.assertTrue[(count got)>50;"the real tree's suites are found"];
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

/ --- the library does not carry the harness --------------------------------

.qt.harness.layout.test_a_library_scan_drops_the_harness_and_nothing_else:{[t]
    nss:`.q`.qunit`.qt`.qt.support`.qt.qetl.io`.qetl.io`.qpipe.job.x`.qtorq;
    .qunit.assertEquals[.qt.support.library_only nss;`.qetl.io`.qpipe.job.x`.qtorq;
        ".qf.util.owned lists every root namespace starting with q, which includes .qt"]};

.qt.harness.layout.test_production_sources_never_mention_the_test_tree:{[t]
    / init.q and everything it loads must stand without .qt and .qunit: the
    / tree is loaded by processes that have no tests directory. The runtime
    / half - a fresh interpreter loading src/ finds neither - is
    / python/uqs/tests/test_etl_standalone_load.py.
    files:(.qt.support.q_files["src"]),(.qt.support.q_files["scripts/processes"]);
    code:raze {[f] ls:read0 hsym `$f; ls:ls where not (trim each ls) like "/*"; (f,": "),/:ls where
        (ls like "*.qt.*") or (ls like "*.qunit*") or ls like "*tests/lib*"} each files;
    .qunit.assertEquals[code;();"no production line refers to .qt, .qunit or tests/lib"]};
