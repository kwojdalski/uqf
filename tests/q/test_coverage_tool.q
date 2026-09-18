/ test_coverage_tool.q - scripts/dev/coverage.q, the .cov library (.covtest).
/ .
/ The API is KX's (code.kx.com/developer/libraries/code-coverage), so the
/ tests are written against that contract rather than against this
/ implementation: the three entry points, the settings keys, the results
/ columns, and the <<<>>> / X report marks.
/ .
/ TWO PROPERTIES CARRY THE WHOLE TOOL, and they pull against each other.
/ .
/   It must instrument ENOUGH. A statement or branch that gets no probe
/   reports as covered forever, which is worse than having no number.
/ .
/   It must not CHANGE what it measures. `$[c;a;b]` is a conditional
/   EXPRESSION whose arms produce a value; `if[c;a;b]` is a statement whose
/   arms produce none. A probe in the wrong one either fails to parse or
/   silently alters a result. So several tests below are pairs: a construct
/   that must be instrumented, beside its look-alike that must not.
/ .
/ Load scripts/dev/coverage.q, tests/lib/qunit.q and tests/lib/testutil.q before
/ this file.

/ Fixtures live in their own namespace so instrumenting them cannot disturb
/ anything else, and so the namespace settings have something to select.
\d .covfix
add:{[a;b] a+b};
branch:{[x] $[x>0;`pos;`neg]};
guard:{[x] if[x>0; :`positive]; `other};
loop:{[n] r:0; do[n; r+:1]; r};
several:{[] a:1; b:2; a+b};
nested:{[x] g:{[y] y*2}; g x};
stringy:{[x] "a;b]"};
untouched:{[x] x};
/ Calls a NEIGHBOUR by its unqualified name - the case that breaks if an
/ instrumented function is re-evaluated at root.
caller:{[x] add[x;1]};
\d .covtest

/ Private: the commonest settings dictionary - instrument exactly one
/ function. Defined INSIDE .covtest: an earlier draft put it after a `\d .`
/ and every test then failed with a bare ` when the name did not resolve.
only:{[nm] (enlist `functions)!enlist nm}

/ --- the results contract -------------------------------------------------

test_run_returns_the_documented_columns:{[t]
    r:.cov.run[.covfix.add;1 2;.covtest.only `.covfix.add];
    .qunit.assertEquals[cols r;
        `name`iterations`lineIterations`blockIterations`lines`blocks`text;
        "the results table is the one the KX API documents"]};

test_a_function_that_ran_reports_an_iteration:{[t]
    r:.cov.run[.covfix.add;1 2;.covtest.only `.covfix.add];
    .qunit.assertEquals[first r`iterations;1;"one call, one iteration"]};

test_the_text_column_is_the_original_source:{[t]
    r:.cov.run[.covfix.add;1 2;.covtest.only `.covfix.add];
    .qunit.assertEquals[first r`text;"{[a;b] a+b}";
        "the report shows the source as written, not the instrumented copy"]};

test_the_call_result_is_not_disturbed:{[t]
    / The property everything else depends on. If instrumenting changed what
    / a function returned, every number this tool produced would be about
    / different code from the code that ships.
    .cov.run[.covfix.add;1 2;.covtest.only `.covfix.add];
    .qunit.assertEquals[.covfix.add[1;2];3;"the function still computes what it did"]};

/ --- statements ------------------------------------------------------------

test_every_statement_is_counted_separately:{[t]
    r:.cov.run[.covfix.several;enlist (::);.covtest.only `.covfix.several];
    .qunit.assertEquals[count first r`lineIterations;3;"three statements, three counters"]};

test_a_nested_lambda_is_instrumented_too:{[t]
    r:.cov.run[.covfix.nested;enlist 5;.covtest.only `.covfix.nested];
    .qunit.assertEquals[count first r`lines;3;
        "the inner lambda's body is a statement of its own"];
    .qunit.assertEquals[all 0<first r`lineIterations;1b;"and it ran"]};

/ --- branches, which is the point -----------------------------------------

/ A function can have every statement executed and a branch never taken.
/ That is where most real gaps are, and it is exactly what a function-level
/ counter cannot see.
test_an_untaken_conditional_arm_is_reported:{[t]
    r:.cov.run[.covfix.branch;enlist 1;.covtest.only `.covfix.branch];
    .qunit.assertEquals[first r`blockIterations;1 0;
        "the positive arm ran once, the negative arm never"]};

test_the_other_arm_is_reported_when_taken:{[t]
    r:.cov.run[.covfix.branch;enlist -1;.covtest.only `.covfix.branch];
    .qunit.assertEquals[first r`blockIterations;0 1;"and the other way round"]};

