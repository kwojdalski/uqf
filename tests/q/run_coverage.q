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
/ is called through that copy and never counted. `.qio.memory` holds
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
\l scripts/torq_pipeline.q
\l scripts/torq_metatables.q
\l src/etl/init.q
\l tests/lib/etl_test_doubles.q
\l tests/q/reference_worker.q
\l scripts/coverage.q

/ The suite's own file and namespace lists, read from run_tests.q rather than
/ copied: a second copy is a second thing to forget, and forgetting the
/ namespace list is how a test silently never runs.
run_tests_src:read0 `:tests/run_tests.q;
/ `1_l` drops the backslash and leaves `l path`, which `system` runs as the
/ q command. Dropping three characters leaves a bare path, which `system`
/ hands to the SHELL - and the shell's "Permission denied" names the file,
/ so it reads like a file-mode problem rather than a q one.
{[l] if[l like "\\l tests/q/test_*"; system 1_l]} each run_tests_src;
value first run_tests_src where run_tests_src like "nsList:*";

/ Every namespace this tree declares, from the one enumeration in
/ src/namespaces.q - fully qualified already, and INCLUDING the nested
/ worker namespaces under .qwrk, which a root-level `like "q*"` scan
/ reported as the single name `qwrk` holding no functions. Four workers
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

exit 0
