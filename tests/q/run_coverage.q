/ run_coverage.q - whole-suite coverage, through the same .cov library an
/ interactive caller uses.
/ .
/ tests/qrun/test_q_coverage.py runs this (`uv run pytest -m coverage`). It is deliberately a thin driver and
/ not a second implementation: there was one of those, a Python tool that
/ instrumented the source FILES before they loaded, and two instrumenters for
/ one language is the duplication this repository has an auditor for.
/ .
/ WHAT MAKES ONE TOOL ENOUGH. In-memory instrumentation has an obvious hole -
/ a function whose VALUE was captured into a registry before instrumentation
/ is called through that copy and never counted. `.qetl.io.memory` holds
/ `write_memory`, so every bounded worker writes through a captured copy.
/ `.cov.reseed` closes it by swapping those copies too, which is why this
/ driver can replace file-level instrumentation rather than merely
/ approximate it.
/ .
/ Run from the repository root:
/   q tests/q/run_coverage.q

/ Seeded and sized as tests/run_tests.q is, so the tests run on the same
/ random draws there and here: attest_kdbx.py reads a pass here as a pass of
/ the plain run (#1704). test_seed.q says why the runner seeds. The size is
/ not cosmetic: -3! truncates to the console width, and at the default 80
/ columns two .qt.man.examples tests failed here and passed there - the only
/ disagreement between the two runs over all 3417 tests on KDB-X.
\c 400 1000
\S 20260916

\l tests/lib/qunit.q
\l tests/lib/support.q
\l src/init.q
\l src/integrations/data.q
\l docs/man.q
\l scripts/processes/torq_pipeline.q
\l scripts/processes/torq_metatables.q
\l src/etl/init.q
\l tests/lib/etl_test_doubles.q
\l tests/q/reference_worker.q
\l scripts/dev/coverage.q

/ The suite's files and its suites come from tests/lib/support.q, not from a
/ copy kept here and not from parsing run_tests.q as text: a second copy is a
/ second thing to forget, and forgetting a suite is how a test silently never
/ runs. The suites are FOUND (discovery.q), so there is no list to forget.
.qt.support.load_suites[];
.qt.support.require_layout[];
nsList:.qt.support.suites[];

/ Every namespace this tree declares, from the one enumeration in
/ src/namespaces.q - fully qualified already, and INCLUDING the nested
/ worker namespaces under .qpipe.job, which a root-level `like "q*"` scan
/ reported as the single name `qpipe.job` holding no functions. Four workers
/ vanished from this report the day they nested, silently, which is exactly
/ the failure this tool exists to reveal. `qunit` is the vendored test
/ framework, not this library's code.
namespaces:.qt.support.library_only .qf.util.functional[];
known:.cov.targets (enlist `namespaces)!enlist namespaces;

/ A SUBSET (#1719): `-files a.q,b.q -modules src/x.q,...` runs the suites the
/ files define and instruments and judges only the functions the modules
/ define - what scripts/dev/test_impact.py's coverage_for picks for a change:
/ q-tests' suites, and for each function judged a file that enters it
/ (tests/q/coverage_map.txt). Both or neither. A function the subset finds
/ newly never entered is not a verdict - the map may be stale - so it exits 3
/ and tests/qrun/test_q_coverage.py runs the whole suite, whose verdict stands. A
/ baseline entry the subset enters is one the whole suite enters too: that
/ fails here (exit 1), as it would there.
/ .
/ `-map PATH` (a whole run only) writes the coverage map: which suite files
/ enter each function (.qt.support.coverage_map).
modules:.qt.support.modules_option .z.x;
subset:not modules~(::);
if[subset<>not (::)~.qt.support.files_option .z.x;
    -1 "coverage: -files and -modules go together - a subset names its suites and the modules it judges";
    exit 2];
map_path:.qt.support.map_option .z.x;
if[subset and not map_path~(::);
    -1 "coverage: -map is written from a whole run - a subset does not see what the other suites enter";
    exit 2];
all_suites:nsList;
nsList:.qt.support.selected_suites[nsList;.z.x];
judged:.qt.support.defined_in[known;modules];

-1 "== coverage: instrumenting ",$[subset;
    string[count judged]," function(s) in ",string[count modules]," module(s), running ",string[count nsList]," suite(s)";
    string[count namespaces]," namespace(s)"]," ==";

/ The run's test outcomes are kept, not discarded: with UQF_TEST_RESULTS and
/ UQF_TEST_FAILURES set they are written as tests/run_tests.q writes them,
/ and scripts/dev/attest_kdbx.py takes q-tests' q-unit verdict from them
/ rather than running the suite a second time (#1704). This exit code does
/ not change: it is the coverage gate's, and a failing test fails q-tests.
/ A subset whose modules define no function still runs its suites, for
/ those outcomes, and has nothing to judge.
if[0=count judged;
    .qt.support.write_outcomes .qunit.runTests[nsList];
    -1 "";
    -1 "coverage: the modules define no function to judge - nothing is uncovered";
    exit 0];
first_probe:.cov.nextLine;
tracking:$[map_path~(::); (::); .qt.support.track_suites[]];
results:.cov.run[{[] `res set .qunit.runTests[nsList]}; enlist (::); (enlist `functions)!enlist judged];
if[not map_path~(::);
    .qt.support.untrack_suites tracking;
    suite_files:.qt.support.suite_files[];
    file_suites:suite_files!{[s;f] .qt.support.suites_of[s;enlist "tests/q/",string f]}[all_suites] each suite_files;
    cmap:.qt.support.coverage_map[results;first_probe;.qt.support.suite_hits;file_suites];
    (hsym `$map_path) 0: .qt.support.map_lines cmap;
    -1 "coverage: wrote the coverage map (",string[count cmap]," function(s)) to ",map_path];
