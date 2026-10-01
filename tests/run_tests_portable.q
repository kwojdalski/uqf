// run_tests_portable.q - the suites listed in tests/q/portable_suites.txt,
// with only what they need loaded, on whichever q runs this file.
//
// Exists for CI. A hosted runner has PeachQ but no KDB-X, and PeachQ cannot
// load the ETL tree yet (#511), so tests/run_tests.q cannot run there at all.
// This loads the quant library alone - src/init.q, which PeachQ loads - and
// the suites that pass on both interpreters, and exits non-zero on any
// failure, so CI can BLOCK on them. tests/run_tests.q remains the full suite,
// and KDB-X the verified interpreter.
//
// Run from the repository root: q tests/run_tests_portable.q

\c 400 1000

/ The same seed as run_tests.q, for the same reason: a failure must re-run.
\S 20260916

\l tests/lib/qunit.q
\l tests/lib/testutil.q
\l src/init.q

\d .portable

/ The listed suite files: one per line, `#` starting a comment.
listed:{[]
    lines:trim each read0 `:tests/q/portable_suites.txt;
    files:lines where (0<count each lines) and not lines like "#*";
    if[0=count files; '"run_tests_portable: tests/q/portable_suites.txt lists no suites"];
    missing:files where not (`$files) in key `:tests/q;
    if[count missing; '"run_tests_portable: listed but not in tests/q: ",", " sv missing];
    files}

/ The namespaces a suite file declares - `\d .<name>test` - read from the file,
/ as .testutil.suite_namespaces does for the whole tree.
declared:{[file]
    src:read0 hsym `$"tests/q/",file;
    ns:3_/:src where src like "\\d .*";
    ns:ns where ns like "*test";
    if[0=count ns; '"run_tests_portable: ",file," declares no \\d .<name>test namespace"];
    `$ns}

\d .

files:.portable.listed[];
{system "l tests/q/",x} each files;
nsList:distinct raze .portable.declared each files;

res:.qunit.runTests[nsList];

nTotal:count res;
nPass:sum res[`status]=`pass;
nFail:sum res[`status]=`fail;
nErr:sum res[`status]=`error;

-1 "";
-1 "================ uqf portable test summary ================";
-1 (string count files)," suites, ",(string nTotal)," tests: ",(string nPass)," passed, ",(string nFail)," failed, ",(string nErr)," errored";
-1 "============================================================";

/ Nothing ran is a failure, not a pass: a gate that tests nothing must not
/ read as green.
if[0=nTotal; -1 "run_tests_portable: no tests ran"; exit 1];

if[(nFail+nErr)>0;
    -1 "";
    -1 "Failures/errors:";
    show 0!select namespace,name,status,
        detail:{$[x=`error; y; z]}'[status;result;msg] from res where status<>`pass;
    exit 1];

exit 0
