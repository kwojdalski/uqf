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

testCrossRateSharedBaseKnown:{[t]
    / EURPLN=4.30, EURUSD=1.075 -> USDPLN=4.30/1.075=4.0 exactly
    .testutil.assertApprox[.uqf.crossRateSharedBase[4.30;1.075];4f;1e-9;"EURPLN, EURUSD -> USDPLN"]};

testCrossRateSharedBaseMatchesManualComposition:{[t]
    rateAX:1.3427; rateAY:0.7231;
    lhs:.uqf.crossRateSharedBase[rateAX;rateAY];
    rhs:.uqf.crossRate[rateAX;.uqf.invertRate rateAY];
    .testutil.assertApprox[lhs;rhs;1e-9;"crossRateSharedBase = crossRate composed with invertRate on the second leg"]};

testCrossRateSharedBaseIdentityWhenPairsEqual:{[t]
    / A/X and A/Y with X=Y (same rate on both legs) -> Y/X = 1
    .testutil.assertApprox[.uqf.crossRateSharedBase[1.2500;1.2500];1f;1e-9;"identical shared-base rates cross to exactly 1"]};

testCrossRateSharedBaseAntiSymmetric:{[t]
    / swapping which pair is "X" and which is "Y" inverts the result
    rateAX:1.10; rateAY:150.0;
    fwd:.uqf.crossRateSharedBase[rateAX;rateAY];
    back:.uqf.crossRateSharedBase[rateAY;rateAX];
    .testutil.assertApprox[fwd*back;1f;1e-9;"swapping the two legs gives the inverse cross rate"]};

testInvertBookSwapsSides:{[t]
    book:`bid`ask!(1.1000;1.1002);
    inverted:.uqf.invertBook book;
    expected:`bid`ask!(1%1.1002;1%1.1000);
    .testutil.assertApprox[inverted`bid;expected`bid;1e-9;"inverted bid = 1/original ask"];
    .testutil.assertApprox[inverted`ask;expected`ask;1e-9;"inverted ask = 1/original bid"]};

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

testCcyOrientCrossChain:{[t]
    r:.uqf.ccyOrientCross[`EURUSD;`USDJPY];
    .qunit.assertEquals[r`crossSym;`EURJPY;"A/B, B/C -> A/C"];
    .qunit.assertFalse[r`invert1;"leg1 not inverted"];
    .qunit.assertFalse[r`invert2;"leg2 not inverted"]};

testCcyOrientCrossSharedQuote:{[t]
    r:.uqf.ccyOrientCross[`EURUSD;`GBPUSD];
    .qunit.assertEquals[r`crossSym;`EURGBP;"A/B, C/B -> A/C"];
    .qunit.assertFalse[r`invert1;"leg1 not inverted"];
    .qunit.assertTrue[r`invert2;"leg2 inverted (shared quote)"]};

testCcyOrientCrossSharedBase:{[t]
    r:.uqf.ccyOrientCross[`USDJPY;`USDCHF];
    .qunit.assertEquals[r`crossSym;`JPYCHF;"B/A, B/C -> A/C"];
    .qunit.assertTrue[r`invert1;"leg1 inverted (shared base)"];
    .qunit.assertFalse[r`invert2;"leg2 not inverted"]};

testCcyOrientCrossRejectsNoSharedCurrency:{[t]
    wrapper:{[dummy] .uqf.ccyOrientCross[`EURUSD;`GBPCHF]};
    .qunit.assertError[wrapper;::;"no shared currency is rejected"]};

testInvertBookDepthKnown:{[t]
    r:.uqf.invertBookDepth[1.1000 1.1002;1000000 1000000];
    .testutil.assertApprox[first r;0.9090909 0.9089256;1e-6;"prices invert elementwise, staying best-first"];
    .testutil.assertApprox[last r;1100000 1100200;1e-6;"sizes rescale into the new base currency"]};

