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
// The DELETE is the point. `.qcov.init_ledger` creates the table only when
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
// .qcov.init_ledger - so a test asserting "a foreign ledger of the right
// shape is accepted" asserts something, rather than comparing init_ledger's
// output against itself.
//
// It exists as ONE fixture because it was three hardcoded column lists, and
// every addition to .qcov.schema broke all three at once in a way that read
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
    @[{system"rm -f ",x};.qcov.ledger_path[];{[e] (::)}];
    .qcov.init_ledger[];
    value `etl_coverage};

\d .
