// testutil.q - small shared helper for tolerance-based float assertions
// on top of the vendored qUnit framework (tests/lib/qunit.q).
// Load qunit.q before this file.

\d .testutil

// True if actual and expected are within tol of each other. Reduced with
// `all` so it also works when actual/expected are equal-length vectors
// (assertThat needs a single boolean, not a vector of them).
approx:{[tol;a;b] all tol>=abs a-b};

// assertThat wrapper for approximate numeric equality.
assertApprox:{[actual;expected;tol;msg] .qunit.assertThat[actual;approx[tol];expected;msg]};

// A genuinely empty etl_coverage ledger, whatever shape it currently has.
//
// The DELETE is the point. `.qetl.coverage.init_ledger` creates the table only when
// absent, which is the right contract - but it means a test that replaced the
// ledger with a differently-shaped one cannot restore it by calling
// init_ledger again: the wrong-shaped table exists, so init_ledger leaves it,
// and the bad shape leaks into every suite that runs afterwards. That is
// exactly what happened when the #60 schema-guard tests were added, and it
// broke 21 tests in two other namespaces.
//
// Also note `value`: init_ledger returns the SYMBOL `etl_coverage, so
// `0#init_ledger[]` is an empty symbol VECTOR rather than an empty table.
// Every suite healed itself on first use, because an empty vector is not in
// `tables` and init_ledger then re-created the table - silent, and invisible
// until something called `meta` directly.
// An empty coverage ledger built column by column, independently of
// .qetl.coverage.init_ledger - so a test asserting "a foreign ledger of the right
// shape is accepted" asserts something, rather than comparing init_ledger's
// output against itself.
//
// It exists as ONE fixture because it was three hardcoded column lists, and
// every addition to .qetl.coverage.schema broke all three at once in a way that read
// like a bug in require_schema. superseded_at did it; run_id did it
// again. test_coverage's test_the_foreign_fixture_tracks_the_declared_schema
// now fails FIRST, and by name, so the next one is a one-line fix here.
foreign_coverage_ledger:{[]
    ([] dataset:`symbol$(); partition:`symbol$(); source_version:`symbol$();
        range_from:`timestamp$();
        range_to:`timestamp$(); rows_published:`long$(); recorded_at:`timestamp$();
        superseded_at:`timestamp$(); run_id:0#0Ng)};

reset_coverage_ledger:{[]
    ![`.;();0b;enlist `etl_coverage];
    // The ledger is persisted now, so deleting the in-memory table is no
    // longer a reset: the next attach or stage_completion reloads whatever
    // the last suite left on disk. Remove the file too, or tests leak rows
    // into each other in run order - which is the same silent cross-test
    // dependency this helper was written to prevent.
    @[{system"rm -f ",x};.qetl.coverage.ledger_path[];{[e] (::)}];
    .qetl.coverage.init_ledger[];
    value `etl_coverage};

// ---------------------------------------------------------------------------
// What the suite IS: its files, and the namespaces they declare.
// ---------------------------------------------------------------------------
// Three scripts need this and all three used to get it by reading
// tests/run_tests.q as TEXT - run_examples.q and run_coverage.q each scanned
// it for `\l tests/q/test_*` lines to re-execute, and run_coverage.q
// additionally `value`d the line starting `nsList:`. That worked only while
// the runner listed its suites literally, and coupled three files to the
// exact spelling of a fourth.
//
// So the definition lives here, as functions, and the runner is one caller
// among three rather than the source everyone parses.

// Every test suite file under tests/q/, alphabetically.
//
// test_*.q ONLY: tests/q/ also holds scripts RUN as child processes -
// run_examples.q, run_coverage.q, upstream_instance.q,
// smoke_databento_odbc.q - and loading those here would execute them.
//
// Two clauses rather than like "test_*.q": an interior wildcard is unreliable
// in q's like (QB002), which the repository's trap gate refuses.
suite_files:{[]
    f:key `:tests/q;
    f:f where f like "*.q";
    f:asc f where f like "test_*";
    if[0=count f; '"testutil.suite_files: no tests/q/test_*.q found - wrong directory?"];
    f}

// Load every suite file. Globbed, so a new suite runs the day it is written.
load_suites:{[] {system "l tests/q/",string x} each .testutil.suite_files[];}

// The namespaces the suite files DECLARE, read from the files.
//
// From the files, not from `key `` after loading, and that distinction is
// the whole point. A test body may create a namespace at RUN time -
// test_backfill_state.q builds `.qcompletetest` as a fixture worker inside
// an assertion - and those match `*test` just as a suite does. Deriving from
// the live root therefore returns a different set depending on WHICH SUITES
// HAVE ALREADY RUN, which is not a property of the tree and cannot be
// compared against anything stable.
//
// A file may declare helper namespaces beside its suite (test_coverage_tool.q
// has `.covfix`), so only the ones named `*test` are suites.
suite_namespaces:{[]
    ns:raze {[f]
        src:read0 hsym `$"tests/q/",string f;
        decls:3_/:src where src like "\\d .*";
        decls where decls like "*test"} each .testutil.suite_files[];
    ns:asc distinct `$ns;
    if[0=count ns; '"testutil.suite_namespaces: no \\d .<name>test found in tests/q/"];
    ns}

// ---------------------------------------------------------------------------
// What the TREE declares, as opposed to what a test registered
// ---------------------------------------------------------------------------
// Several suites check a live registry against the namespaces it should have
// produced - every registered source has a `.qpipe.source.<name>`, every registered
// worker a `.qpipe.job.<name>`. Those registries also hold entries the TESTS put
// there: `.qetl.job.bounded` fixture workers named `reference`, `partial` and `fixture_*`,
// and sources registered by `etl_test_doubles.q`. None has a declaration
// file, so none has a namespace, and a test that reads the registry alone
// fails or passes on whether the suite that registered them ran first.
//
// So: the files are the tree's declarations, and a suite intersects the
// registry with these to ask about the tree rather than about the process.

// The declaration stems in one of the ETL directories: `foo.q -> `foo.
etl_declaration_names:{[dir]
    n:key hsym `$dir;
    n:n where n like "*.q";
    asc `$-2_/:string n}

// The q command a runner starts its child processes with: $QCMD if set,
// otherwise `q`, resolved by the shell on PATH. TorQ's rule (torq.sh), and
// uqs.interpreter.q_command's (#414); scripts/test.py passes QCMD on to every lane
// it runs, so the children run the binary the runner itself was started with.
q_interpreter:{[] $[""~getenv `QCMD; "q"; getenv `QCMD]}

// Every .q file under a directory, recursively.
//
// `key` on a directory returns a symbol LIST (11h) and on a file an atom
// (-11h), which is how a subdirectory is told from a file without shelling
// out.
q_files:{[dir]
    paths:(dir,"/"),/:string key hsym `$dir;
    isdir:{11h=type key hsym `$x} each paths;
    (paths where (not isdir) and paths like "*.q"),
        raze .testutil.q_files each paths where isdir}

// The namespaces this tree's own source DECLARES, from the files.
//
// The alternative - a live scan filtered by a hand-kept deny-list of test
// scaffolding - cannot hold, because suites create whole namespaces at run
// time: `.qcompletetest` and `.qmethodsonly` are fixture workers built
// inside assertions, and `.qpipe.job.nt_k`/`.qpipe.job.nt_l` are streaming jobs
// registered by a test. Each new one would have to be remembered, and until
// it was, whichever suite ran first decided the answer.
//
// Worker instances are appended because no file declares them: `.qetl.job.bounded.define`
// stamps `.qpipe.job.<name>` from the registered name (#227).
tree_namespaces:{[]
    decls:raze {[f]
        src:read0 hsym `$f;
        3_/:src where src like "\\d .*"} each .testutil.q_files["src"];
    ns:`$decls where 1<count each decls;
    ns:ns,`$".qpipe.job.",/:string .testutil.etl_declaration_names["src/etl/workers"];
    asc distinct ns}


/ Every log line `f` writes, as (level;id;text;fields) with the scoped context
/ merged in as the real .qetl.log.line merges it - with TRACE switched on for
/ the call when `trace` is 1b. The real line function and the trace switch
/ are put back however `f` ends, thrown or not: a recorder left installed
/ swallows every log line of every suite after it.
/ @param trace 1b to switch TRACE on for the call
/ @param f a niladic function
/ @return the lines, in order
captured_log:{[trace;f]
    keep:.qetl.log.line; was:.qetl.log.trace_enabled;
    if[trace; .qetl.log.trace 1b];
    `.testutil.lines set ();
    .qetl.log.line:{[level;id;text;fields] .testutil.lines,:enlist (level;id;text;.qetl.log.with_scope fields)};
    @[f;::;::];
    .qetl.log.line:keep; .qetl.log.trace was;
    .testutil.lines}

/ ------------------------------------------------- KEYED-TABLE REGISTRIES
/ .
/ .qetl.transform.registry, .qetl.source.sources, .qetl.job.bounded.worker_cfg
/ and .qetl.dag.jobs are keyed tables keyed on `name` (#512), as
/ .qalloc.methods is. A test that swaps a row in or out cannot use the
/ dictionary idioms it once did - `k _ reg`, `reg[k]:row`, `reg[k;col]:v` all
/ throw on a keyed table - so it goes through these, which keep the registry
/ declared rather than rebuilding it.

/ Drop rows from a registry by name; a name it does not hold is ignored.
/ @param reg the registry's global name, e.g. `.qetl.source.sources
/ @param names the names to drop
drop_rows:{[reg;names] ![reg;enlist (in;`name;enlist (),names);0b;`symbol$()];}

/ Store `row` under `name`, replacing any row already there.
/ @param reg the registry's global name
/ @param name the key
/ @param row the row, as the registry's def returns it
put_row:{[reg;name;row] reg upsert (enlist[`name]!enlist name),row;}

/ Set one field of one row, leaving the rest of the row as it was.
/ @param reg the registry's global name
/ @param name the key of a row the registry holds
/ @param col the column
/ @param v the value
set_field:{[reg;name;col;v] put_row[reg;name;@[(get reg) name;col;:;v]]}

/ ------------------------------------------------------ TorQ PROCESS NAME
/ .
/ Run f as if hosted by a TorQ process named p - or, with p null, as plain q
/ with no TorQ name at all - then put .proc back as it was, absent included.
/ .qetl.run.proc_name is src/'s one read of it (#619), so this is how a test
/ shows a behaviour in both hosts.
/ @param p the process name, or ` for no TorQ
/ @param f a niladic function
/ @return f's result, or (`threw;error)
with_procname:{[p;f]
    had:@[{`procname in key x};`.proc;0b];
    old:$[had; .proc.procname; `];
    $[null p; if[had; ![`.proc;();0b;enlist `procname]]; `.proc.procname set p];
    r:@[f;::;{(`threw;x)}];
    $[had; `.proc.procname set old; @[{![`.proc;();0b;enlist `procname]};::;::]];
    r}

// ------------------------------------------------------------ ISOLATION
//
// Every suite runs in ONE q process, so whatever a test changes outlives it
// unless something puts it back (#834). #801's test set
// UQS_REQUIRE_LIVE_SOURCES=1 and never cleared it, and every suite after it
// ran with live sources required. Each test restoring its own state by hand
// is a convention, and a convention is broken by the next test written.
//
// So the harness does it: .qunit.runTest - the vendored runner's per-test
// entry point, which runNsTests calls by name - is wrapped below to take a
// snapshot before each test and put it back after, whatever the test did and
// whether or not it threw. setUp/tearDown run inside the wrapper, so state a
// suite's setUp establishes is gone after each test as well.
//
// WHAT IS RESTORED:
//   environment   every variable the tree reads - the contract surface's env
//                 scan (docs/reference/surfaces/current/variables.csv) - and
//                 every one a test or the source sets with setenv. q cannot
//                 unset a variable, so one that was unset comes back as "",
//                 which getenv cannot tell apart.
//   the timer     \t, so a test that starts a timer cannot leave it firing
//                 into later suites.
//   root tables   one a test creates is dropped after it, so the next test
//                 meets the tree's own tables, not a fixture left behind.
//                 One that existed before the test is left alone: a test
//                 that changes a shared table restores it itself.

// Private: the names in `files`' `setenv[`NAME;...]` calls.
// @private
setenv_names:{[files]
    lines:raze {read0 hsym `$x} each files;
    / `[[]`: a bare `[` opens a character class in ss's pattern, as in like's
    at:ss[;"setenv[[]`"] each lines;
    names:raze {[l;i] {`$(x?";")#x} each 8_/:i _\: l}'[lines;at];
    names where {all x in .Q.an} each string names}

// The environment variables a test may change, read once at load.
env_names:{[]
    / untrapped: a harness that could not read it would isolate less, silently
    surface:exec name from (enlist "S";enlist ",") 0: `:docs/reference/surfaces/current/variables.csv;
    asc distinct surface,.testutil.setenv_names .testutil.q_files["tests/q"],.testutil.q_files["src"]}[]

// What the harness restores after each test: the environment, the timer, and
// which root tables exist.
snapshot:{[] `env`timer`tables!(.testutil.env_names!getenv each .testutil.env_names;system"t";tables `.)}

// Put back what `snapshot` took: only what changed is written.
// @param s a snapshot
restore:{[s]
    now:getenv each key s`env;
    changed:where not (s`env)~'now;
    if[count changed; setenv'[changed;(s`env) changed]];
    if[not (s`timer)=system"t"; system"t ",string s`timer];
    made:(tables `.) except s`tables;
    if[count made; ![`.;();0b;made]];
    }

// The runner's own runTest, kept once: loading this file twice must not wrap
// the wrapper, which would then call itself.
if[not `run_test_unisolated in key `.testutil; run_test_unisolated:.qunit.runTest];

// Private: one test, isolated - see ISOLATION above.
// @private
isolated_run_test:{[fn]
    s:.testutil.snapshot[];
    r:@[.testutil.run_test_unisolated;fn;{[s;e] .testutil.restore s; 'e}[s]];
    .testutil.restore s;
    r}

.qunit.runTest:isolated_run_test;

\d .
