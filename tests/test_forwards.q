// test_forwards.q - tests for src/forwards.q. Load src/rates.q,
// src/forwards.q, tests/lib/qunit.q and tests/lib/testutil.q before this
// file.

\d .forwardstest

testFwdSimpleNoDifferentialIsSpot:{[t]
    rs:0.01 0.03 0.07;
    .testutil.assertApprox[.uqf.fwdSimple[1.10;rs;rs;1];1.10+0*rs;1e-9;"rd=rf -> forward=spot (simple)"]};

testFwdContNoDifferentialIsSpot:{[t]
    rs:0.01 0.03 0.07;
    .testutil.assertApprox[.uqf.fwdCont[1.10;rs;rs;1];1.10+0*rs;1e-9;"rd=rf -> forward=spot (continuous)"]};

testFwdSimpleKnownExample:{[t] .testutil.assertApprox[.uqf.fwdSimple[1.10;0.05;0.02;1];1.132353;1e-5;"EURUSD-style CIRP example, 1y"]};
testFwdContKnownExample:{[t] .testutil.assertApprox[.uqf.fwdCont[1.10;0.05;0.02;1];1.10*exp 0.03;1e-9;"continuous CIRP matches exp((rd-rf)*t) directly"]};

testFwdPointsKnown:{[t] .testutil.assertApprox[.uqf.fwdPoints[1.132353;1.10;10000];323.53;1e-1;"forward points in pips for the CIRP example"]};
testFwdPointsZeroWhenNoMove:{[t] .testutil.assertApprox[.uqf.fwdPoints[1.10;1.10;10000];0f;1e-9;"forward equals spot -> zero points"]};

testPointsToOutrightRoundTrip:{[t]
    fwd:.uqf.fwdSimple[1.10;0.05;0.02;1];
    points:.uqf.fwdPoints[fwd;1.10;10000];
    .testutil.assertApprox[.uqf.pointsToOutright[1.10;points;10000];fwd;1e-8;"points -> outright round trip"]};

testImpliedForeignRateRoundTrip:{[t]
    spot:1.2500; rd:0.045; rf:0.015; tt:0.5;
    fwd:.uqf.fwdSimple[spot;rd;rf;tt];
    .testutil.assertApprox[.uqf.impliedForeignRate[spot;fwd;rd;tt];rf;1e-8;"recovers rf from a forward built with fwdSimple"]};

testImpliedDomesticRateRoundTrip:{[t]
    spot:1.2500; rd:0.045; rf:0.015; tt:0.5;
    fwd:.uqf.fwdSimple[spot;rd;rf;tt];
    .testutil.assertApprox[.uqf.impliedDomesticRate[spot;fwd;rf;tt];rd;1e-8;"recovers rd from a forward built with fwdSimple"]};

testImpliedRateRoundTripAcrossManyScenarios:{[t]
    spots:1.10 0.90 1.25 150.0;
    rds:0.05 0.02 0.045 0.001;
    rfs:0.02 0.05 0.015 0.05;
    tts:1 0.25 0.5 2;
    fwds:.uqf.fwdSimple[spots;rds;rfs;tts];
    .testutil.assertApprox[.uqf.impliedForeignRate[spots;fwds;rds;tts];rfs;1e-6;"implied rf recovered across several currency-pair scenarios"]};

testCrossRateTriangulationKnown:{[t] .testutil.assertApprox[.uqf.crossRate[1.10;150];165f;1e-9;"EURUSD * USDJPY = EURJPY"]};

testCrossRateRoundTrip:{[t]
    ab:1.3427; bc:0.7231;
    ac:.uqf.crossRate[ab;bc];
    .testutil.assertApprox[.uqf.crossRate[ac;.uqf.invertRate bc];ab;1e-9;"(A/B*B/C)*C/B = A/B round trip via invertRate"]};

testInvertRateRoundTrip:{[t]
    rates:0.5 1.10 150.25 0.7231;
    .testutil.assertApprox[.uqf.invertRate .uqf.invertRate rates;rates;1e-9;"invert twice returns the original rate"]};

testInvertRateKnown:{[t] .testutil.assertApprox[.uqf.invertRate 2f;0.5;1e-9;"1/2=0.5"]};

