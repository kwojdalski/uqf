/ test_coverage_tool.q - scripts/dev/coverage.q, the .cov library (.qt.cov).
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
/ Load scripts/dev/coverage.q, tests/lib/qunit.q and tests/lib/support.q before
/ this file.

/ Fixtures live in their own namespace so instrumenting them cannot disturb
/ anything else, and so the namespace settings have something to select.
.qt.fixture.cov.add:{[a;b] a+b};
.qt.fixture.cov.branch:{[x] $[x>0;`pos;`neg]};
.qt.fixture.cov.guard:{[x] if[x>0; :`positive]; `other};
.qt.fixture.cov.loop:{[n] r:0; do[n; r+:1]; r};
.qt.fixture.cov.several:{[] a:1; b:2; a+b};
.qt.fixture.cov.nested:{[x] g:{[y] y*2}; g x};
.qt.fixture.cov.stringy:{[x] "a;b]"};
.qt.fixture.cov.untouched:{[x] x};
/ Calls a NEIGHBOUR by its unqualified name - the case that breaks if an
/ instrumented function is re-evaluated at root.
.qt.fixture.cov.caller:{[x] .qt.fixture.cov.add[x;1]};

/ Private: the commonest settings dictionary - instrument exactly one
/ function. Defined INSIDE .qt.cov: an earlier draft put it after a `\d .`
/ and every test then failed with a bare ` when the name did not resolve.
.qt.cov.only:{[nm] (enlist `functions)!enlist nm}

/ --- the results contract -------------------------------------------------