.qt.support.write_outcomes res;

/ Banner, because the suite this instruments contains .cov's OWN tests, and
/ one of them prints a two-line coverage report of its fixture. Without a
/ delimiter the reader meets "Coverage: 100% of 3 tracked character(s)"
/ immediately before the real total and has no way to tell them apart.
-1 "";
-1 $[subset; "================= subset coverage ================="; "================= whole-suite coverage ================="];
-1 each .cov.format.summary results;

/ The functions nothing entered at all. Listed rather than left in a
/ percentage, because "which code did no test reach" is the question someone
/ runs this to answer, and a total cannot say it.
cold:exec name from results where iterations=0;
if[count cold;
    -1 "";
    -1 "Never entered (",string[count cold],"):";
    {[n] -1 "  ",string n} each asc cold];

/ ------------------------------------------------------------ THE GATE
/ .
/ This driver used to end `exit 0`: it reported and never refused, so a
/ function could stop being called and nothing said so. What it prints is
/ exactly what a gate needs, and printing it without checking it is the shape
/ of a check that reads as protection while asserting nothing.
/ .
/ The comparison is against tests/q/coverage_baseline.txt, and it fails in
/ BOTH directions. A new never-entered function is a blind spot that just
/ opened. A baseline entry that is now entered means the list has gone stale,
/ and a list that only ever grows stops describing anything - so closing a
/ gap includes deleting its line. A subset judges the baseline entries among
/ its functions, and any that names no function at all
/ (.qt.support.coverage_gate).
baseline_path:`:tests/q/coverage_baseline.txt;
baseline_lines:@[read0;baseline_path;{[e] ()}];
if[0=count baseline_lines;
    -1 "";
    -1 "coverage: cannot read ",string[baseline_path]," - it is the list this run is checked against";
    exit 2];
/ Comments and blanks out; the rest are function names.
baseline:`$baseline_lines where not (baseline_lines like "#*") or 0=count each baseline_lines;

gate:.qt.support.coverage_gate[cold;baseline;judged;known];
new_gaps:gate`new_gaps;
closed:gate`closed;

if[count new_gaps;
    -1 "";
    -1 "FAIL  ",string[count new_gaps]," function(s) no test enters, and not in the baseline:";
    {[n] -1 "  ",string n} each new_gaps;
    -1 "";
    -1 "      Cover them, or add them to tests/q/coverage_baseline.txt with the reason."];

if[count closed;
    -1 "";
    -1 "FAIL  ",string[count closed]," baseline entry(ies) are now covered - delete them:";
    {[n] -1 "  ",string n} each closed;
    -1 "";
    -1 "      A baseline that keeps entries after their gap is closed stops describing anything."];

if[subset and (count new_gaps) and 0=count closed;
    -1 "";
    -1 "coverage: a subset that finds a function never entered is not a verdict - a run of the whole suite decides (exit 3)";
    exit 3];
if[count[new_gaps]+count closed; exit 1];

-1 "";
-1 "coverage: every uncovered function is a known one (",string[count baseline]," in the baseline",
    $[subset; "; ",string[count judged]," function(s) judged"; ""],")";
exit 0
