// test_rates.q - tests for src/rates.q. Load src/rates.q,
// tests/lib/qunit.q and tests/lib/testutil.q before this file.

\d .ratestest

test_growth_simple_zero_rate:{[t] .testutil.assertApprox[.uqf.growth_simple[0f;5];1f;1e-9;"no rate -> no growth"]};
test_growth_simple_zero_time:{[t] .testutil.assertApprox[.uqf.growth_simple[0.05;0f];1f;1e-9;"no time -> no growth"]};
test_growth_simple_known:{[t] .testutil.assertApprox[.uqf.growth_simple[0.05;1];1.05;1e-9;"5% for 1y simple"]};
test_growth_cont_zero_rate:{[t] .testutil.assertApprox[.uqf.growth_cont[0f;5];1f;1e-9;"no rate -> no growth"]};
test_growth_cont_known:{[t] .testutil.assertApprox[.uqf.growth_cont[0.05;1];1.051271;1e-6;"5% for 1y continuous"]};

test_df_simple_known:{[t] .testutil.assertApprox[.uqf.df_simple[0.05;1];0.952381;1e-6;"1/1.05"]};
test_df_cont_known:{[t] .testutil.assertApprox[.uqf.df_cont[0.05;1];0.9512294;1e-6;"exp(-0.05)"]};
test_df_cont_zero_time:{[t] .testutil.assertApprox[.uqf.df_cont[0.07;0f];1f;1e-9;"no time -> no discounting"]};
test_df_simple_zero_time:{[t] .testutil.assertApprox[.uqf.df_simple[0.07;0f];1f;1e-9;"no time -> no discounting"]};

test_df_simple_inverts_growth_simple:{[t]
    rs:0.01 0.03 0.05 0.10;
    ts:0.25 0.5 1 2;
    .testutil.assertApprox[.uqf.df_simple[rs;ts]*.uqf.growth_simple[rs;ts];1+0*rs;1e-9;"df*growth=1 (simple) across several rate/tenor pairs"]};

test_df_cont_inverts_growth_cont:{[t]
    rs:0.01 0.03 0.05 0.10;
    ts:0.25 0.5 1 2;
    .testutil.assertApprox[.uqf.df_cont[rs;ts]*.uqf.growth_cont[rs;ts];1+0*rs;1e-9;"df*growth=1 (continuous) across several rate/tenor pairs"]};

test_simple_to_cont_known:{[t] .testutil.assertApprox[.uqf.simple_to_cont[0.05;1];0.04879016;1e-6;"ln(1.05)"]};

test_simple_to_cont_round_trip:{[t]
    rs:0.005 0.02 0.05 0.08 0.15;
    ts:0.1 0.5 1 2 5;
    cont_rates:.uqf.simple_to_cont[rs;ts];
    .testutil.assertApprox[.uqf.cont_to_simple[cont_rates;ts];rs;1e-6;"cont_to_simple(simple_to_cont(r))=r round trip"]};

test_simple_cont_equivalent_growth:{[t]
    r:0.06; tt:1.5;
    cont_r:.uqf.simple_to_cont[r;tt];
    .testutil.assertApprox[.uqf.growth_cont[cont_r;tt];.uqf.growth_simple[r;tt];1e-6;"converted cont rate gives the same growth factor as the original simple rate"]};

\d .
