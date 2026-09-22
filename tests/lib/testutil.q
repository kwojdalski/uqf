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
// The DELETE is the point. `.qmatz.init_ledger` creates the table only when
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
// .qmatz.init_ledger - so a test asserting "a foreign ledger of the right
// shape is accepted" asserts something, rather than comparing init_ledger's
// output against itself.
//
// It exists as ONE fixture because it was three hardcoded column lists, and
// every addition to .qmatz.schema broke all three at once in a way that read
// like a bug in require_schema. superseded_at did it; run_id did it
// again. test_coverage's test_the_foreign_fixture_tracks_the_declared_schema
// now fails FIRST, and by name, so the next one is a one-line fix here.
foreign_coverage_ledger:{[]
    ([] dataset:`symbol$(); partition:`symbol$(); source_version:`symbol$();
        range_from:`timestamp$();
        range_to:`timestamp$(); rows_published:`long$(); recorded_at:`timestamp$();
        superseded_at:`timestamp$(); run_id:`guid$())};

reset_coverage_ledger:{[]
    ![`.;();0b;enlist `etl_coverage];
    // The ledger is persisted now, so deleting the in-memory table is no
    // longer a reset: the next attach or stage_completion reloads whatever
    // the last suite left on disk. Remove the file too, or tests leak rows
    // into each other in run order - which is the same silent cross-test
    // dependency this helper was written to prevent.
    @[{system"rm -f ",x};.qmatz.ledger_path[];{[e] (::)}];
    .qmatz.init_ledger[];
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
// produced - every registered source has a `.qfeed.<name>`, every registered
// worker a `.qwrk.<name>`. Those registries also hold entries the TESTS put
// there: `.qbw` fixture workers named `reference`, `partial` and `fixture_*`,
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
// inside assertions, and `.qsub.nt_k`/`.qsub.nt_l` are streaming jobs
// registered by a test. Each new one would have to be remembered, and until
// it was, whichever suite ran first decided the answer.
//
// Worker instances are appended because no file declares them: `.qbw.define`
// stamps `.qwrk.<name>` from the registered name (#227).
tree_namespaces:{[]
    decls:raze {[f]
        src:read0 hsym `$f;
        3_/:src where src like "\\d .*"} each .testutil.q_files["src"];
    ns:`$decls where 1<count each decls;
    ns:ns,`$".qwrk.",/:string .testutil.etl_declaration_names["src/etl/workers"];
    asc distinct ns}

\d .
