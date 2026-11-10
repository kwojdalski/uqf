# Execution quality (markouts)

How good was each fill? A markout compares a fill's price with the market mid a
short time after it. If you bought and the mid then rose, the fill was good.

This page covers real crypto fills and equity fills, scored over the canonical
`executions` and `market_data` like the [normalized FX markouts](fx-markout.md).

## `crypto_markout1`

`crypto_markout1` (`src/etl/streaming/crypto_markout.q`) subscribes to the
canonical `executions` and `market_data` and scores the crypto fills among them
(a BASE-QUOTE symbol such as `BTC-USDT`; the FX fills in the same table are not
routable by the crypto domain and are dropped). It never reads
`crypto_sim_fills`: simulated and real execution are kept apart. Like
`fxmarkout1`, it is a horizon job over the [markout
core](../architecture/markout-contract.md): it queues each fill and, once a
second, scores the ones whose 10s horizon has passed and whose books have caught
up. The result is `crypto_markout`, in the contract's long layout: a row per
fill, horizon (1s and 10s) and component (`total`, and `execution` for the move
from the fill to the reference at the fill).

- **The reference is the best mid across venues.** At `source_time + horizon`,
  each venue's latest top of book counts unless it is more than five seconds old
  (`.qf.mkt.reference_max_age`). The highest bid and lowest ask over the venues
  left give the mid. A venue that went quiet does not set the price. `posbook1`
  marks crypto positions with the same function.
- **Basis points, and the fee.**
  `markout_bps = side x 10000 x (ref - price) / price`, positive when the market
  moved in your favour. `fee_quote` is the fill's fee in the quote currency (a
  fee in the base currency is valued at the fill price; one in any third
  currency is null, not free), `fee_bps` that as a share of notional, and
  `markout_net_bps` the markout less it, on the `total` row.
- **Both clocks are the venue's.** cryptorust's fills recorder sends the venue's
  own time for each fill (`crypto_trades.source_time`, from the exchange's trade
  time). The `executions` normalizer keeps it, on the `venue` clock, where there
  is one, and falls back to the plant's receipt `time` on the `receipt` clock
  where there is not (a row recorded before the column, or by a recorder that
  predates it). The core scores a fill only against books on its own clock, so a
  receipt-clock fill is a `clock_mismatch` row naming why, with no bps, instead
  of a figure that mixes two clocks. The retired job read the plant's receipt
  `time` on both sides, which was consistent only because it never used the
  venue's stamp.
- **USDT is declared worth 1 USD.** Amounts are reported in USD (the
  configuration snapshot's `report_ccy`, #1999), through a fixed rate the spec
  declares and every row marks (`rate_source` is `fixed`). A site with a
  USDT-USD book declares it in the spec instead.

A fill with no live book at a horizon keeps its row, with a null `ref_t1` and
`markout_bps` and the status `unavailable`.

The bounded twin, `hdb_crypto_markout_backfill1`, scores the HDB's `executions`
and `market_data` with the same transform and writes the same table under the
key `(venue, fill_id, horizon, kind, step)`, replacing what the live job wrote
for the window: the fills it never saw. The live row and the refilled row are
the same row.

It does not start with the stack. `uqs start --profile crypto` starts it with
`cryptomock1`, `executions1` and `marketdata1`; next to cryptorust's real
recorders, start it on its own.

This replaces the job that read `crypto_trades` and `crypto_book` and published
`crypto_execution_quality` (#729). The new dataset's `markout_bps` on its
`total` rows is that table's `markout_bps`, to the last digit, wherever the
fills and the books are on one clock (`tests/q/test_crypto_markout.q`).

## `eq_markout1`

`eq_markout1` (`src/etl/streaming/eq_markout.q`) is the same job over the equity
domain (`src/etl/transforms/eq_domain.q`, #1981). It scores the fills of the
equity venues among the canonical `executions` (`XNAS`, named once in
`.qpipe.transform.eq_markout_outputs.venues`) against the equity venues' books
in `market_data`, and publishes `eq_markout` in the contract's long layout: a
row per fill, horizon (5s, 30s and 1min) and component (`total`, and
`execution`). The FX and crypto fills of the same table are not kept: the equity
domain prices any symbol, so the venue is what tells a fill's dataset.

- **The reference is the mid of the venue's book.** At `source_time + horizon`
  the latest top of book counts unless it is more than a minute old (`max_age`).
  A minute, not the five seconds FX and crypto use: an equity touch can sit
  unchanged for far longer than a currency pair's, and a limit tighter than the
  quiet periods would turn them into holes.
- **Basis points.** `markout_bps = 10000 x move / price`, positive when the
  market moved in your favour. The fill's commission is not netted: no rule for
  turning a per-share or per-order fee into a share of notional is declared yet.
- **The session close is a boundary, not a gap.** The spec's sessions are the US
  regular session (09:30 to 16:00 New York, as UTC instants, daylight time
  included; 13:00 on the day after Thanksgiving, Christmas Eve and 3 July) for
  every NYSE trading day of 2026 to 2028, and by the same rule outside it. A
  `[open, close)` interval: a pair whose horizon crosses the close, or lands
  exactly on it, is `unavailable` with a null move, whatever book the next
  session has already published; so is a fill outside every session. Worked by
  hand in `src/etl/transforms/eq_markout_outputs.q` and
  `tests/q/test_hdb_eq_markout_backfill.q`: a META sell ten seconds before the
  close is priced at 5s and unavailable at 30s and 1min. The calendar skips NYSE
  holidays, has no extended hours and is data in the spec, so a site with
  another declares its own.
- **Both clocks are the venue's.** The Databento bundle's books are on the
  `venue` clock and so are the fills `eq_fills_backfill1` declares; a fill on
  the `receipt` clock is a `clock_mismatch` row, as for crypto.
- **Prices in USD.** The instruments are USD quoted (`eq_domain`'s default,
  overridable per instrument), so no rate is read.

`eq_markout1` does not start with the stack, and is in no profile. Equity books
reach `market_data` from the Databento bundle's `databento1`, but no live feed
in this tree publishes equity fills onto `executions`: `eq_fills_backfill1`
lands a drop's fills in the HDB. `hdb_eq_markout_backfill1` is the bounded twin
and, until a live fills feed exists, the job that does the work. It scores the
HDB's equity-venue `executions` against its `market_data` with the same
transform and writes the same table under the key
`(venue, fill_id, horizon, kind, step)`, so a live row and a refilled one are
the same row (`tests/q/test_eq_markout.q`). The window is ready only once the
equity books have landed past its end plus the furthest horizon
(`complete_through`).

```
uqs job backfill hdb_eq_markout_backfill --source-version v1 --from 2026-02-25 --to 2026-02-26
```