testInvertBookDepthRoundTrip:{[t]
    prices:1.2500 1.2503 1.2505;
    sizes:2000000 1500000 3000000;
    once:.uqf.invertBookDepth[prices;sizes];
    twice:.uqf.invertBookDepth[once 0;once 1];
    .testutil.assertApprox[twice 0;prices;1e-6;"inverting twice restores prices"];
    .testutil.assertApprox[twice 1;sizes;1e-3;"inverting twice restores sizes"]};

testCrossBookAtSizesMatchesCrossBookAtNegligibleSize:{[t]
    / a size far smaller than any level's depth should reduce to exactly
    / crossBook's top-of-book result - a self-consistency check that
    / needs no hand-computed magic numbers.
    eurusdBook:`bidPrices`bidSizes`askPrices`askSizes!(1.0998 1.0996;1000000 1000000;1.1000 1.1002;1000000 1000000);
    usdjpyBook:`bidPrices`bidSizes`askPrices`askSizes!(149.98 149.96;1000000 2000000;150.00 150.02;1000000 2000000);
    r:.uqf.crossBookAtSizes[`EURUSD;eurusdBook;`USDJPY;usdjpyBook;enlist 100;`bid`ask];
    tob:.uqf.crossBook[`EURUSD;`bid`ask!(1.0998;1.1000);`USDJPY;`bid`ask!(149.98;150.00)];
    .qunit.assertEquals[first r`sym;tob`sym;"cross symbol matches crossBook"];
    .testutil.assertApprox[first r`bid;tob`bid;1e-6;"negligible-size bid matches crossBook's top-of-book bid"];
    .testutil.assertApprox[first r`ask;tob`ask;1e-6;"negligible-size ask matches crossBook's top-of-book ask"]};

testCrossBookAtSizesSharedCornerMatchesCrossBookAtNegligibleSize:{[t]
    / same consistency check, but for the shared-quote (invert) branch
    audusdBook:`bidPrices`bidSizes`askPrices`askSizes!(0.6498 0.6496;2000000 2000000;0.6500 0.6502;2000000 2000000);
    eurusdBook:`bidPrices`bidSizes`askPrices`askSizes!(1.0998 1.0996;2000000 2000000;1.1000 1.1002;2000000 2000000);
    r:.uqf.crossBookAtSizes[`AUDUSD;audusdBook;`EURUSD;eurusdBook;enlist 100;`bid`ask];
    tob:.uqf.crossBook[`AUDUSD;`bid`ask!(0.6498;0.6500);`EURUSD;`bid`ask!(1.0998;1.1000)];
    .qunit.assertEquals[first r`sym;tob`sym;"cross symbol matches crossBook (AUDEUR)"];
    .testutil.assertApprox[first r`bid;tob`bid;1e-6;"negligible-size bid matches crossBook's top-of-book bid"];
    .testutil.assertApprox[first r`ask;tob`ask;1e-6;"negligible-size ask matches crossBook's top-of-book ask"]};

testCrossBookAtSizesWalksMultipleLevels:{[t]
    eurusdBook:`bidPrices`bidSizes`askPrices`askSizes!(1.0998 1.0996;1000000 1000000;1.1000 1.1002;1000000 1000000);
    usdjpyBook:`bidPrices`bidSizes`askPrices`askSizes!(149.98 149.96;1000000 2000000;150.00 150.02;1000000 2000000);
    r:.uqf.crossBookAtSizes[`EURUSD;eurusdBook;`USDJPY;usdjpyBook;enlist 1500000;`bid`ask`mid];
    .testutil.assertApprox[first r`bid;164.9293;1e-3;"blended bid after walking depth on both legs"];
    .testutil.assertApprox[first r`ask;165.0187;1e-3;"blended ask after walking depth on both legs"];
    .testutil.assertApprox[first r`mid;164.974;1e-3;"mid is the average of the swept bid and ask"];
    .qunit.assertTrue[first r`bidFullyFilled;"enough depth to fully fill 1.5mm"];
    .qunit.assertTrue[first r`askFullyFilled;"enough depth to fully fill 1.5mm"]};

