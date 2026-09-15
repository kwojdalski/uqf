// test_coercion.q - tests for src/etl/core/coercion.q (.qcoer), the shared
// text-to-type layer answering E-05.
//
// The three trap classes named on issue #73 as having actually bitten the
// real sources: floats, timestamps, and symbols. Each is asserted against
// MEASURED q behaviour rather than against what one might assume - and two
// assumptions turned out to be wrong, so those are pinned here too, because
// a test that records "q already does the right thing" stops someone
// re-adding a workaround for a trap that does not exist.
//
// Load tests/lib/qunit.q, tests/lib/testutil.q and src/etl/core/coercion.q
// before this file.

\d .coertest

/ --- floats: the decimal comma DELETES the value -------------------------

/ Measured: "F"$"1,0842" is 0n. So a European decimal comma does not
/ truncate to 1f, it nulls the whole value - and a null rate reads
/ downstream as "no data for this row" rather than as a parse failure
/ anyone would investigate.
test_q_itself_loses_a_comma_decimal_entirely:{[t]
    .qunit.assertEquals[null "F"$"1,0842";1b;"the trap this function exists for: q nulls the value rather than erroring"]};

test_a_comma_decimal_is_recovered:{[t]
    .qunit.assertEquals[.qcoer.to_float["1,0842"];1.0842;"a European decimal separator survives"]};

test_a_plain_decimal_still_works:{[t]
    .qunit.assertEquals[.qcoer.to_float["1.0842"];1.0842;"the ordinary form is unaffected"]};

/ "1,234.56" is ambiguous between a thousands separator and something
/ malformed, and no rule is safe: read as European it is 1234.56, read as a
/ thousands separator it is also 1234.56, but "1,234" alone is either 1234
/ or 1.234. Refusing beats guessing, because a wrong magnitude in an FX rate
/ is not a rounding error.
test_a_mixed_comma_and_dot_is_refused_rather_than_guessed:{[t]
    .qunit.assertEquals[null .qcoer.to_float["1,234.56"];1b;"an ambiguous separator is nulled, not guessed at"]};

test_an_exponent_parses:{[t]
    .qunit.assertEquals[.qcoer.to_float["1.0842e0"];1.0842;"exponent notation is a legitimate float form"]};

test_a_negative_parses:{[t]
    .qunit.assertEquals[.qcoer.to_float["-0.5"];-0.5;"a negative is not malformed"]};

test_text_that_is_not_a_number_is_nulled:{[t]
    .qunit.assertEquals[null .qcoer.to_float["n/a"];1b;"a non-numeric field is null rather than a partial parse"]};

/ This one records that q ALREADY does the right thing. I expected "F"$"" to
/ be 0f - a missing rate becoming a real price of zero - and it is not: it
/ is 0n. Pinned so nobody adds a workaround for a trap that does not exist.
test_q_already_maps_empty_to_null_not_zero:{[t]
    .qunit.assertEquals[null "F"$"";1b;"q maps empty to null already; this layer agrees with it rather than correcting it"]};

test_empty_is_null_not_zero:{[t]
    .qunit.assertEquals[null .qcoer.to_float[""];1b;"a missing number must never read as a real zero"]};

test_whitespace_only_is_null:{[t]
    .qunit.assertEquals[null .qcoer.to_float["   "];1b;"a padded empty field is still empty"]};

test_a_long_parses_and_empties_to_null:{[t]
    .qunit.assertEquals[(.qcoer.to_long["1000000"];null .qcoer.to_long[""]);(1000000j;1b);"the same empty rule applies to integers"]};

/ --- timestamps: the worst trap -----------------------------------------

/ Measured: "P"$"2026.09.15" silently yields midnight. Every row in a day
/ then collapses onto 00:00, intraday ordering is destroyed, and every
/ markout at a positive horizon reads the wrong quote. Nothing errors.
test_q_itself_widens_a_date_to_midnight_silently:{[t]
    .qunit.assertEquals["P"$"2026.09.15";2026.09.15D00:00:00.000000000;"the trap: a date becomes a timestamp at midnight with no complaint"]};

/ So a date-only value is REFUSED rather than widened. A caller that
/ genuinely wants midnight has to say so.
test_a_date_only_value_is_refused_not_widened:{[t]
    .qunit.assertEquals[null .qcoer.to_timestamp["2026-09-15"];1b;"refusing beats destroying intraday ordering"]};

test_a_date_only_q_format_value_is_also_refused:{[t]
    .qunit.assertEquals[null .qcoer.to_timestamp["2026.09.15"];1b;"the refusal is about the missing time, not the separator style"]};

test_an_iso_timestamp_parses:{[t]
    .qunit.assertEquals[.qcoer.to_timestamp["2026-09-15T09:30:00"];2026.09.15D09:30:00.000000000;"ISO is a legitimate source format"]};

