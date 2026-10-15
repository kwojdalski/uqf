// test_stats.q - tests for src/foundation/stats.q (.qf.math.ncdf, .qf.math.npdf, .qf.math.inv_ncdf,
// .qf.math.horner_eval). Load src/foundation/stats.q, tests/lib/qunit.q and
// tests/lib/support.q before this file.


.qt.qf.math.stats.test_horner_eval_constant:{[t] .qt.support.assertApprox[.qf.math.horner_eval[enlist 7;100];7;1e-9;"constant polynomial ignores x"]};
.qt.qf.math.stats.test_horner_eval_linear:{[t] .qt.support.assertApprox[.qf.math.horner_eval[2 3;5];13;1e-9;"2x+3 at x=5"]};
.qt.qf.math.stats.test_horner_eval_quadratic:{[t] .qt.support.assertApprox[.qf.math.horner_eval[2 3 4;5];69;1e-9;"2x^2+3x+4 at x=5"]};
.qt.qf.math.stats.test_horner_eval_negative_coeffs:{[t] .qt.support.assertApprox[.qf.math.horner_eval[1 0 -1;3];8;1e-9;"x^2-1 at x=3"]};

.qt.qf.math.stats.test_ncdf_zero:{[t] .qt.support.assertApprox[.qf.math.ncdf 0f;0.5;1e-9;"N(0)=0.5"]};
.qt.qf.math.stats.test_ncdf_one:{[t] .qt.support.assertApprox[.qf.math.ncdf 1f;0.8413447;1e-6;"N(1)"]};
.qt.qf.math.stats.test_ncdf_minus_one:{[t] .qt.support.assertApprox[.qf.math.ncdf -1f;0.1586553;1e-6;"N(-1)"]};
.qt.qf.math.stats.test_ncdf_known196:{[t] .qt.support.assertApprox[.qf.math.ncdf 1.96;0.9750021;1e-5;"N(1.96) - standard 95% two-tailed z"]};
.qt.qf.math.stats.test_ncdf_known_minus196:{[t] .qt.support.assertApprox[.qf.math.ncdf -1.96;0.0249979;1e-5;"N(-1.96)"]};
.qt.qf.math.stats.test_ncdf_known258:{[t] .qt.support.assertApprox[.qf.math.ncdf 2.575829;0.995;1e-5;"N(2.575829) - standard 99% two-tailed z"]};

.qt.qf.math.stats.test_ncdf_symmetry:{[t]
    xs:0.1 0.5 1 1.5 2 2.5 3;
    lhs:.qf.math.ncdf[xs]+.qf.math.ncdf[neg xs];
    .qt.support.assertApprox[lhs;1+0*xs;1e-6;"N(x)+N(-x)=1 for several x"]};

.qt.qf.math.stats.test_npdf_zero:{[t] .qt.support.assertApprox[.qf.math.npdf 0f;0.3989423;1e-6;"n(0)=1/sqrt(2*pi)"]};

.qt.qf.math.stats.test_npdf_symmetric:{[t]
    xs:0.3 0.7 1.2 2.1;
    .qt.support.assertApprox[.qf.math.npdf xs;.qf.math.npdf neg xs;1e-9;"n(x)=n(-x)"]};

.qt.qf.math.stats.test_inv_ncdf_half:{[t] .qt.support.assertApprox[.qf.math.inv_ncdf 0.5;0f;1e-6;"inv_ncdf(0.5)=0"]};
.qt.qf.math.stats.test_inv_ncdf975:{[t] .qt.support.assertApprox[.qf.math.inv_ncdf 0.975;1.959964;1e-5;"inv_ncdf(0.975) - 95% one-tailed z"]};
.qt.qf.math.stats.test_inv_ncdf995:{[t] .qt.support.assertApprox[.qf.math.inv_ncdf 0.995;2.575829;1e-5;"inv_ncdf(0.995) - 99% two-tailed z"]};
.qt.qf.math.stats.test_inv_ncdf99:{[t] .qt.support.assertApprox[.qf.math.inv_ncdf 0.99;2.326348;1e-5;"inv_ncdf(0.99) - 99% one-tailed z, common VaR level"]};
.qt.qf.math.stats.test_inv_ncdf95:{[t] .qt.support.assertApprox[.qf.math.inv_ncdf 0.95;1.644854;1e-5;"inv_ncdf(0.95) - 95% one-tailed z, common VaR level"]};
.qt.qf.math.stats.test_inv_ncdf_lower_tail:{[t] .qt.support.assertApprox[.qf.math.inv_ncdf 0.001;-3.090232;1e-4;"inv_ncdf lower-tail branch (p<0.02425)"]};
.qt.qf.math.stats.test_inv_ncdf_upper_tail:{[t] .qt.support.assertApprox[.qf.math.inv_ncdf 0.999;3.090232;1e-4;"inv_ncdf upper-tail branch (p>0.97575)"]};

.qt.qf.math.stats.test_inv_ncdf_round_trip:{[t]
    ps:0.001 0.025 0.1 0.3 0.5 0.7 0.9 0.975 0.999;
    .qt.support.assertApprox[.qf.math.ncdf .qf.math.inv_ncdf ps;ps;1e-6;"N(invN(p))=p round trip across the full domain"]};

.qt.qf.math.stats.test_inv_ncdf_rejects_at_zero:{[t] .qunit.assertError[.qf.math.inv_ncdf;0f;"inv_ncdf(0) is out of domain"]};
.qt.qf.math.stats.test_inv_ncdf_rejects_at_one:{[t] .qunit.assertError[.qf.math.inv_ncdf;1f;"inv_ncdf(1) is out of domain"]};
.qt.qf.math.stats.test_inv_ncdf_rejects_negative:{[t] .qunit.assertError[.qf.math.inv_ncdf;-0.1;"inv_ncdf(-0.1) is out of domain"]};
