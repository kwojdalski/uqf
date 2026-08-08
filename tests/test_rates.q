// test_rates.q - tests for src/rates.q. Load src/rates.q,
// tests/lib/qunit.q and tests/lib/testutil.q before this file.

\d .ratestest

testGrowthSimpleZeroRate:{[t] .testutil.assertApprox[.uqf.growth_simple[0f;5];1f;1e-9;"no rate -> no growth"]};
testGrowthSimpleZeroTime:{[t] .testutil.assertApprox[.uqf.growth_simple[0.05;0f];1f;1e-9;"no time -> no growth"]};
testGrowthSimpleKnown:{[t] .testutil.assertApprox[.uqf.growth_simple[0.05;1];1.05;1e-9;"5% for 1y simple"]};
testGrowthContZeroRate:{[t] .testutil.assertApprox[.uqf.growth_cont[0f;5];1f;1e-9;"no rate -> no growth"]};
testGrowthContKnown:{[t] .testutil.assertApprox[.uqf.growth_cont[0.05;1];1.051271;1e-6;"5% for 1y continuous"]};

testDfSimpleKnown:{[t] .testutil.assertApprox[.uqf.df_simple[0.05;1];0.952381;1e-6;"1/1.05"]};
testDfContKnown:{[t] .testutil.assertApprox[.uqf.df_cont[0.05;1];0.9512294;1e-6;"exp(-0.05)"]};
testDfContZeroTime:{[t] .testutil.assertApprox[.uqf.df_cont[0.07;0f];1f;1e-9;"no time -> no discounting"]};
testDfSimpleZeroTime:{[t] .testutil.assertApprox[.uqf.df_simple[0.07;0f];1f;1e-9;"no time -> no discounting"]};

testDfSimpleInvertsGrowthSimple:{[t]
    rs:0.01 0.03 0.05 0.10;
    ts:0.25 0.5 1 2;
    .testutil.assertApprox[.uqf.df_simple[rs;ts]*.uqf.growth_simple[rs;ts];1+0*rs;1e-9;"df*growth=1 (simple) across several rate/tenor pairs"]};

testDfContInvertsGrowthCont:{[t]
    rs:0.01 0.03 0.05 0.10;
    ts:0.25 0.5 1 2;
    .testutil.assertApprox[.uqf.df_cont[rs;ts]*.uqf.growth_cont[rs;ts];1+0*rs;1e-9;"df*growth=1 (continuous) across several rate/tenor pairs"]};

testSimpleToContKnown:{[t] .testutil.assertApprox[.uqf.simple_to_cont[0.05;1];0.04879016;1e-6;"ln(1.05)"]};

testSimpleToContRoundTrip:{[t]
    rs:0.005 0.02 0.05 0.08 0.15;
    ts:0.1 0.5 1 2 5;
    contRates:.uqf.simple_to_cont[rs;ts];
    .testutil.assertApprox[.uqf.cont_to_simple[contRates;ts];rs;1e-6;"cont_to_simple(simple_to_cont(r))=r round trip"]};

testSimpleContEquivalentGrowth:{[t]
    r:0.06; tt:1.5;
    contR:.uqf.simple_to_cont[r;tt];
    .testutil.assertApprox[.uqf.growth_cont[contR;tt];.uqf.growth_simple[r;tt];1e-6;"converted cont rate gives the same growth factor as the original simple rate"]};

\d .
