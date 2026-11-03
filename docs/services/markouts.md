# Execution quality (markouts)

How good was each fill? A markout compares a fill's price with the market mid a
short time after it. If you bought and the mid then rose, the fill was good.

This page covers real crypto fills. Markouts over the canonical `executions` and
`market_data` are [normalized FX markouts](fx-markout.md).

## `crypto_markout1`

`crypto_markout1` (`src/etl/streaming/crypto_markout.q`) subscribes to
`crypto_trades` and `crypto_book`. It scores only real fills: it never reads
`crypto_sim_fills`, because simulated and real execution are kept apart. It
buffers fills and books and, once a second, scores the fills whose 10s horizon
has passed.

What to know about it:

- **The reference is the best mid across venues.** At `trade_time + horizon`,
  each venue's latest top of book counts unless it is more than five seconds old
  (`.qf.mkt.reference_max_age`). The highest bid and lowest ask over the venues
  left give the mid (`.qf.mkt.best_mid_across_venues`). A venue that went quiet
  does not set the price. `posbook1` marks crypto positions with the same
  function.
- **Basis points, not pips.**
  `markout_bps = side × 10000 × (ref_price − trade_price) / trade_price`.
  Positive means the market moved in your favour.
- **Both clocks are the plant's.** A fill's `time` and a book's `time` are both
  this tickerplant's receipt stamps. Real fills are polled from cryptorust, so a
  fill's time can trail the trade by the poll interval.

A fill with no live book at a horizon keeps its row, with a null `ref_price` and
`markout_bps`. `crypto_execution_quality` carries `sym`, `venue`, `fill_id`,
`trade_time`, `horizon`, `side`, `trade_price`, `ref_price` and `markout_bps`.

It does not start with the stack. `uqs start --profile crypto` starts it with
`cryptomock1`; next to cryptorust's real recorders, start it on its own. There
is no HDB backfill for it yet.