test_a_conditional_still_returns_its_own_value:{[t]
    / The wrapper around a `$` arm is an EXPRESSION that returns the arm. If
    / it returned anything else - or evaluated the untaken arm - this fails.
    .cov.run[.covfix.branch;enlist 1;.covtest.only `.covfix.branch];
    .qunit.assertEquals[(.covfix.branch 1;.covfix.branch -1);`pos`neg;
        "both arms still produce what they always did"]};

test_an_if_arm_is_a_block_as_well_as_a_statement:{[t]
    r:.cov.run[.covfix.guard;enlist -1;.covtest.only `.covfix.guard];
    .qunit.assertEquals[first r`blockIterations;enlist 0;
        "the guarded statement never ran, and is reported as an untaken branch"]};

test_a_taken_if_arm_is_counted:{[t]
    r:.cov.run[.covfix.guard;enlist 5;.covtest.only `.covfix.guard];
    .qunit.assertEquals[first r`blockIterations;enlist 1;"taken once"]};

test_a_loop_body_counts_every_iteration:{[t]
    / Not "did it run" but "how often" - a loop that ran once where it should
    / have run a thousand times is a bug a boolean cannot show.
    r:.cov.run[.covfix.loop;enlist 4;.covtest.only `.covfix.loop];
    .qunit.assertEquals[first r`blockIterations;enlist 4;"four iterations, four counts"]};

/ --- the lexer, through the tool ------------------------------------------

test_a_semicolon_inside_a_string_is_not_a_statement_boundary:{[t]
    / If it were, the probe would land inside the string literal and the
    / function would return different text.
    r:.cov.run[.covfix.stringy;enlist 1;.covtest.only `.covfix.stringy];
    .qunit.assertEquals[count first r`lines;1;"one statement, not two"];
    .qunit.assertEquals[.covfix.stringy 1;"a;b]";"and the string is unchanged"]};

/ --- selection -------------------------------------------------------------

test_a_namespace_selects_its_lambdas:{[t]
    r:.cov.run[.covfix.add;1 2;(enlist `namespaces)!enlist `.covfix];
    .qunit.assertTrue[`.covfix.branch in r`name;"every lambda in the namespace is instrumented"]};

test_ignorefunctions_removes_one:{[t]
    r:.cov.run[.covfix.add;1 2;`namespaces`ignoreFunctions!(`.covfix;`.covfix.branch)];
    .qunit.assertEquals[`.covfix.branch in r`name;0b;"an ignored function is not instrumented"]};

test_ignore_beats_include:{[t]
    / An ignore is a statement about what must not be touched, so naming the
    / same function on both sides must leave it OUT. `add` is included as
    / well, or the selection would be empty and the run refused for a
    / different reason entirely.
    r:.cov.run[.covfix.add;1 2;
        `functions`ignoreFunctions!(`.covfix.add`.covfix.branch;enlist `.covfix.branch)];
    .qunit.assertEquals[`.covfix.branch in r`name;0b;"subtraction wins"];
    .qunit.assertTrue[`.covfix.add in r`name;"and the rest is still selected"]};

test_selecting_nothing_is_refused_rather_than_reported_as_perfect:{[t]
    / An empty selection would otherwise render as 100% of nothing, which is
    / the most misleading output this tool could produce.
    .qunit.assertError[{.cov.run[.covfix.add;1 2;x]};(enlist `functions)!enlist `.covfix.no_such;
        "a settings dictionary that selects no function is an error"]};

test_settings_must_be_a_dictionary:{[t]
    .qunit.assertError[{.cov.run[.covfix.add;1 2;x]};`not_a_dict;
        "a malformed settings argument is refused at the door"]};

/ --- restoration -----------------------------------------------------------

test_the_original_is_restored_afterwards:{[t]
    .cov.run[.covfix.add;1 2;.covtest.only `.covfix.add];
    .qunit.assertEquals[last value .covfix.add;"{[a;b] a+b}";
        "the tree is left exactly as it was found"]};

test_the_original_is_restored_even_when_the_call_throws:{[t]
    / A run that threw and left the tree instrumented would poison every
    / later call in the session - worse than losing the measurement.
    thrower:{[x] '"boom"};
    `.covfix.thrower set thrower;
    @[{.cov.run[.covfix.thrower;enlist 1;.covtest.only `.covfix.thrower]};::;{x}];
    .qunit.assertEquals[last value .covfix.thrower;"{[x] '\"boom\"}";
        "restoration survives a failing call"]};

test_a_failing_call_is_reported_not_swallowed:{[t]
    `.covfix.thrower2 set {[x] '"boom"};
    r:@[{.cov.run[.covfix.thrower2;enlist 1;.covtest.only `.covfix.thrower2]; ""};::;{x}];
    .qunit.assertTrue[r like "*boom*";"the original error reaches the caller"]};

test_a_function_in_a_namespace_still_resolves_its_own_names:{[t]
    / Re-evaluating a function's source at ROOT rebinds every unqualified
    / name in it. .covfix.caller says `add`, meaning .covfix.add; valued at
    / root that is a root `add` which does not exist, and the function fails
    / with 'add the moment it runs.
    r:.cov.run[.covfix.caller;enlist 1;.covtest.only `.covfix.caller];
    .qunit.assertEquals[first r`iterations;1;"a namespaced function runs under instrumentation"];
    .qunit.assertEquals[.covfix.caller 1;2;"and still resolves its neighbours afterwards"]};

/ --- captured copies, and nesting ----------------------------------------