testCrossBookAtSizesInsufficientDepth:{[t]
    / total depth per side is 2mm; asking for 3mm can't be fully filled
    eurusdBook:`bidPrices`bidSizes`askPrices`askSizes!(1.0998 1.0996;1000000 1000000;1.1000 1.1002;1000000 1000000);
    usdjpyBook:`bidPrices`bidSizes`askPrices`askSizes!(149.98 149.96;1000000 2000000;150.00 150.02;1000000 2000000);
    r:.uqf.crossBookAtSizes[`EURUSD;eurusdBook;`USDJPY;usdjpyBook;enlist 3000000;`bid`ask];
    .testutil.assertApprox[first r`bidFilledSize;2000000f;1e-6;"bid caps at leg1's total depth"];
    .testutil.assertApprox[first r`askFilledSize;2000000f;1e-6;"ask caps at leg1's total depth"];
    .qunit.assertFalse[first r`bidFullyFilled;"not fully filled"];
    .qunit.assertFalse[first r`askFullyFilled;"not fully filled"]};

testCrossBookAtSizesMidVariesWithAsymmetricDepth:{[t]
    / an asymmetric book (thin ask, deep bid) should make mid genuinely
    / size-dependent, not coincidentally constant
    thinAskBook:`bidPrices`bidSizes`askPrices`askSizes!(1.0998 1.0996 1.0994;3000000 3000000 3000000;1.1000 1.1010;200000 5000000);
    usdjpyBook:`bidPrices`bidSizes`askPrices`askSizes!(149.98 149.96;1000000 2000000;150.00 150.02;1000000 2000000);
    r:.uqf.crossBookAtSizes[`EURUSD;thinAskBook;`USDJPY;usdjpyBook;500000 3000000;enlist `mid];
    .qunit.assertTrue[(r[`mid] 0)<(r[`mid] 1);"mid increases with size once the thin ask level is exhausted"]};

testCrossBookAtSizesSidesFiltering:{[t]
    eurusdBook:`bidPrices`bidSizes`askPrices`askSizes!(1.0998 1.0996;1000000 1000000;1.1000 1.1002;1000000 1000000);
    usdjpyBook:`bidPrices`bidSizes`askPrices`askSizes!(149.98 149.96;1000000 2000000;150.00 150.02;1000000 2000000);
    r:.uqf.crossBookAtSizes[`EURUSD;eurusdBook;`USDJPY;usdjpyBook;enlist 1000000;enlist `mid];
    .qunit.assertEquals[cols r;`size`sym`mid;"requesting just mid returns only size, sym and mid columns"]};

testCrossBookAtSizesRejectsInvalidSide:{[t]
    / note: wrapper takes the whole (book1;book2) tuple as a single
    / argument rather than closing over local variables - nested q
    / lambdas do NOT see an enclosing function's locals, only globals.
    wrapper:{[books] .uqf.crossBookAtSizes[`EURUSD;books 0;`USDJPY;books 1;enlist 1000000;enlist `close]};
    eurusdBook:`bidPrices`bidSizes`askPrices`askSizes!(1.0998 1.0996;1000000 1000000;1.1000 1.1002;1000000 1000000);
    usdjpyBook:`bidPrices`bidSizes`askPrices`askSizes!(149.98 149.96;1000000 2000000;150.00 150.02;1000000 2000000);
    .qunit.assertError[wrapper;(eurusdBook;usdjpyBook);"an unrecognised side symbol is rejected"]};

testCrossBookAtSizesRejectsNoSharedCurrency:{[t]
    wrapper:{[books] .uqf.crossBookAtSizes[`EURUSD;books 0;`GBPCHF;books 1;enlist 1000000;`bid`ask]};
    eurusdBook:`bidPrices`bidSizes`askPrices`askSizes!(1.0998 1.0996;1000000 1000000;1.1000 1.1002;1000000 1000000);
    gbpchfBook:`bidPrices`bidSizes`askPrices`askSizes!(1.20 1.19;1000000 1000000;1.21 1.22;1000000 1000000);
    .qunit.assertError[wrapper;(eurusdBook;gbpchfBook);"EURUSD and GBPCHF share no currency"]};

\d .
