/ test_numeric_assertions.q - numeric comparisons must not hide missing results.

.qt.harness.approx.test_one_null_never_matches_a_number:{[t]
    .qunit.assertFalse[.qt.support.approx[1e-9;0n;42f];"a missing result is not approximately 42"];
    .qunit.assertFalse[.qt.support.approx[1e-9;42f;0n];"a finite result is not an expected null"]};

.qt.harness.approx.test_null_positions_in_vectors_must_match:{[t]
    .qunit.assertFalse[.qt.support.approx[1e-9;1f,0n;1 42f];"one missing element fails the comparison"];
    .qunit.assertFalse[.qt.support.approx[1e-9;0n,1f;1f,0n];"missing elements in different positions do not match"];
    .qunit.assertTrue[.qt.support.approx[1e-9;0n,1f;0n,1.0000000001];"matching explicit nulls and close finite values pass"]};

.qt.harness.approx.test_finite_values_still_obey_the_tolerance:{[t]
    .qunit.assertTrue[.qt.support.approx[0.001;1f;1.0001];"inside tolerance"];
    .qunit.assertFalse[.qt.support.approx[0.001;1f;1.01];"outside tolerance"];
    .qunit.assertTrue[.qt.support.approx[0.001;1 2f;1.0001 2.0001];"finite vectors are compared element by element"]};

.qt.harness.approx.test_matching_explicit_nulls_and_empty_vectors_remain_supported:{[t]
    .qunit.assertTrue[.qt.support.approx[1e-9;0n;0n];"an explicitly expected null matches"];
    .qunit.assertTrue[.qt.support.approx[1e-9;`float$();`float$()];"two empty vectors match"]};
