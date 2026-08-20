// test_risk.q - tests for src/risk.q. Load src/stats.q, src/risk.q,
// tests/lib/qunit.q and tests/lib/testutil.q before this file.

\d .risktest

test_pip_value_known:{[t] .testutil.assertApprox[.uqf.pip_value[1000000;10000];100f;1e-9;"1,000,000 notional, 10000 pip factor -> 100 per pip"]};
test_pip_value_jpy_pip_factor:{[t] .testutil.assertApprox[.uqf.pip_value[1000000;100];10000f;1e-9;"JPY-style pip factor of 100"]};

test_pnl_long_profit:{[t] .testutil.assertApprox[.uqf.pnl[1000000;1.1000;1.1050;1];5000f;1e-9;"long, price up -> profit"]};
test_pnl_long_loss:{[t] .testutil.assertApprox[.uqf.pnl[1000000;1.1000;1.0950;1];-5000f;1e-9;"long, price down -> loss"]};
test_pnl_short_profit:{[t] .testutil.assertApprox[.uqf.pnl[1000000;1.1000;1.0950;-1];5000f;1e-9;"short, price down -> profit"]};
test_pnl_short_loss:{[t] .testutil.assertApprox[.uqf.pnl[1000000;1.1000;1.1050;-1];-5000f;1e-9;"short, price up -> loss"]};
test_pnl_zero_when_price_unchanged:{[t] .testutil.assertApprox[.uqf.pnl[1000000;1.1000;1.1000;1];0f;1e-9;"no price move -> no P&L"]};
test_pnl_zero_notional:{[t] .testutil.assertApprox[.uqf.pnl[0;1.1000;1.1050;1];0f;1e-9;"zero notional -> zero P&L regardless of move"]};

test_carry_return_known:{[t] .testutil.assertApprox[.uqf.carry_return[1.10;1.1050];0.004545455;1e-6;"(1.1050-1.10)/1.10"]};
test_carry_return_zero_when_flat:{[t] .testutil.assertApprox[.uqf.carry_return[1.10;1.10];0f;1e-9;"forward=spot -> zero carry"]};
test_carry_pnl_known:{[t] .testutil.assertApprox[.uqf.carry_pnl[1000000;1.10;1.1050];5000f;1e-6;"notional * (fwd-spot)"]};

test_carry_pnl_consistent_with_carry_return:{[t]
    notional:1000000; spot:1.2500; fwd:1.2620;
    lhs:.uqf.carry_pnl[notional;spot;fwd];
    rhs:notional*.uqf.carry_return[spot;fwd]*spot;
    .testutil.assertApprox[lhs;rhs;1e-6;"carry_pnl = notional*carry_return*spot identity"]};

test_var_parametric_zero_at_fifty_percent_confidence:{[t] .testutil.assertApprox[.uqf.var_parametric[1000000;0.10;1;0.5];0f;1e-6;"inv_ncdf(0.5)=0 -> zero VaR at 50% confidence"]};

test_var_parametric_known_example:{[t]
    expected:1000000*0.10*sqrt[1%252]*1.644854;
    .testutil.assertApprox[.uqf.var_parametric[1000000;0.10;1%252;0.95];expected;1e-2;"1-day 95% VaR on 1mm notional, 10% annual vol"]};

test_var_parametric_scales_with_sqrt_time:{[t]
    notional:1000000; vol:0.12; confidence:0.99;
    var_one_year:.uqf.var_parametric[notional;vol;1;confidence];
    var_four_years:.uqf.var_parametric[notional;vol;4;confidence];
    .testutil.assertApprox[var_four_years;2*var_one_year;1e-6;"VaR scales with sqrt(t): 4y VaR = 2x 1y VaR"]};

test_var_parametric_scales_linearly_with_notional:{[t]
    var_small:.uqf.var_parametric[1000000;0.10;1;0.95];
    var_large:.uqf.var_parametric[2000000;0.10;1;0.95];
    .testutil.assertApprox[var_large;2*var_small;1e-6;"VaR scales linearly with notional"]};

test_var_parametric_higher_confidence_is_larger_var:{[t]
    var95:.uqf.var_parametric[1000000;0.10;1;0.95];
    var99:.uqf.var_parametric[1000000;0.10;1;0.99];
    .qunit.assertTrue[var99>var95;"99% VaR must exceed 95% VaR for the same book"]};

/ Deterministic historical P&L series: -100, -99, ..., 98, 99 (200 outcomes)
test_var_historical95:{[t]
    pnl_series:-100+til 200;
    .testutil.assertApprox[.uqf.var_historical[pnl_series;0.95];90f;1e-9;"5th percentile of a known series"]};

test_var_historical99:{[t]
    pnl_series:-100+til 200;
    .testutil.assertApprox[.uqf.var_historical[pnl_series;0.99];98f;1e-9;"1st percentile of a known series"]};

test_var_historical_monotonic_in_confidence:{[t]
    pnl_series:-100+til 200;
    var90:.uqf.var_historical[pnl_series;0.90];
    var95:.uqf.var_historical[pnl_series;0.95];
    var99:.uqf.var_historical[pnl_series;0.99];
    .qunit.assertTrue[(var99>=var95)&(var95>=var90);"VaR is non-decreasing as confidence increases"]};

test_var_historical_all_losses_worse_case:{[t]
    / every outcome a loss of exactly 50 -> VaR must be exactly 50 at any confidence
    pnl_series:50#-50;
    .testutil.assertApprox[.uqf.var_historical[pnl_series;0.95];50f;1e-9;"uniform -50 loss series gives VaR=50 regardless of confidence"]};

\d .
