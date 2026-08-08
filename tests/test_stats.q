// test_stats.q - tests for src/stats.q (.uqf.ncdf, .uqf.npdf, .uqf.inv_ncdf,
// .uqf.horner_eval). Load src/stats.q, tests/lib/qunit.q and
// tests/lib/testutil.q before this file.

\d .statstest

testHornerEvalConstant:{[t] .testutil.assertApprox[.uqf.horner_eval[enlist 7;100];7;1e-9;"constant polynomial ignores x"]};
testHornerEvalLinear:{[t] .testutil.assertApprox[.uqf.horner_eval[2 3;5];13;1e-9;"2x+3 at x=5"]};
testHornerEvalQuadratic:{[t] .testutil.assertApprox[.uqf.horner_eval[2 3 4;5];69;1e-9;"2x^2+3x+4 at x=5"]};
testHornerEvalNegativeCoeffs:{[t] .testutil.assertApprox[.uqf.horner_eval[1 0 -1;3];8;1e-9;"x^2-1 at x=3"]};

testNcdfZero:{[t] .testutil.assertApprox[.uqf.ncdf 0f;0.5;1e-9;"N(0)=0.5"]};
testNcdfOne:{[t] .testutil.assertApprox[.uqf.ncdf 1f;0.8413447;1e-6;"N(1)"]};
testNcdfMinusOne:{[t] .testutil.assertApprox[.uqf.ncdf -1f;0.1586553;1e-6;"N(-1)"]};
testNcdfKnown196:{[t] .testutil.assertApprox[.uqf.ncdf 1.96;0.9750021;1e-5;"N(1.96) - standard 95% two-tailed z"]};
testNcdfKnownMinus196:{[t] .testutil.assertApprox[.uqf.ncdf -1.96;0.0249979;1e-5;"N(-1.96)"]};
testNcdfKnown258:{[t] .testutil.assertApprox[.uqf.ncdf 2.575829;0.995;1e-5;"N(2.575829) - standard 99% two-tailed z"]};

testNcdfSymmetry:{[t]
    xs:0.1 0.5 1 1.5 2 2.5 3;
    lhs:.uqf.ncdf[xs]+.uqf.ncdf[neg xs];
    .testutil.assertApprox[lhs;1+0*xs;1e-6;"N(x)+N(-x)=1 for several x"]};

testNpdfZero:{[t] .testutil.assertApprox[.uqf.npdf 0f;0.3989423;1e-6;"n(0)=1/sqrt(2*pi)"]};

testNpdfSymmetric:{[t]
    xs:0.3 0.7 1.2 2.1;
    .testutil.assertApprox[.uqf.npdf xs;.uqf.npdf neg xs;1e-9;"n(x)=n(-x)"]};

testInvNcdfHalf:{[t] .testutil.assertApprox[.uqf.inv_ncdf 0.5;0f;1e-6;"inv_ncdf(0.5)=0"]};
testInvNcdf975:{[t] .testutil.assertApprox[.uqf.inv_ncdf 0.975;1.959964;1e-5;"inv_ncdf(0.975) - 95% one-tailed z"]};
testInvNcdf995:{[t] .testutil.assertApprox[.uqf.inv_ncdf 0.995;2.575829;1e-5;"inv_ncdf(0.995) - 99% two-tailed z"]};
testInvNcdf99:{[t] .testutil.assertApprox[.uqf.inv_ncdf 0.99;2.326348;1e-5;"inv_ncdf(0.99) - 99% one-tailed z, common VaR level"]};
testInvNcdf95:{[t] .testutil.assertApprox[.uqf.inv_ncdf 0.95;1.644854;1e-5;"inv_ncdf(0.95) - 95% one-tailed z, common VaR level"]};
testInvNcdfLowerTail:{[t] .testutil.assertApprox[.uqf.inv_ncdf 0.001;-3.090232;1e-4;"inv_ncdf lower-tail branch (p<0.02425)"]};
testInvNcdfUpperTail:{[t] .testutil.assertApprox[.uqf.inv_ncdf 0.999;3.090232;1e-4;"inv_ncdf upper-tail branch (p>0.97575)"]};

testInvNcdfRoundTrip:{[t]
    ps:0.001 0.025 0.1 0.3 0.5 0.7 0.9 0.975 0.999;
    .testutil.assertApprox[.uqf.ncdf .uqf.inv_ncdf ps;ps;1e-6;"N(invN(p))=p round trip across the full domain"]};

testInvNcdfRejectsAtZero:{[t] .qunit.assertError[.uqf.inv_ncdf;0f;"inv_ncdf(0) is out of domain"]};
testInvNcdfRejectsAtOne:{[t] .qunit.assertError[.uqf.inv_ncdf;1f;"inv_ncdf(1) is out of domain"]};
testInvNcdfRejectsNegative:{[t] .qunit.assertError[.uqf.inv_ncdf;-0.1;"inv_ncdf(-0.1) is out of domain"]};

\d .
