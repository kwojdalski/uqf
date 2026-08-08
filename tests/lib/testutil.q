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

\d .
