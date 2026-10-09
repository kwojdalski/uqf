.qcat.describe[`mock_trades]:
    "One synthetic FX trade from the mockups bundle - generated, never real.";
.qcat.describe[`mock_positions]:
    "One pair's net position and VWAP over a published mock_trades window.";
.qcat.describe[`mock_ticks]:
    "One synthetic top-of-book FX quote from the mockups bundle's feed.";
.qcat.describe[`mock_mids]:
    "The mid and spread of one mock_ticks quote.";
/ Generated data with nothing to protect, at a few thousand rows a day: read
/ whole rather than held to a querypolicy.csv row a bundle cannot add.
.qcat.unbounded[`mock_trades]:"synthetic rows, a few thousand a day";
.qcat.unbounded[`mock_positions]:"one row per pair and window";
.qcat.unbounded[`mock_ticks]:"synthetic rows, one per pair a second";
.qcat.unbounded[`mock_mids]:"one row per mock_ticks quote";
