// test_rates.q - tests for src/rates.q. Load src/rates.q,
// tests/lib/qunit.q and tests/lib/testutil.q before this file.

\d .ratestest

testGrowthSimpleZeroRate:{[t] .testutil.assertApprox[.uqf.growthSimple[0f;5];1f;1e-9;"no rate -> no growth"]};
testGrowthSimpleZeroTime:{[t] .testutil.assertApprox[.uqf.growthSimple[0.05;0f];1f;1e-9;"no time -> no growth"]};
testGrowthSimpleKnown:{[t] .testutil.assertApprox[.uqf.growthSimple[0.05;1];1.05;1e-9;"5% for 1y simple"]};
testGrowthContZeroRate:{[t] .testutil.assertApprox[.uqf.growthCont[0f;5];1f;1e-9;"no rate -> no growth"]};
testGrowthContKnown:{[t] .testutil.assertApprox[.uqf.growthCont[0.05;1];1.051271;1e-6;"5% for 1y continuous"]};

testDfSimpleKnown:{[t] .testutil.assertApprox[.uqf.dfSimple[0.05;1];0.952381;1e-6;"1/1.05"]};
testDfContKnown:{[t] .testutil.assertApprox[.uqf.dfCont[0.05;1];0.9512294;1e-6;"exp(-0.05)"]};
testDfContZeroTime:{[t] .testutil.assertApprox[.uqf.dfCont[0.07;0f];1f;1e-9;"no time -> no discounting"]};
testDfSimpleZeroTime:{[t] .testutil.assertApprox[.uqf.dfSimple[0.07;0f];1f;1e-9;"no time -> no discounting"]};

testDfSimpleInvertsGrowthSimple:{[t]
    rs:0.01 0.03 0.05 0.10;
    ts:0.25 0.5 1 2;
    .testutil.assertApprox[.uqf.dfSimple[rs;ts]*.uqf.growthSimple[rs;ts];1+0*rs;1e-9;"df*growth=1 (simple) across several rate/tenor pairs"]};

testDfContInvertsGrowthCont:{[t]
    rs:0.01 0.03 0.05 0.10;
    ts:0.25 0.5 1 2;
    .testutil.assertApprox[.uqf.dfCont[rs;ts]*.uqf.growthCont[rs;ts];1+0*rs;1e-9;"df*growth=1 (continuous) across several rate/tenor pairs"]};

testSimpleToContKnown:{[t] .testutil.assertApprox[.uqf.simpleToCont[0.05;1];0.04879016;1e-6;"ln(1.05)"]};

testSimpleToContRoundTrip:{[t]
    rs:0.005 0.02 0.05 0.08 0.15;
    ts:0.1 0.5 1 2 5;
    contRates:.uqf.simpleToCont[rs;ts];
    .testutil.assertApprox[.uqf.contToSimple[contRates;ts];rs;1e-6;"contToSimple(simpleToCont(r))=r round trip"]};

testSimpleContEquivalentGrowth:{[t]
    r:0.06; tt:1.5;
    contR:.uqf.simpleToCont[r;tt];
    .testutil.assertApprox[.uqf.growthCont[contR;tt];.uqf.growthSimple[r;tt];1e-6;"converted cont rate gives the same growth factor as the original simple rate"]};

\d .