testInvertBookSwapsSides:{[t]
    book:`bid`ask!(1.1000;1.1002);
    inv:.uqf.invertBook book;
    expected:`bid`ask!(1%1.1002;1%1.1000);
    .testutil.assertApprox[inv`bid;expected`bid;1e-9;"inverted bid = 1/original ask"];
    .testutil.assertApprox[inv`ask;expected`ask;1e-9;"inverted ask = 1/original bid"]};

testInvertBookRoundTrip:{[t]
    book:`bid`ask!(1.2500;1.2503);
    back:.uqf.invertBook .uqf.invertBook book;
    .testutil.assertApprox[back`bid;book`bid;1e-9;"invert twice restores bid"];
    .testutil.assertApprox[back`ask;book`ask;1e-9;"invert twice restores ask"]};

testCrossBookDirectForm:{[t]
    eurusd:`bid`ask!(1.1000;1.1002);
    usdjpy:`bid`ask!(150.00;150.02);
    r:.uqf.crossBook[`EURUSD;eurusd;`USDJPY;usdjpy];
    .qunit.assertEquals[r`sym;`EURJPY;"A/B * B/C -> A/C symbol"];
    .testutil.assertApprox[r`bid;1.10*150.00;1e-8;"synthetic bid = leg1.bid*leg2.bid"];
    .testutil.assertApprox[r`ask;1.1002*150.02;1e-8;"synthetic ask = leg1.ask*leg2.ask"]};

testCrossBookInvertSecondLeg:{[t]
    eurusd:`bid`ask!(1.1000;1.1002);
    gbpusd:`bid`ask!(1.2500;1.2503);
    r:.uqf.crossBook[`EURUSD;eurusd;`GBPUSD;gbpusd];
    .qunit.assertEquals[r`sym;`EURGBP;"A/B and C/B -> A/C symbol"];
    .testutil.assertApprox[r`bid;1.1000%1.2503;1e-8;"EUR/GBP bid = EURUSD.bid / GBPUSD.ask"];
    .testutil.assertApprox[r`ask;1.1002%1.2500;1e-8;"EUR/GBP ask = EURUSD.ask / GBPUSD.bid"]};

testCrossBookInvertFirstLeg:{[t]
    usdjpy:`bid`ask!(150.00;150.02);
    usdchf:`bid`ask!(0.9000;0.9003);
    r:.uqf.crossBook[`USDJPY;usdjpy;`USDCHF;usdchf];
    .qunit.assertEquals[r`sym;`JPYCHF;"B/A and B/C -> A/C symbol"];
    .testutil.assertApprox[r`bid;0.9000%150.02;1e-8;"JPY/CHF bid = USDCHF.bid / USDJPY.ask"];
    .testutil.assertApprox[r`ask;0.9003%150.00;1e-8;"JPY/CHF ask = USDCHF.ask / USDJPY.bid"]};

testCrossBookNeverCrossedFromValidInputs:{[t]
    books:(`bid`ask!(1.1000;1.1002);`bid`ask!(150.00;150.02);`bid`ask!(1.2500;1.2503);`bid`ask!(0.9000;0.9003));
    syms:`EURUSD`USDJPY`GBPUSD`USDCHF;
    r1:.uqf.crossBook[syms 0;books 0;syms 1;books 1];
    r2:.uqf.crossBook[syms 0;books 0;syms 2;books 2];
    r3:.uqf.crossBook[syms 1;books 1;syms 3;books 3];
    .qunit.assertFalse[.uqf.bookCrossed r1;"EUR/USD x USD/JPY synthetic book is not crossed"];
    .qunit.assertFalse[.uqf.bookCrossed r2;"EUR/USD x GBP/USD synthetic book is not crossed"];
    .qunit.assertFalse[.uqf.bookCrossed r3;"USD/JPY x USD/CHF synthetic book is not crossed"]};

testCrossBookRejectsNoSharedCurrency:{[t]
    wrapper:{[dummy] .uqf.crossBook[`EURUSD;`bid`ask!(1.10;1.1002);`GBPCHF;`bid`ask!(1.20;1.2003)]};
    .qunit.assertError[wrapper;::;"EURUSD and GBPCHF share no currency"]};

\d .