.qt.cov.test_run_returns_the_documented_columns:{[t]
    r:.cov.run[.qt.fixture.cov.add;1 2;.qt.cov.only[`.qt.fixture.cov.add]];
    .qunit.assertEquals[cols r;
        `name`iterations`lineIterations`blockIterations`lines`blocks`text;
        "the results table is the one the KX API documents"]};

.qt.cov.test_a_function_that_ran_reports_an_iteration:{[t]
    r:.cov.run[.qt.fixture.cov.add;1 2;.qt.cov.only[`.qt.fixture.cov.add]];
    .qunit.assertEquals[first r`iterations;1;"one call, one iteration"]};

.qt.cov.test_the_text_column_is_the_original_source:{[t]
    r:.cov.run[.qt.fixture.cov.add;1 2;.qt.cov.only[`.qt.fixture.cov.add]];
    .qunit.assertEquals[first r`text;"{[a;b] a+b}";
        "the report shows the source as written, not the instrumented copy"]};

.qt.cov.test_the_call_result_is_not_disturbed:{[t]
    / The property everything else depends on. If instrumenting changed what
    / a function returned, every number this tool produced would be about
    / different code from the code that ships.
    .cov.run[.qt.fixture.cov.add;1 2;.qt.cov.only[`.qt.fixture.cov.add]];
    .qunit.assertEquals[.qt.fixture.cov.add[1;2];3;"the function still computes what it did"]};

/ --- statements ------------------------------------------------------------

.qt.cov.test_every_statement_is_counted_separately:{[t]
    r:.cov.run[.qt.fixture.cov.several;enlist (::);.qt.cov.only[`.qt.fixture.cov.several]];
    .qunit.assertEquals[count first r`lineIterations;3;"three statements, three counters"]};

.qt.cov.test_a_nested_lambda_is_instrumented_too:{[t]
    r:.cov.run[.qt.fixture.cov.nested;enlist 5;.qt.cov.only[`.qt.fixture.cov.nested]];
    .qunit.assertEquals[count first r`lines;3;
        "the inner lambda's body is a statement of its own"];
    .qunit.assertEquals[all 0<first r`lineIterations;1b;"and it ran"]};

/ --- branches, which is the point -----------------------------------------

/ A function can have every statement executed and a branch never taken.
/ That is where most real gaps are, and it is exactly what a function-level
/ counter cannot see.
.qt.cov.test_an_untaken_conditional_arm_is_reported:{[t]
    r:.cov.run[.qt.fixture.cov.branch;enlist 1;.qt.cov.only[`.qt.fixture.cov.branch]];
    .qunit.assertEquals[first r`blockIterations;1 0;
        "the positive arm ran once, the negative arm never"]};

.qt.cov.test_the_other_arm_is_reported_when_taken:{[t]
    r:.cov.run[.qt.fixture.cov.branch;enlist -1;.qt.cov.only[`.qt.fixture.cov.branch]];
    .qunit.assertEquals[first r`blockIterations;0 1;"and the other way round"]};

.qt.cov.test_a_conditional_still_returns_its_own_value:{[t]
    / The wrapper around a `$` arm is an EXPRESSION that returns the arm. If
    / it returned anything else - or evaluated the untaken arm - this fails.
    .cov.run[.qt.fixture.cov.branch;enlist 1;.qt.cov.only[`.qt.fixture.cov.branch]];
    .qunit.assertEquals[(.qt.fixture.cov.branch[1];.qt.fixture.cov.branch -1);`pos`neg;
        "both arms still produce what they always did"]};

.qt.cov.test_an_if_arm_is_a_block_as_well_as_a_statement:{[t]
    r:.cov.run[.qt.fixture.cov.guard;enlist -1;.qt.cov.only[`.qt.fixture.cov.guard]];
    .qunit.assertEquals[first r`blockIterations;enlist 0;
        "the guarded statement never ran, and is reported as an untaken branch"]};

.qt.cov.test_a_taken_if_arm_is_counted:{[t]
    r:.cov.run[.qt.fixture.cov.guard;enlist 5;.qt.cov.only[`.qt.fixture.cov.guard]];
    .qunit.assertEquals[first r`blockIterations;enlist 1;"taken once"]};

.qt.cov.test_a_loop_body_counts_every_iteration:{[t]
    / Not "did it run" but "how often" - a loop that ran once where it should
    / have run a thousand times is a bug a boolean cannot show.
    r:.cov.run[.qt.fixture.cov.loop;enlist 4;.qt.cov.only[`.qt.fixture.cov.loop]];
    .qunit.assertEquals[first r`blockIterations;enlist 4;"four iterations, four counts"]};

/ --- the lexer, through the tool ------------------------------------------

.qt.cov.test_a_semicolon_inside_a_string_is_not_a_statement_boundary:{[t]
    / If it were, the probe would land inside the string literal and the
    / function would return different text.
    r:.cov.run[.qt.fixture.cov.stringy;enlist 1;.qt.cov.only[`.qt.fixture.cov.stringy]];
    .qunit.assertEquals[count first r`lines;1;"one statement, not two"];
    .qunit.assertEquals[.qt.fixture.cov.stringy[1];"a;b]";"and the string is unchanged"]};

/ --- selection -------------------------------------------------------------

.qt.cov.test_a_namespace_selects_its_lambdas:{[t]
    r:.cov.run[.qt.fixture.cov.add;1 2;(enlist `namespaces)!enlist `.qt.fixture.cov];
    .qunit.assertTrue[`.qt.fixture.cov.branch in r`name;"every lambda in the namespace is instrumented"]};

.qt.cov.test_ignorefunctions_removes_one:{[t]
    r:.cov.run[.qt.fixture.cov.add;1 2;`namespaces`ignoreFunctions!(`.qt.fixture.cov;`.qt.fixture.cov.branch)];
    .qunit.assertEquals[`.qt.fixture.cov.branch in r`name;0b;"an ignored function is not instrumented"]};

.qt.cov.test_ignore_beats_include:{[t]
    / An ignore is a statement about what must not be touched, so naming the
    / same function on both sides must leave it OUT. `add` is included as
    / well, or the selection would be empty and the run refused for a
    / different reason entirely.
    r:.cov.run[.qt.fixture.cov.add;1 2;
        `functions`ignoreFunctions!(`.qt.fixture.cov.add`.qt.fixture.cov.branch;enlist `.qt.fixture.cov.branch)];
    .qunit.assertEquals[`.qt.fixture.cov.branch in r`name;0b;"subtraction wins"];
    .qunit.assertTrue[`.qt.fixture.cov.add in r`name;"and the rest is still selected"]};

