// test_ccy.q - tests for src/ccy.q (currency pair symbol conventions).
// Load src/ccy.q, tests/lib/qunit.q and tests/lib/testutil.q before this
// file.

\d .ccytest

testCcyToStrSymbol:{[t] .qunit.assertEquals[.uqf.ccy_to_str `EURUSD;"EURUSD";"symbol -> string"]};
testCcyToStrAlreadyString:{[t] .qunit.assertEquals[.uqf.ccy_to_str "EURUSD";"EURUSD";"string stays a single string, not exploded into chars"]};

testIsCcyPairCanonicalSymbol:{[t] .qunit.assertTrue[.uqf.is_ccy_pair `EURUSD;"canonical symbol is valid"]};
testIsCcyPairCanonicalString:{[t] .qunit.assertTrue[.uqf.is_ccy_pair "EURUSD";"canonical string is valid"]};
testIsCcyPairRejectsLowercase:{[t] .qunit.assertFalse[.uqf.is_ccy_pair `eurusd;"lowercase symbol is rejected"]};
testIsCcyPairRejectsMixedCase:{[t] .qunit.assertFalse[.uqf.is_ccy_pair "EurUsd";"mixed case is rejected"]};
testIsCcyPairRejectsSlash:{[t] .qunit.assertFalse[.uqf.is_ccy_pair "EUR/USD";"separator is rejected"]};
testIsCcyPairRejectsWrongLength:{[t] .qunit.assertFalse[.uqf.is_ccy_pair "EURUS";"5 chars is rejected"]};
testIsCcyPairRejectsDigits:{[t] .qunit.assertFalse[.uqf.is_ccy_pair "EUR123";"digits are rejected"]};
testIsCcyPairRejectsEmpty:{[t] .qunit.assertFalse[.uqf.is_ccy_pair "";"empty string is rejected"]};

testNormalizeCcyPairLowercaseSymbol:{[t] .qunit.assertEquals[.uqf.normalize_ccy_pair `eurusd;`EURUSD;"lowercase symbol normalizes"]};
testNormalizeCcyPairSlash:{[t] .qunit.assertEquals[.uqf.normalize_ccy_pair "EUR/USD";`EURUSD;"slash separator stripped"]};
testNormalizeCcyPairLowercaseSlash:{[t] .qunit.assertEquals[.uqf.normalize_ccy_pair "eur/usd";`EURUSD;"lowercase + slash normalizes"]};
testNormalizeCcyPairDash:{[t] .qunit.assertEquals[.uqf.normalize_ccy_pair "eur-usd";`EURUSD;"dash separator stripped"]};
testNormalizeCcyPairUnderscore:{[t] .qunit.assertEquals[.uqf.normalize_ccy_pair "eur_usd";`EURUSD;"underscore separator stripped"]};
testNormalizeCcyPairSpace:{[t] .qunit.assertEquals[.uqf.normalize_ccy_pair "eur usd";`EURUSD;"space separator stripped"]};
testNormalizeCcyPairAlreadyCanonical:{[t] .qunit.assertEquals[.uqf.normalize_ccy_pair `EURUSD;`EURUSD;"already-canonical input is a no-op"]};
testNormalizeCcyPairRejectsUnrecoverable:{[t]
    wrapper:{[x] .uqf.normalize_ccy_pair "EURUSDX"};
    .qunit.assertError[wrapper;::;"cannot normalize a 7-letter mess into CURCUR"]};

testCcyPairSymbolKnown:{[t] .qunit.assertEquals[.uqf.ccy_pair_symbol[`EUR;`USD];`EURUSD;"builds EURUSD from EUR, USD"]};
testCcyPairSymbolLowercaseInputs:{[t] .qunit.assertEquals[.uqf.ccy_pair_symbol["eur";"usd"];`EURUSD;"lowercase inputs still build canonical output"]};
testCcyPairSymbolRejectsWrongLengthBase:{[t]
    wrapper:{[x] .uqf.ccy_pair_symbol[`EU;`USD]};
    .qunit.assertError[wrapper;::;"2-letter base is rejected"]};
testCcyPairSymbolRejectsWrongLengthQuote:{[t]
    wrapper:{[x] .uqf.ccy_pair_symbol[`EUR;`USDX]};
    .qunit.assertError[wrapper;::;"4-letter quote is rejected"]};
testCcyPairSymbolRejectsNonAlpha:{[t]
    wrapper:{[x] .uqf.ccy_pair_symbol[`EUR;`123]};
    .qunit.assertError[wrapper;::;"non-alphabetic quote is rejected"]};

testCcyPairLegsKnown:{[t]
    legs:.uqf.ccy_pair_legs `EURUSD;
    .qunit.assertEquals[legs`base;`EUR;"base leg"];
    .qunit.assertEquals[legs`quote;`USD;"quote leg"]};
testCcyPairLegsAcceptsLooseFormat:{[t]
    legs:.uqf.ccy_pair_legs "eur/usd";
    .qunit.assertEquals[legs`base;`EUR;"base leg from loose format"];
    .qunit.assertEquals[legs`quote;`USD;"quote leg from loose format"]};

testCcyPairSymbolAndLegsRoundTrip:{[t]
    pairs:`EURUSD`GBPJPY`AUDNZD`USDCHF;
    legsList:.uqf.ccy_pair_legs each pairs;
    rebuilt:{.uqf.ccy_pair_symbol[x`base;x`quote]} each legsList;
    .qunit.assertEquals[rebuilt;pairs;"split then rebuild returns the original pairs"]};

testNormalizeCcyPairIdempotent:{[t]
    once:.uqf.normalize_ccy_pair "eur/usd";
    twice:.uqf.normalize_ccy_pair once;
    .qunit.assertEquals[once;twice;"normalizing an already-normalized pair is a no-op"]};

\d .
