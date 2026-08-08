// test_risk.q - tests for src/risk.q. Load src/stats.q, src/risk.q,
// tests/lib/qunit.q and tests/lib/testutil.q before this file.

\d .risktest

testPipValueKnown:{[t] .testutil.assertApprox[.uqf.pip_value[1000000;10000];100f;1e-9;"1,000,000 notional, 10000 pip factor -> 100 per pip"]};
testPipValueJpyPipFactor:{[t] .testutil.assertApprox[.uqf.pip_value[1000000;100];10000f;1e-9;"JPY-style pip factor of 100"]};

testPnlLongProfit:{[t] .testutil.assertApprox[.uqf.pnl[1000000;1.1000;1.1050;1];5000f;1e-9;"long, price up -> profit"]};
testPnlLongLoss:{[t] .testutil.assertApprox[.uqf.pnl[1000000;1.1000;1.0950;1];-5000f;1e-9;"long, price down -> loss"]};
testPnlShortProfit:{[t] .testutil.assertApprox[.uqf.pnl[1000000;1.1000;1.0950;-1];5000f;1e-9;"short, price down -> profit"]};
testPnlShortLoss:{[t] .testutil.assertApprox[.uqf.pnl[1000000;1.1000;1.1050;-1];-5000f;1e-9;"short, price up -> loss"]};
testPnlZeroWhenPriceUnchanged:{[t] .testutil.assertApprox[.uqf.pnl[1000000;1.1000;1.1000;1];0f;1e-9;"no price move -> no P&L"]};
testPnlZeroNotional:{[t] .testutil.assertApprox[.uqf.pnl[0;1.1000;1.1050;1];0f;1e-9;"zero notional -> zero P&L regardless of move"]};

testCarryReturnKnown:{[t] .testutil.assertApprox[.uqf.carry_return[1.10;1.1050];0.004545455;1e-6;"(1.1050-1.10)/1.10"]};
testCarryReturnZeroWhenFlat:{[t] .testutil.assertApprox[.uqf.carry_return[1.10;1.10];0f;1e-9;"forward=spot -> zero carry"]};
testCarryPnlKnown:{[t] .testutil.assertApprox[.uqf.carry_pnl[1000000;1.10;1.1050];5000f;1e-6;"notional * (fwd-spot)"]};

testCarryPnlConsistentWithCarryReturn:{[t]
    notional:1000000; spot:1.2500; fwd:1.2620;
    lhs:.uqf.carry_pnl[notional;spot;fwd];
    rhs:notional*.uqf.carry_return[spot;fwd]*spot;
    .testutil.assertApprox[lhs;rhs;1e-6;"carry_pnl = notional*carry_return*spot identity"]};

testVarParametricZeroAtFiftyPercentConfidence:{[t] .testutil.assertApprox[.uqf.var_parametric[1000000;0.10;1;0.5];0f;1e-6;"inv_ncdf(0.5)=0 -> zero VaR at 50% confidence"]};

testVarParametricKnownExample:{[t]
    expected:1000000*0.10*sqrt[1%252]*1.644854;
    .testutil.assertApprox[.uqf.var_parametric[1000000;0.10;1%252;0.95];expected;1e-2;"1-day 95% VaR on 1mm notional, 10% annual vol"]};

testVarParametricScalesWithSqrtTime:{[t]
    notional:1000000; vol:0.12; confidence:0.99;
    varOneYear:.uqf.var_parametric[notional;vol;1;confidence];
    varFourYears:.uqf.var_parametric[notional;vol;4;confidence];
    .testutil.assertApprox[varFourYears;2*varOneYear;1e-6;"VaR scales with sqrt(t): 4y VaR = 2x 1y VaR"]};

testVarParametricScalesLinearlyWithNotional:{[t]
    varSmall:.uqf.var_parametric[1000000;0.10;1;0.95];
    varLarge:.uqf.var_parametric[2000000;0.10;1;0.95];
    .testutil.assertApprox[varLarge;2*varSmall;1e-6;"VaR scales linearly with notional"]};

testVarParametricHigherConfidenceIsLargerVar:{[t]
    var95:.uqf.var_parametric[1000000;0.10;1;0.95];
    var99:.uqf.var_parametric[1000000;0.10;1;0.99];
    .qunit.assertTrue[var99>var95;"99% VaR must exceed 95% VaR for the same book"]};

/ Deterministic historical P&L series: -100, -99, ..., 98, 99 (200 outcomes)
testVarHistorical95:{[t]
    pnlSeries:-100+til 200;
    .testutil.assertApprox[.uqf.var_historical[pnlSeries;0.95];90f;1e-9;"5th percentile of a known series"]};

testVarHistorical99:{[t]
    pnlSeries:-100+til 200;
    .testutil.assertApprox[.uqf.var_historical[pnlSeries;0.99];98f;1e-9;"1st percentile of a known series"]};

testVarHistoricalMonotonicInConfidence:{[t]
    pnlSeries:-100+til 200;
    var90:.uqf.var_historical[pnlSeries;0.90];
    var95:.uqf.var_historical[pnlSeries;0.95];
    var99:.uqf.var_historical[pnlSeries;0.99];
    .qunit.assertTrue[(var99>=var95)&(var95>=var90);"VaR is non-decreasing as confidence increases"]};

testVarHistoricalAllLossesWorseCase:{[t]
    / every outcome a loss of exactly 50 -> VaR must be exactly 50 at any confidence
    pnlSeries:50#-50;
    .testutil.assertApprox[.uqf.var_historical[pnlSeries;0.95];50f;1e-9;"uniform -50 loss series gives VaR=50 regardless of confidence"]};

\d .