.qt.cov.test_selecting_nothing_is_refused_rather_than_reported_as_perfect:{[t]
    / An empty selection would otherwise render as 100% of nothing, which is
    / the most misleading output this tool could produce.
    .qunit.assertError[{.cov.run[.qt.fixture.cov.add;1 2;x]};(enlist `functions)!enlist `.qt.fixture.cov.no_such;
        "a settings dictionary that selects no function is an error"]};

.qt.cov.test_settings_must_be_a_dictionary:{[t]
    .qunit.assertError[{.cov.run[.qt.fixture.cov.add;1 2;x]};`not_a_dict;
        "a malformed settings argument is refused at the door"]};

/ --- restoration -----------------------------------------------------------

.qt.cov.test_the_original_is_restored_afterwards:{[t]
    .cov.run[.qt.fixture.cov.add;1 2;.qt.cov.only[`.qt.fixture.cov.add]];
    .qunit.assertEquals[last value .qt.fixture.cov.add;"{[a;b] a+b}";
        "the tree is left exactly as it was found"]};

.qt.cov.test_the_original_is_restored_even_when_the_call_throws:{[t]
    / A run that threw and left the tree instrumented would poison every
    / later call in the session - worse than losing the measurement.
    thrower:{[x] '"boom"};
    `.qt.fixture.cov.thrower set thrower;
    @[{.cov.run[.qt.fixture.cov.thrower;enlist 1;.qt.cov.only[`.qt.fixture.cov.thrower]]};::;{x}];
    .qunit.assertEquals[last value .qt.fixture.cov.thrower;"{[x] '\"boom\"}";
        "restoration survives a failing call"]};

.qt.cov.test_a_failing_call_is_reported_not_swallowed:{[t]
    `.qt.fixture.cov.thrower2 set {[x] '"boom"};
    r:@[{.cov.run[.qt.fixture.cov.thrower2;enlist 1;.qt.cov.only[`.qt.fixture.cov.thrower2]]; ""};::;{x}];
    .qunit.assertTrue[r like "*boom*";"the original error reaches the caller"]};

.qt.cov.test_a_function_in_a_namespace_still_resolves_its_own_names:{[t]
    / Re-evaluating a function's source at ROOT rebinds every unqualified
    / name in it. .qt.fixture.cov.caller says `add`, meaning .qt.fixture.cov.add; valued at
    / root that is a root `add` which does not exist, and the function fails
    / with 'add the moment it runs.
    r:.cov.run[.qt.fixture.cov.caller;enlist 1;.qt.cov.only[`.qt.fixture.cov.caller]];
    .qunit.assertEquals[first r`iterations;1;"a namespaced function runs under instrumentation"];
    .qunit.assertEquals[.qt.fixture.cov.caller[1];2;"and still resolves its neighbours afterwards"]};

/ --- captured copies, and nesting ----------------------------------------

/ THE BLIND SPOT .cov.reseed CLOSES. Instrumenting a NAME does nothing for a
/ copy of the function taken before instrumentation. `.qetl.io.memory` is
/ `(enlist `write)!enlist write_memory`, so every bounded worker writes
/ through a captured copy - and `.qetl.io.write_memory` reported as never called
/ while being exercised on every window. A number that says "never called"
/ about code the suite runs constantly is worse than no number: the obvious
/ response is to write a test that already exists.
.qt.cov.test_a_captured_copy_is_counted:{[t]
    `.qt.fixture.cov.holder set (enlist `f)!enlist .qt.fixture.cov.add;
    r:.cov.run[{[] .qt.fixture.cov.holder[`f][1;2]};enlist (::);.qt.cov.only[`.qt.fixture.cov.add]];
    .qunit.assertEquals[first r`iterations;1;
        "a call through a dictionary that captured the function is counted"]};

