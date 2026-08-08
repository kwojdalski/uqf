// test_daycount.q - tests for src/daycount.q. Load src/daycount.q,
// tests/lib/qunit.q and tests/lib/testutil.q before this file.

\d .daycounttest

testDcfAct360LeapYear:{[t] .testutil.assertApprox[.uqf.dcf_act_360[2024.01.01;2025.01.01];1.016667;1e-5;"366 actual days (2024 is a leap year) / 360"]};
testDcfAct360NonLeapYear:{[t] .testutil.assertApprox[.uqf.dcf_act_360[2023.01.01;2024.01.01];1.013889;1e-5;"365 actual days / 360"]};
testDcfAct365LeapYear:{[t] .testutil.assertApprox[.uqf.dcf_act_365[2024.01.01;2025.01.01];1.00274;1e-5;"366 actual days / 365"]};
testDcfAct365ExactYear:{[t] .testutil.assertApprox[.uqf.dcf_act_365[2023.01.01;2024.01.01];1f;1e-5;"365 actual days / 365 = 1.0 exactly"]};
testDcfAct360SameDate:{[t] .testutil.assertApprox[.uqf.dcf_act_360[2024.06.15;2024.06.15];0f;1e-9;"zero days apart"]};
testDcfAct365SameDate:{[t] .testutil.assertApprox[.uqf.dcf_act_365[2024.06.15;2024.06.15];0f;1e-9;"zero days apart"]};

testDcf30E360FullCalendarYear:{[t] .testutil.assertApprox[.uqf.dcf_30e_360[2024.01.01;2025.01.01];1f;1e-9;"same day/month a year apart = exactly 1.0 under 30/360"]};
testDcf30E360OneMonth:{[t] .testutil.assertApprox[.uqf.dcf_30e_360[2024.01.15;2024.02.15];0.08333333;1e-6;"30/360 = 30 days"]};
testDcf30E360MonthEndCapped:{[t] .testutil.assertApprox[.uqf.dcf_30e_360[2024.01.31;2024.03.31];0.1666667;1e-6;"both day-of-month 31 capped to 30 -> 60/360"]};
testDcf30E360SameDate:{[t] .testutil.assertApprox[.uqf.dcf_30e_360[2024.06.15;2024.06.15];0f;1e-9;"zero elapsed time"]};

testYearFracDispatchAct360:{[t] .testutil.assertApprox[.uqf.year_frac[`act360;2024.01.01;2025.01.01];.uqf.dcf_act_360[2024.01.01;2025.01.01];1e-9;"year_frac dispatches to dcf_act_360"]};
testYearFracDispatchAct365:{[t] .testutil.assertApprox[.uqf.year_frac[`act365;2024.01.01;2025.01.01];.uqf.dcf_act_365[2024.01.01;2025.01.01];1e-9;"year_frac dispatches to dcf_act_365"]};
testYearFracDispatch30e360:{[t] .testutil.assertApprox[.uqf.year_frac[`30e360;2024.01.01;2025.01.01];.uqf.dcf_30e_360[2024.01.01;2025.01.01];1e-9;"year_frac dispatches to dcf_30e_360"]};
testYearFracRejectsUnknownConvention:{[t]
    wrapper:{[dates] .uqf.year_frac[`bogus;dates 0;dates 1]};
    .qunit.assertError[wrapper;(2024.01.01;2025.01.01);"unknown convention symbol should error"]};

\d .
