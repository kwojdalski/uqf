// test_daycount.q - tests for src/foundation/daycount.q. Load src/foundation/daycount.q,
// tests/lib/qunit.q and tests/lib/support.q before this file.


.qt.qf.px.daycount.test_dcf_act360_leap_year:{[t] .qt.support.assertApprox[.qf.px.dcf_act_360[2024.01.01;2025.01.01];1.016667;1e-5;"366 actual days (2024 is a leap year) / 360"]};
.qt.qf.px.daycount.test_dcf_act360_non_leap_year:{[t] .qt.support.assertApprox[.qf.px.dcf_act_360[2023.01.01;2024.01.01];1.013889;1e-5;"365 actual days / 360"]};
.qt.qf.px.daycount.test_dcf_act365_leap_year:{[t] .qt.support.assertApprox[.qf.px.dcf_act_365[2024.01.01;2025.01.01];1.00274;1e-5;"366 actual days / 365"]};
.qt.qf.px.daycount.test_dcf_act365_exact_year:{[t] .qt.support.assertApprox[.qf.px.dcf_act_365[2023.01.01;2024.01.01];1f;1e-5;"365 actual days / 365 = 1.0 exactly"]};
.qt.qf.px.daycount.test_dcf_act360_same_date:{[t] .qt.support.assertApprox[.qf.px.dcf_act_360[2024.06.15;2024.06.15];0f;1e-9;"zero days apart"]};
.qt.qf.px.daycount.test_dcf_act365_same_date:{[t] .qt.support.assertApprox[.qf.px.dcf_act_365[2024.06.15;2024.06.15];0f;1e-9;"zero days apart"]};

.qt.qf.px.daycount.test_dcf30_e360_full_calendar_year:{[t] .qt.support.assertApprox[.qf.px.dcf_30e_360[2024.01.01;2025.01.01];1f;1e-9;"same day/month a year apart = exactly 1.0 under 30/360"]};
.qt.qf.px.daycount.test_dcf30_e360_one_month:{[t] .qt.support.assertApprox[.qf.px.dcf_30e_360[2024.01.15;2024.02.15];0.08333333;1e-6;"30/360 = 30 days"]};
.qt.qf.px.daycount.test_dcf30_e360_month_end_capped:{[t] .qt.support.assertApprox[.qf.px.dcf_30e_360[2024.01.31;2024.03.31];0.1666667;1e-6;"both day-of-month 31 capped to 30 -> 60/360"]};
.qt.qf.px.daycount.test_dcf30_e360_same_date:{[t] .qt.support.assertApprox[.qf.px.dcf_30e_360[2024.06.15;2024.06.15];0f;1e-9;"zero elapsed time"]};

.qt.qf.px.daycount.test_year_frac_dispatch_act360:{[t] .qt.support.assertApprox[.qf.px.year_frac[`act360;2024.01.01;2025.01.01];.qf.px.dcf_act_360[2024.01.01;2025.01.01];1e-9;"year_frac dispatches to dcf_act_360"]};
.qt.qf.px.daycount.test_year_frac_dispatch_act365:{[t] .qt.support.assertApprox[.qf.px.year_frac[`act365;2024.01.01;2025.01.01];.qf.px.dcf_act_365[2024.01.01;2025.01.01];1e-9;"year_frac dispatches to dcf_act_365"]};
.qt.qf.px.daycount.test_year_frac_dispatch30e360:{[t] .qt.support.assertApprox[.qf.px.year_frac[`30e360;2024.01.01;2025.01.01];.qf.px.dcf_30e_360[2024.01.01;2025.01.01];1e-9;"year_frac dispatches to dcf_30e_360"]};
.qt.qf.px.daycount.test_year_frac_rejects_unknown_convention:{[t]
    wrapper:{[dates] .qf.px.year_frac[`bogus;dates 0;dates 1]};
    .qunit.assertError[wrapper;(2024.01.01;2025.01.01);"unknown convention symbol should error"]};