/ THE BLIND SPOT .cov.reseed CLOSES. Instrumenting a NAME does nothing for a
/ copy of the function taken before instrumentation. `.qio.memory` is
/ `(enlist `write)!enlist write_memory`, so every bounded worker writes
/ through a captured copy - and `.qio.write_memory` reported as never called
/ while being exercised on every window. A number that says "never called"
/ about code the suite runs constantly is worse than no number: the obvious
/ response is to write a test that already exists.
test_a_captured_copy_is_counted:{[t]
    `.covfix.holder set (enlist `f)!enlist .covfix.add;
    r:.cov.run[{[] .covfix.holder[`f][1;2]};enlist (::);.covtest.only `.covfix.add];
    .qunit.assertEquals[first r`iterations;1;
        "a call through a dictionary that captured the function is counted"]};

test_a_captured_copy_is_put_back:{[t]
    `.covfix.holder set (enlist `f)!enlist .covfix.add;
    .cov.run[{[] .covfix.holder[`f][1;2]};enlist (::);.covtest.only `.covfix.add];
    .qunit.assertEquals[.covfix.holder[`f]~.covfix.add;1b;
        "the registry holds the original again afterwards"]};

test_a_copy_nested_two_dictionaries_deep_is_counted:{[t]
    / .qsrc.sources is source -> declaration -> query, which is this shape.
    `.covfix.deep set (enlist `decl)!enlist (enlist `f)!enlist .covfix.add;
    r:.cov.run[{[] .covfix.deep[`decl][`f][1;2]};enlist (::);.covtest.only `.covfix.add];
    .qunit.assertEquals[first r`iterations;1;"the walk reaches a nested capture"]};

test_an_unrelated_global_is_left_alone:{[t]
    `.covfix.untouched_dict set (enlist `g)!enlist .covfix.branch;
    .cov.run[{[] 1+1};enlist (::);.covtest.only `.covfix.add];
    .qunit.assertEquals[.covfix.untouched_dict[`g]~.covfix.branch;1b;
        "a dictionary holding a function nobody instrumented is not rewritten"]};

/ A suite being MEASURED can contain tests that call .cov.run - this file is
/ proof - and an inner run that simply reallocated the counters left every
/ outer probe indexing past the end of a shorter vector. The whole suite then
/ died in a beforeNamespace with a bare 'length that named nothing.
test_a_nested_run_does_not_destroy_the_outer_counters:{[t]
    outer:.cov.run[{[]
        .cov.run[.covfix.add;1 2;.covtest.only `.covfix.add];
        .covfix.branch 1}
      ;enlist (::);.covtest.only `.covfix.branch];
    .qunit.assertEquals[first outer`iterations;1;
        "the outer measurement survives a run nested inside it"]};

test_a_nested_run_reports_its_own_numbers:{[t]
    / `::` assigns the GLOBAL `.covtest.inner`, so the result is read back
    / through that name rather than through a local of the same spelling.
    `.covtest.inner set (::);
    .cov.run[{[]
        `.covtest.inner set .cov.run[.covfix.add;1 2;.covtest.only `.covfix.add];
        .covfix.branch 1}
      ;enlist (::);.covtest.only `.covfix.branch];
    .qunit.assertEquals[first .covtest.inner`iterations;1;"and the inner one is still measured"]};

/ --- the report -------------------------------------------------------------

/ Two single-wildcard patterns rather than one "*<<<*>>>*": q's `like`
/ throws a bare 'nyi on more than one interior `*`, so the obvious pattern
/ fails as an ERROR rather than as a mismatch - which reads like a broken
/ tool rather than a broken test.
test_the_report_marks_an_untaken_branch:{[t]
    out:.cov.format.go .cov.run[.covfix.branch;enlist 1;.covtest.only `.covfix.branch];
    .qunit.assertTrue[any out like "*<<<*";"an unexecuted section opens with <<<"];
    .qunit.assertTrue[any out like "*>>>*";"and closes with >>>"]};

test_a_line_holding_unrun_code_is_flagged:{[t]
    out:.cov.format.go .cov.run[.covfix.branch;enlist 1;.covtest.only `.covfix.branch];
    .qunit.assertTrue[any out like "X *";"and its line is prefixed with X"]};

test_a_fully_covered_function_is_marked_nowhere:{[t]
    out:.cov.format.go .cov.run[.covfix.add;1 2;.covtest.only `.covfix.add];
    .qunit.assertEquals[any out like "*<<<*";0b;"nothing to mark when everything ran"];
    .qunit.assertTrue[any out like "*100*";"and it reports as complete"]};

test_the_report_counts_the_incomplete_functions:{[t]
    out:.cov.format.go .cov.run[.covfix.branch;enlist 1;.covtest.only `.covfix.branch];
    .qunit.assertTrue[any out like "1 function(s) with incomplete coverage*";
        "the summary says how many need attention"]};

test_display_prints_and_returns_nothing:{[t]
    .qunit.assertEquals[.cov.format.display .cov.run[.covfix.add;1 2;.covtest.only `.covfix.add];
        (::);
        "display is for its effect, and returns null like the API says"]};

\d .