.qt.cov.test_a_captured_copy_is_put_back:{[t]
    `.qt.fixture.cov.holder set (enlist `f)!enlist .qt.fixture.cov.add;
    .cov.run[{[] .qt.fixture.cov.holder[`f][1;2]};enlist (::);.qt.cov.only[`.qt.fixture.cov.add]];
    .qunit.assertEquals[.qt.fixture.cov.holder[`f]~.qt.fixture.cov.add;1b;
        "the registry holds the original again afterwards"]};

.qt.cov.test_a_copy_nested_two_dictionaries_deep_is_counted:{[t]
    / .qetl.source.sources is source -> declaration -> query, which is this shape.
    `.qt.fixture.cov.deep set (enlist `decl)!enlist (enlist `f)!enlist .qt.fixture.cov.add;
    r:.cov.run[{[] .qt.fixture.cov.deep[`decl][`f][1;2]};enlist (::);.qt.cov.only[`.qt.fixture.cov.add]];
    .qunit.assertEquals[first r`iterations;1;"the walk reaches a nested capture"]};

.qt.cov.test_an_unrelated_global_is_left_alone:{[t]
    `.qt.fixture.cov.untouched_dict set (enlist `g)!enlist .qt.fixture.cov.branch;
    .cov.run[{[] 1+1};enlist (::);.qt.cov.only[`.qt.fixture.cov.add]];
    .qunit.assertEquals[.qt.fixture.cov.untouched_dict[`g]~.qt.fixture.cov.branch;1b;
        "a dictionary holding a function nobody instrumented is not rewritten"]};

/ An EMPTY typed dictionary must come back with its types, and `~` is the
/ assertion that matters: (`symbol$())!`long$() and (`symbol$())!() are both
/ empty and both 99h, so anything weaker passes while the types are gone.
/ .
/ The walk used to rebuild every dictionary it visited as
/ (key v)!f each value v, and `each` over an empty TYPED vector gives a
/ generic empty list - so this exact shape came back untyped. reseed installs
/ whatever differs from the original, and a type change differs, so the
/ corrupted copy was written to the real global. .qtorq.published is one:
/ record_published's `0^published t` then gave () rather than 0 and the next
/ comparison threw 'type, which is what made q-unit red and q-order green
/ (#460).
.qt.cov.test_an_empty_typed_global_keeps_its_types:{[t]
    `.qt.fixture.cov.empty_typed set (`symbol$())!`long$();
    .cov.run[{[] 1+1};enlist (::);.qt.cov.only[`.qt.fixture.cov.add]];
    .qunit.assertEquals[.qt.fixture.cov.empty_typed~(`symbol$())!`long$();1b;
        "an empty typed dictionary is returned identical, types included"];
    .qunit.assertEquals[0^.qt.fixture.cov.empty_typed `absent;0;
        "so 0^ on a missing key still fills with 0, not with ()"]};

/ Where the trap STOPS, which is worth pinning even though neither case was
/ ever broken: swap_value rebuilds only 99h, 98h and 0h, so an empty TYPED
/ vector is returned by the default arm untouched whether it sits inside a
/ dictionary or is reached directly. This one characterises that boundary -
/ it passes with the fix and without it. The test above is the regression.
.qt.cov.test_an_empty_typed_vector_nested_in_a_global_keeps_its_type:{[t]
    `.qt.fixture.cov.nested_empty set (enlist `counts)!enlist `long$();
    `.qt.fixture.cov.list_empty set `float$();
    .cov.run[{[] 1+1};enlist (::);.qt.cov.only[`.qt.fixture.cov.add]];
    .qunit.assertEquals[.qt.fixture.cov.nested_empty[`counts]~`long$();1b;
        "an empty long vector inside a dictionary is still a long vector"];
    .qunit.assertEquals[.qt.fixture.cov.list_empty~`float$();1b;
        "and an empty float vector reached directly is still a float vector"]};

/ A suite being MEASURED can contain tests that call .cov.run - this file is
/ proof - and an inner run that simply reallocated the counters left every
/ outer probe indexing past the end of a shorter vector. The whole suite then
/ died in a beforeNamespace with a bare 'length that named nothing.
.qt.cov.test_a_nested_run_does_not_destroy_the_outer_counters:{[t]
    outer:.cov.run[{[]
        .cov.run[.qt.fixture.cov.add;1 2;.qt.cov.only[`.qt.fixture.cov.add]];
        .qt.fixture.cov.branch[1]}
      ;enlist (::);.qt.cov.only[`.qt.fixture.cov.branch]];
    .qunit.assertEquals[first outer`iterations;1;
        "the outer measurement survives a run nested inside it"]};

