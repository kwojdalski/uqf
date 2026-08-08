// test_execution.q - tests for src/execution.q. Load src/execution.q,
// tests/lib/qunit.q and tests/lib/testutil.q before this file.

\d .executiontest

testMarkoutKnownSingleBuy:{[t] .testutil.assertApprox[.uqf.markout[1;1.1000;1.1010;10000];10f;1e-6;"buy, price rallies 10 pips after -> +10 pip markout"]};
testMarkoutKnownSingleSell:{[t] .testutil.assertApprox[.uqf.markout[-1;1.1000;1.0990;10000];10f;1e-6;"sell, price falls 10 pips after -> +10 pip markout (favourable)"]};
testMarkoutAdverseMove:{[t] .testutil.assertApprox[.uqf.markout[1;1.1000;1.0990;10000];-10f;1e-6;"buy, price falls after -> negative (adverse) markout"]};
testMarkoutZeroWhenFlat:{[t] .testutil.assertApprox[.uqf.markout[1;1.1000;1.1000;10000];0f;1e-9;"no move -> zero markout"]};

testMarkoutVectorizedAcrossHorizons:{[t]
    horizons:1.1010 1.1005 1.0995 1.1000;
    expected:10 5 -5 0;
    .testutil.assertApprox[.uqf.markout[1;1.1000;horizons;10000];expected;1e-6;"markout profile across several post-trade horizons"]};

testMarkoutSideFlipIsNegation:{[t]
    buySide:.uqf.markout[1;1.1000;1.1010;10000];
    sellSide:.uqf.markout[-1;1.1000;1.1010;10000];
    .testutil.assertApprox[sellSide;neg buySide;1e-9;"flipping side negates markout for the same prices"]};

testEffSpreadKnownBuyAboveMid:{[t] .testutil.assertApprox[.uqf.effSpread[1;1.1002;1.1000;10000];4f;1e-6;"buy 0.2 pips above mid -> 2*0.2=0.4... scaled: 4 pip effective spread"]};
testEffSpreadKnownSellBelowMid:{[t] .testutil.assertApprox[.uqf.effSpread[-1;1.0998;1.1000;10000];4f;1e-6;"sell 0.2 pips below mid -> same 4 pip effective spread (symmetric)"]};
testEffSpreadZeroAtMid:{[t] .testutil.assertApprox[.uqf.effSpread[1;1.1000;1.1000;10000];0f;1e-9;"trade exactly at mid -> zero effective spread"]};
testEffSpreadNegativeIsPriceImprovement:{[t] .testutil.assertApprox[.uqf.effSpread[1;1.0999;1.1000;10000];-2f;1e-6;"buy below mid -> negative effective spread (price improvement)"]};

testSlippageKnownCost:{[t] .testutil.assertApprox[.uqf.slippage[1;1.1000;1.1003;10000];3f;1e-6;"buy fills 3 pips worse than arrival -> +3 pip slippage cost"]};
testSlippageZeroWhenNoMove:{[t] .testutil.assertApprox[.uqf.slippage[1;1.1000;1.1000;10000];0f;1e-9;"execution at arrival price -> zero slippage"]};
testSlippageNegativeIsImprovement:{[t] .testutil.assertApprox[.uqf.slippage[1;1.1000;1.0998;10000];-2f;1e-6;"buy fills better than arrival -> negative slippage"]};
testSlippageSellSideSign:{[t] .testutil.assertApprox[.uqf.slippage[-1;1.1000;1.0997;10000];3f;1e-6;"sell fills 3 pips worse (lower) than arrival -> +3 pip cost"]};

testFillRatioKnown:{[t] .testutil.assertApprox[.uqf.fillRatio[73;100];0.73;1e-9;"73 fills out of 100 quotes"]};
testFillRatioZero:{[t] .testutil.assertApprox[.uqf.fillRatio[0;100];0f;1e-9;"no fills"]};
testFillRatioFull:{[t] .testutil.assertApprox[.uqf.fillRatio[100;100];1f;1e-9;"every quote filled"]};

testRejectRatioKnown:{[t] .testutil.assertApprox[.uqf.rejectRatio[4;100];0.04;1e-9;"4 rejects out of 100 requests"]};

testVwapKnownExample:{[t] .testutil.assertApprox[.uqf.vwap[1.1000 1.1010 1.1005;1000000 2000000 1000000];1.100625;1e-9;"size-weighted average across three fills"]};
testVwapSingleFillEqualsThatPrice:{[t] .testutil.assertApprox[.uqf.vwap[enlist 1.1234;enlist 1000000];1.1234;1e-9;"a single fill's vwap is just its own price"]};
testVwapEqualSizesEqualsSimpleAverage:{[t]
    prices:1.1000 1.1010 1.1020 1.1030;
    sizes:1000000 1000000 1000000 1000000;
    .testutil.assertApprox[.uqf.vwap[prices;sizes];avg prices;1e-9;"equal sizes -> vwap reduces to the plain average"]};

\d .
