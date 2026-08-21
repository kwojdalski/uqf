/ example_defaults.q - shared scaling constants used by scripts/*.q to
/ build synthetic order-book/tick data for worked examples. Unlike
/ forwards.q's ts_col/col_precedence (genuine library output-shape
/ config, read by src/*.q functions), nothing in src/*.q consumes these -
/ they exist so every example script that builds a synthetic book (level
/ prices, level sizes) starts from the same baseline instead of each
/ redefining its own copy of the same numbers. A script may still
/ override locally (e.g. scaling pip_size per symbol) without touching
/ these defaults.
/ .

\d .qex

/ One pip, in rate terms - the increment example scripts use between
/ adjacent synthetic order book levels (e.g. spot, spot-pip_size,
/ spot-2*pip_size, ...). Not the same thing as pip_factor (10000 for most
/ pairs, 100 for JPY crosses), which is the real library's caller-
/ supplied scaling parameter for markout/eff_spread/slippage/etc - this
/ is 1%10000 in the same units pip_factor scales, purely for building
/ demo data.
pip_size:0.0001;

/ One synthetic order-book level's baseline notional, in example scripts'
/ synthetic books - a round, readable size (1 million) that scripts scale
/ up per level to build a depth ladder.
size_unit:1e6;

/ Jitter amount (same units as size_unit) example scripts add per row so
/ consecutive synthetic ticks' sizes aren't identical.
size_row_drift:1e4;

\d .