.qt.cov.test_a_nested_run_reports_its_own_numbers:{[t]
    / `::` assigns the GLOBAL `.qt.cov.inner`, so the result is read back
    / through that name rather than through a local of the same spelling.
    `.qt.cov.inner set (::);
    .cov.run[{[]
        `.qt.cov.inner set .cov.run[.qt.fixture.cov.add;1 2;.qt.cov.only[`.qt.fixture.cov.add]];
        .qt.fixture.cov.branch[1]}
      ;enlist (::);.qt.cov.only[`.qt.fixture.cov.branch]];
    .qunit.assertEquals[first .qt.cov.inner`iterations;1;"and the inner one is still measured"]};

/ --- the report -------------------------------------------------------------

/ Two single-wildcard patterns rather than one "*<<<*>>>*": q's `like`
/ throws a bare 'nyi on more than one interior `*`, so the obvious pattern
/ fails as an ERROR rather than as a mismatch - which reads like a broken
/ tool rather than a broken test.
.qt.cov.test_the_report_marks_an_untaken_branch:{[t]
    out:.cov.format.go .cov.run[.qt.fixture.cov.branch;enlist 1;.qt.cov.only[`.qt.fixture.cov.branch]];
    .qunit.assertTrue[any out like "*<<<*";"an unexecuted section opens with <<<"];
    .qunit.assertTrue[any out like "*>>>*";"and closes with >>>"]};

.qt.cov.test_a_line_holding_unrun_code_is_flagged:{[t]
    out:.cov.format.go .cov.run[.qt.fixture.cov.branch;enlist 1;.qt.cov.only[`.qt.fixture.cov.branch]];
    .qunit.assertTrue[any out like "X *";"and its line is prefixed with X"]};

.qt.cov.test_a_fully_covered_function_is_marked_nowhere:{[t]
    out:.cov.format.go .cov.run[.qt.fixture.cov.add;1 2;.qt.cov.only[`.qt.fixture.cov.add]];
    .qunit.assertEquals[any out like "*<<<*";0b;"nothing to mark when everything ran"];
    .qunit.assertTrue[any out like "*100*";"and it reports as complete"]};

.qt.cov.test_the_report_counts_the_incomplete_functions:{[t]
    out:.cov.format.go .cov.run[.qt.fixture.cov.branch;enlist 1;.qt.cov.only[`.qt.fixture.cov.branch]];
    .qunit.assertTrue[any out like "1 function(s) with incomplete coverage*";
        "the summary says how many need attention"]};

/ #1342: "100" must mean complete. Rounding printed 99.95 - one untaken
/ character in 2000 - as 100, beside "partial: 1" for the same function.
.qt.cov.test_a_percent_short_of_100_never_prints_as_100:{[t]
    .qunit.assertEquals[.cov.pct1 100f*1999%2000;"99.9";"1999 of 2000 is 99.9, not 100"];
    .qunit.assertEquals[.cov.pct1 99.99;"99.9";"truncated, however close"];
    .qunit.assertEquals[.cov.pct1 100f;"100";"and complete still says 100"]};

.qt.cov.test_a_percent_is_not_truncated_below_its_value_by_float_error:{[t]
    .qunit.assertEquals[.cov.pct1 100f*29%100;"29";"28.999999999999996 is 29, not 28.9"];
    .qunit.assertEquals[.cov.pct1 12.34;"12.3";"one decimal place"]};

/ #1341: fewer than ten functions are each listed once - `10#` cycled a
/ short table, so one function filled all ten lines.
.qt.cov.test_least_covered_lists_each_function_once:{[t]
    out:.cov.format.summary .cov.run[.qt.fixture.cov.add;1 2;.qt.cov.only[`.qt.fixture.cov.add]];
    .qunit.assertEquals[(1+out?"Least covered:")_out;enlist "  .qt.fixture.cov.add  100%";"one function run, one line"]};

.qt.cov.test_display_prints_and_returns_nothing:{[t]
    .qunit.assertEquals[.cov.format.display .cov.run[.qt.fixture.cov.add;1 2;.qt.cov.only[`.qt.fixture.cov.add]];
        (::);
        "display is for its effect, and returns null like the API says"]};