test_a_q_format_timestamp_parses:{[t]
    .qunit.assertEquals[.qcoer.to_timestamp["2026.09.15D09:30:00"];2026.09.15D09:30:00.000000000;"so is q's own"]};

test_sub_second_precision_survives:{[t]
    .qunit.assertEquals[.qcoer.to_timestamp["2026-09-15T09:30:00.123456789"];2026.09.15D09:30:00.123456789;"nanosecond precision is not rounded away"]};

test_midnight_can_be_asked_for_explicitly:{[t]
    .qunit.assertEquals[.qcoer.to_date_as_midnight["2026-09-15"];2026.09.15D00:00:00.000000000;"a caller for whom midnight is correct says so, greppably"]};

test_date_only_detection_is_exported:{[t]
    .qunit.assertEquals[.qcoer.is_date_only each ("2026-09-15";"2026-09-15T09:30:00");10b;"'did the source give me a date' is answerable directly"]};

test_a_malformed_timestamp_is_null:{[t]
    .qunit.assertEquals[null .qcoer.to_timestamp["not a date"];1b;"garbage is null rather than a partial parse"]};

/ --- symbols: case is the trap, whitespace is not -----------------------

/ Measured: `$"eurusd" does NOT match `EURUSD, and that is silent - every
/ join returns zero rows and reads as "no data for that pair".
test_q_does_not_fold_case:{[t]
    .qunit.assertEquals[(`$"eurusd")~`EURUSD;0b;"the trap: a lower-case source symbol misses every join, with no error"]};

test_case_is_folded:{[t]
    .qunit.assertEquals[.qcoer.to_symbol[" eurusd"];`EURUSD;"the library's convention is upper-case pairs throughout"]};

/ This records that I was WRONG about trailing whitespace: q's `$ trims it
/ already, the symbol is 6 characters, and it matches and joins correctly.
/ Pinned so the trim below is never cited as fixing a q behaviour.
test_q_already_trims_trailing_whitespace:{[t]
    padded:`$"EURUSD ";
    .qunit.assertEquals[(count string padded;padded~`EURUSD);(6;1b);"q trims on symbol construction, so whitespace is not the trap I assumed"]};

test_a_padded_symbol_still_joins:{[t]
    t1:([] sym:enlist `$"EURUSD "; v:enlist 1);
    t2:([] sym:enlist `EURUSD; w:enlist 2);
    .qunit.assertEquals[count t1 ij `sym xkey t2;1;"the join works, confirming q handled the padding"]};

test_the_trim_is_still_applied:{[t]
    .qunit.assertEquals[.qcoer.to_symbol["EURUSD "];`EURUSD;"defensive rather than corrective, but consistent"]};

test_case_can_be_preserved_when_it_matters:{[t]
    .qunit.assertEquals[.qcoer.to_symbol_cased[" AbC123 "];`AbC123;"an exchange order id is case-sensitive, and still gets trimmed"]};

test_an_empty_symbol_is_the_null_symbol:{[t]
    .qunit.assertEquals[.qcoer.to_symbol[""];` ;"absent is distinguishable from a real value"]};

/ --- require_present and column coercion --------------------------------

/ The coercions null rather than throw, so one bad row does not abort a
/ window. But some columns cannot be null and still mean anything, and for
/ those, publishing the null is worse than failing: it puts a hole in the
/ data that every downstream consumer must remember to check for.
test_a_mandatory_null_is_refused:{[t]
    .qunit.assertError[{.qcoer.require_present[`rate;x]};0n;"a trade with no rate must not be published"]};

test_a_present_value_passes_through:{[t]
    .qunit.assertEquals[.qcoer.require_present[`rate;1.0842];1.0842;"a real value is returned unchanged"]};

test_the_refusal_names_the_column:{[t]
    err:@[{.qcoer.require_present[`rate;x]; ""};0n;{x}];
    .qunit.assertEquals[err like "*rate*";1b;"the message names which column failed, which is all the operator needs"]};

/ A count rather than a throw, so the WORKER decides: a few bad rows in a
/ million might be tolerable and worth logging, while a column that failed
/ entirely means the format changed and the window should not be published.
test_a_column_reports_its_failure_count:{[t]
    r:.qcoer.coerce_column[.qcoer.to_float;("1.0842";"";"bad";"1.2631")];
    .qunit.assertEquals[r`failed;2;"the caller is told how many values failed, not just that some did"]};

test_a_wholly_failed_column_is_visible:{[t]
    r:.qcoer.coerce_column[.qcoer.to_timestamp;("2026-09-15";"2026-09-16")];
    .qunit.assertEquals[r`failed;2;"a date-only column fails entirely, which is how a format change announces itself"]};

test_a_clean_column_reports_no_failures:{[t]
    r:.qcoer.coerce_column[.qcoer.to_float;("1.0842";"1,2631")];
    .qunit.assertEquals[(r`failed;count r`values);(0;2);"a clean column costs nothing"]};

\d .
