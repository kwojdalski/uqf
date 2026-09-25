/ run_coverage.q - whole-suite coverage, through the same .cov library an
/ interactive caller uses.
/ .
/ `scripts/test.py coverage` runs this. It is deliberately a thin driver and
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

\l tests/lib/qunit.q
\l tests/lib/testutil.q
\l src/init.q
\l src/integrations/data.q
\l docs/man.q
\l scripts/processes/torq_pipeline.q
\l scripts/processes/torq_metatables.q
\l src/etl/init.q
\l tests/lib/etl_test_doubles.q
\l tests/q/reference_worker.q
\l scripts/dev/coverage.q

/ The suite's file and namespace lists come from testutil, not from a copy
/ kept here and not from parsing run_tests.q as text: a second copy is a
/ second thing to forget, and forgetting the namespace list is how a test
/ silently never runs.
.testutil.load_suites[];
nsList:.testutil.suite_namespaces[];

/ Every namespace this tree declares, from the one enumeration in
/ src/namespaces.q - fully qualified already, and INCLUDING the nested
/ worker namespaces under .qpipe.job, which a root-level `like "q*"` scan
/ reported as the single name `qpipe.job` holding no functions. Four workers
/ vanished from this report the day they nested, silently, which is exactly
/ the failure this tool exists to reveal. `qunit` is the vendored test
/ framework, not this library's code.
namespaces:.qns.functional[] except `.qunit;

-1 "== coverage: instrumenting ",string[count namespaces]," namespace(s) ==";

results:.cov.run[{[] .qunit.runTests[nsList]}; enlist (::); (enlist `namespaces)!enlist namespaces];

/ Banner, because the suite this instruments contains .cov's OWN tests, and
/ one of them prints a two-line coverage report of its fixture. Without a
/ delimiter the reader meets "Coverage: 100% of 3 tracked character(s)"
/ immediately before the real total and has no way to tell them apart.
-1 "";
-1 "================= whole-suite coverage =================";
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
/ gap includes deleting its line.
baseline_path:`:tests/q/coverage_baseline.txt;
baseline_lines:@[read0;baseline_path;{[e] ()}];
if[0=count baseline_lines;
    -1 "";
    -1 "coverage: cannot read ",string[baseline_path]," - it is the list this run is checked against";
    exit 2];
/ Comments and blanks out; the rest are function names.
baseline:`$baseline_lines where not (baseline_lines like "#*") or 0=count each baseline_lines;

new_gaps:asc cold except baseline;
closed:asc baseline except cold;

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

if[count[new_gaps]+count closed; exit 1];

-1 "";
-1 "coverage: every uncovered function is a known one (",string[count baseline]," in the baseline)";
exit 0
