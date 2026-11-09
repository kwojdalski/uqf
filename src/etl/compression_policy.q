/ compression_policy.q - how this stack's HDB tables are compressed (#2152).
/ .
/ The declarations, in one place; the module that validates them and writes
/ TorQ's compressionconfig.csv is core/compression.q, and
/ docs/reference/compression-policy.md says what each choice is for. After
/ editing this file:
/     uv run python scripts/generate/derive.py compression-config
/ .
/ WITHIN WHAT TORQ'S .cmp TAKES: algorithms 0-3 for the age pass, because .cmp is the engine
/ (#2146) and this is the file it reads. lz4hc and zstd are refused at
/ declaration, naming that, except on a table whose mode is `write: the writer
/ compresses it and the age pass never applies its row (#2295).
/ .
/ MODE (#2151) says WHEN a table is compressed: `age (the default, and every
/ declaration below), `write or `both. Nothing here changes it, so every table
/ keeps being compressed by TorQ's age pass and written uncompressed: it is a
/ choice for a table that is scanned in bulk and whose disk is the constraint.
/ docs/reference/compression-policy.md has the trade-off and what each does.
/ .
/ A table that is not named here takes the stack default. A declaration is
/ PARTIAL: it states only what differs from that default.

\d .

/ Everything not named below: gzip at level 6 in 64 KB blocks (block size 16),
/ once a partition is a week old. A week keeps the days still being backfilled,
/ re-read and corrected in the cheap, uncompressed form.
.qetl.compression.set_default[`algo`blocksize`level`minage!(2;16;6;7)];

/ Deep and wide books, ticks and quotes: the bulk of the bytes, written once and
/ scanned in whole-day sweeps. Nested float ladders compress well. Compressed
/ after three days, in 128 KB blocks, which costs random access nobody does.
{[t] .qetl.compression.declare[t;`algo`blocksize`level`minage!(2;17;6;3)]} each
    `market_data`fx_orderbook`mkt_orderbook`wide_orderbook`crypto_book`crypto_book_1s`superbook`book_liquidity_1m`last_value`crypto_trades;

/ Markouts: wide, derived and re-derived by backfills, so left alone for two
/ weeks. The boolean columns are the best candidates there are - one byte of
/ two values - and take gzip's top level at no cost worth counting.
{[t] .qetl.compression.declare[t;`algo`blocksize`level`minage!(2;16;6;14)]} each `crypto_markout`eq_markout;
.qetl.compression.declare[`fx_markout;`minage`columns!(14;(enlist `invert)!enlist 2 16 9)];
.qetl.compression.declare[`fx_markout_wide;`minage`columns!(14;`leg1_invert`leg2_invert`leg3_invert!(2 16 9;2 16 9;2 16 9))];

/ Small reference and state tables: a few rows a day. Compressing them saves
/ nothing and makes every read of them decompress, so they stay as written.
{[t] .qetl.compression.declare[t;(enlist `algo)!enlist 0]} each
    `reference_data`economic_calendar`connections`config_change`ccy_exposure`order_routing;
