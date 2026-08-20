// test_ccy.q - tests for src/ccy.q (currency pair symbol conventions).
// Load src/ccy.q, tests/lib/qunit.q and tests/lib/testutil.q before this
// file.

\d .ccytest

test_ccy_to_str_symbol:{[t] .qunit.assertEquals[.uqf.ccy_to_str `EURUSD;"EURUSD";"symbol -> string"]};
test_ccy_to_str_already_string:{[t] .qunit.assertEquals[.uqf.ccy_to_str "EURUSD";"EURUSD";"string stays a single string, not exploded into chars"]};

test_is_ccy_pair_canonical_symbol:{[t] .qunit.assertTrue[.uqf.is_ccy_pair `EURUSD;"canonical symbol is valid"]};
test_is_ccy_pair_canonical_string:{[t] .qunit.assertTrue[.uqf.is_ccy_pair "EURUSD";"canonical string is valid"]};
test_is_ccy_pair_rejects_lowercase:{[t] .qunit.assertFalse[.uqf.is_ccy_pair `eurusd;"lowercase symbol is rejected"]};
test_is_ccy_pair_rejects_mixed_case:{[t] .qunit.assertFalse[.uqf.is_ccy_pair "EurUsd";"mixed case is rejected"]};
test_is_ccy_pair_rejects_slash:{[t] .qunit.assertFalse[.uqf.is_ccy_pair "EUR/USD";"separator is rejected"]};
test_is_ccy_pair_rejects_wrong_length:{[t] .qunit.assertFalse[.uqf.is_ccy_pair "EURUS";"5 chars is rejected"]};
test_is_ccy_pair_rejects_digits:{[t] .qunit.assertFalse[.uqf.is_ccy_pair "EUR123";"digits are rejected"]};
test_is_ccy_pair_rejects_empty:{[t] .qunit.assertFalse[.uqf.is_ccy_pair "";"empty string is rejected"]};

test_normalize_ccy_pair_lowercase_symbol:{[t] .qunit.assertEquals[.uqf.normalize_ccy_pair `eurusd;`EURUSD;"lowercase symbol normalizes"]};
test_normalize_ccy_pair_slash:{[t] .qunit.assertEquals[.uqf.normalize_ccy_pair "EUR/USD";`EURUSD;"slash separator stripped"]};
test_normalize_ccy_pair_lowercase_slash:{[t] .qunit.assertEquals[.uqf.normalize_ccy_pair "eur/usd";`EURUSD;"lowercase + slash normalizes"]};
test_normalize_ccy_pair_dash:{[t] .qunit.assertEquals[.uqf.normalize_ccy_pair "eur-usd";`EURUSD;"dash separator stripped"]};
test_normalize_ccy_pair_underscore:{[t] .qunit.assertEquals[.uqf.normalize_ccy_pair "eur_usd";`EURUSD;"underscore separator stripped"]};
test_normalize_ccy_pair_space:{[t] .qunit.assertEquals[.uqf.normalize_ccy_pair "eur usd";`EURUSD;"space separator stripped"]};
test_normalize_ccy_pair_already_canonical:{[t] .qunit.assertEquals[.uqf.normalize_ccy_pair `EURUSD;`EURUSD;"already-canonical input is a no-op"]};
test_normalize_ccy_pair_rejects_unrecoverable:{[t]
    wrapper:{[x] .uqf.normalize_ccy_pair "EURUSDX"};
    .qunit.assertError[wrapper;::;"cannot normalize a 7-letter mess into CURCUR"]};

test_ccy_pair_symbol_known:{[t] .qunit.assertEquals[.uqf.ccy_pair_symbol[`EUR;`USD];`EURUSD;"builds EURUSD from EUR, USD"]};
test_ccy_pair_symbol_lowercase_inputs:{[t] .qunit.assertEquals[.uqf.ccy_pair_symbol["eur";"usd"];`EURUSD;"lowercase inputs still build canonical output"]};
test_ccy_pair_symbol_rejects_wrong_length_base:{[t]
    wrapper:{[x] .uqf.ccy_pair_symbol[`EU;`USD]};
    .qunit.assertError[wrapper;::;"2-letter base is rejected"]};
test_ccy_pair_symbol_rejects_wrong_length_quote:{[t]
    wrapper:{[x] .uqf.ccy_pair_symbol[`EUR;`USDX]};
    .qunit.assertError[wrapper;::;"4-letter quote is rejected"]};
test_ccy_pair_symbol_rejects_non_alpha:{[t]
    wrapper:{[x] .uqf.ccy_pair_symbol[`EUR;`123]};
    .qunit.assertError[wrapper;::;"non-alphabetic quote is rejected"]};

test_ccy_pair_legs_known:{[t]
    legs:.uqf.ccy_pair_legs `EURUSD;
    .qunit.assertEquals[legs`base;`EUR;"base leg"];
    .qunit.assertEquals[legs`quote;`USD;"quote leg"]};
test_ccy_pair_legs_accepts_loose_format:{[t]
    legs:.uqf.ccy_pair_legs "eur/usd";
    .qunit.assertEquals[legs`base;`EUR;"base leg from loose format"];
    .qunit.assertEquals[legs`quote;`USD;"quote leg from loose format"]};

test_ccy_pair_symbol_and_legs_round_trip:{[t]
    pairs:`EURUSD`GBPJPY`AUDNZD`USDCHF;
    legs_list:.uqf.ccy_pair_legs each pairs;
    rebuilt:{.uqf.ccy_pair_symbol[x`base;x`quote]} each legs_list;
    .qunit.assertEquals[rebuilt;pairs;"split then rebuild returns the original pairs"]};

test_normalize_ccy_pair_idempotent:{[t]
    once:.uqf.normalize_ccy_pair "eur/usd";
    twice:.uqf.normalize_ccy_pair once;
    .qunit.assertEquals[once;twice;"normalizing an already-normalized pair is a no-op"]};

\d .
