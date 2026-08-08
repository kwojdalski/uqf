// test_ccy.q - tests for src/ccy.q (currency pair symbol conventions).
// Load src/ccy.q, tests/lib/qunit.q and tests/lib/testutil.q before this
// file.

\d .ccytest

testCcyToStrSymbol:{[t] .qunit.assertEquals[.uqf.ccyToStr `EURUSD;"EURUSD";"symbol -> string"]};
testCcyToStrAlreadyString:{[t] .qunit.assertEquals[.uqf.ccyToStr "EURUSD";"EURUSD";"string stays a single string, not exploded into chars"]};

testIsCcyPairCanonicalSymbol:{[t] .qunit.assertTrue[.uqf.isCcyPair `EURUSD;"canonical symbol is valid"]};
testIsCcyPairCanonicalString:{[t] .qunit.assertTrue[.uqf.isCcyPair "EURUSD";"canonical string is valid"]};
testIsCcyPairRejectsLowercase:{[t] .qunit.assertFalse[.uqf.isCcyPair `eurusd;"lowercase symbol is rejected"]};
testIsCcyPairRejectsMixedCase:{[t] .qunit.assertFalse[.uqf.isCcyPair "EurUsd";"mixed case is rejected"]};
testIsCcyPairRejectsSlash:{[t] .qunit.assertFalse[.uqf.isCcyPair "EUR/USD";"separator is rejected"]};
testIsCcyPairRejectsWrongLength:{[t] .qunit.assertFalse[.uqf.isCcyPair "EURUS";"5 chars is rejected"]};
testIsCcyPairRejectsDigits:{[t] .qunit.assertFalse[.uqf.isCcyPair "EUR123";"digits are rejected"]};
testIsCcyPairRejectsEmpty:{[t] .qunit.assertFalse[.uqf.isCcyPair "";"empty string is rejected"]};

testNormalizeCcyPairLowercaseSymbol:{[t] .qunit.assertEquals[.uqf.normalizeCcyPair `eurusd;`EURUSD;"lowercase symbol normalizes"]};
testNormalizeCcyPairSlash:{[t] .qunit.assertEquals[.uqf.normalizeCcyPair "EUR/USD";`EURUSD;"slash separator stripped"]};
testNormalizeCcyPairLowercaseSlash:{[t] .qunit.assertEquals[.uqf.normalizeCcyPair "eur/usd";`EURUSD;"lowercase + slash normalizes"]};
testNormalizeCcyPairDash:{[t] .qunit.assertEquals[.uqf.normalizeCcyPair "eur-usd";`EURUSD;"dash separator stripped"]};
testNormalizeCcyPairUnderscore:{[t] .qunit.assertEquals[.uqf.normalizeCcyPair "eur_usd";`EURUSD;"underscore separator stripped"]};
testNormalizeCcyPairSpace:{[t] .qunit.assertEquals[.uqf.normalizeCcyPair "eur usd";`EURUSD;"space separator stripped"]};
testNormalizeCcyPairAlreadyCanonical:{[t] .qunit.assertEquals[.uqf.normalizeCcyPair `EURUSD;`EURUSD;"already-canonical input is a no-op"]};
testNormalizeCcyPairRejectsUnrecoverable:{[t]
    wrapper:{[x] .uqf.normalizeCcyPair "EURUSDX"};
    .qunit.assertError[wrapper;::;"cannot normalize a 7-letter mess into CURCUR"]};

testCcyPairSymbolKnown:{[t] .qunit.assertEquals[.uqf.ccyPairSymbol[`EUR;`USD];`EURUSD;"builds EURUSD from EUR, USD"]};
testCcyPairSymbolLowercaseInputs:{[t] .qunit.assertEquals[.uqf.ccyPairSymbol["eur";"usd"];`EURUSD;"lowercase inputs still build canonical output"]};
testCcyPairSymbolRejectsWrongLengthBase:{[t]
    wrapper:{[x] .uqf.ccyPairSymbol[`EU;`USD]};
    .qunit.assertError[wrapper;::;"2-letter base is rejected"]};
testCcyPairSymbolRejectsWrongLengthQuote:{[t]
    wrapper:{[x] .uqf.ccyPairSymbol[`EUR;`USDX]};
    .qunit.assertError[wrapper;::;"4-letter quote is rejected"]};
testCcyPairSymbolRejectsNonAlpha:{[t]
    wrapper:{[x] .uqf.ccyPairSymbol[`EUR;`123]};
    .qunit.assertError[wrapper;::;"non-alphabetic quote is rejected"]};

testCcyPairLegsKnown:{[t]
    legs:.uqf.ccyPairLegs `EURUSD;
    .qunit.assertEquals[legs`base;`EUR;"base leg"];
    .qunit.assertEquals[legs`quote;`USD;"quote leg"]};
testCcyPairLegsAcceptsLooseFormat:{[t]
    legs:.uqf.ccyPairLegs "eur/usd";
    .qunit.assertEquals[legs`base;`EUR;"base leg from loose format"];
    .qunit.assertEquals[legs`quote;`USD;"quote leg from loose format"]};

testCcyPairSymbolAndLegsRoundTrip:{[t]
    pairs:`EURUSD`GBPJPY`AUDNZD`USDCHF;
    legsList:.uqf.ccyPairLegs each pairs;
    rebuilt:{.uqf.ccyPairSymbol[x`base;x`quote]} each legsList;
    .qunit.assertEquals[rebuilt;pairs;"split then rebuild returns the original pairs"]};

testNormalizeCcyPairIdempotent:{[t]
    once:.uqf.normalizeCcyPair "eur/usd";
    twice:.uqf.normalizeCcyPair once;
    .qunit.assertEquals[once;twice;"normalizing an already-normalized pair is a no-op"]};

\d .
