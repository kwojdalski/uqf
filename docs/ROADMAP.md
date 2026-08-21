# Roadmap: order-book / microstructure feature candidates

Candidate functions for a possible new `src/microstructure.q` module, covering
liquidity/order-flow signals that quantify *what the book looks like* and
*how it's moving* rather than pricing/execution outcomes (which is what
`book.q`, `forwards.q` and `execution.q` already cover).

**Status: implemented.** Every Tier 1 and Tier 2 function below now lives in
`src/microstructure.q` (tests in `tests/test_microstructure.q`) - see
`docs/prompts/microstructure-features.md` for the implementation prompt this
was built from, including the one deliberate shape change from what's written
below: every function takes a whole `quotes`-table COLUMN (a vector of
per-row level vectors), not one row's flat vectors, and returns a
row-aligned vector, the same generalization `markout_at_horizons`/
`cross_book_at_sizes` already make elsewhere in this library.

**Source:** feature/formula definitions mined from an external equity LOB
deep-RL feature catalog (built on Databento MBP-10 data). uqf's data model
is different - `quotes` tables of
`` `ts`sym`bid_prices`bid_sizes`ask_prices`ask_sizes `` (level-0-first
vectors per row, see `forwards.q`'s `require_quotes_cols`), i.e. periodic
book *snapshots*, not an L3 add/cancel/trade event tape. Every candidate
below is filtered and re-expressed against that shape; several source
features that depend on a genuine event tape (`action`/`side` per order)
don't carry over and are listed separately at the bottom as explicitly out
of scope for now.

Sign/parameter conventions follow the rest of the library: `pip_factor`
(10000 / 100 for JPY), `side` (1 buy / -1 sell), snake_case, one qDoc block
per function, tested on both PeachQ and KDB-X.

## Tier 1 - single-snapshot (one row of a `quotes` table)

Take `bid_prices`/`bid_sizes`/`ask_prices`/`ask_sizes` (or a `book` dict of
the same, matching `sweep_price`'s level-0-first convention) for one row and
return a scalar. No history needed - straightforward, highest value per unit
effort.

| Candidate function | Source feature | Formula | Notes |
|---|---|---|---|
| `book_pressure_at_level[bid_sizes;ask_sizes;level]` | `book_pressure` (#1) | $(V^{bid}_i-V^{ask}_i)/(V^{bid}_i+V^{ask}_i)$ | Range $[-1,1]$, 0 when both sides empty at that level. The concrete example the user asked about. |
| `order_book_imbalance[bid_sizes;ask_sizes;n_levels]` | `order_book_imbalance` (#5) | same ratio, summed over levels 0..n_levels-1 | Multi-level generalisation of the above; more robust per the cited literature. |
| `microprice[bid_prices;bid_sizes;ask_prices;ask_sizes]` | `microprice` (#2) | $(V^{ask}_0 P^{bid}_0+V^{bid}_0 P^{ask}_0)/(V^{bid}_0+V^{ask}_0)$, fallback to mid when both sizes are 0 | Level-0 only. Natural companion to `cross_ref_price_at`'s `mid`. |
| `microprice_divergence[...]` | `microprice_divergence` (#3) | `microprice - mid` | Trivial once `microprice` exists. |
| `spread_bps[bid_prices;ask_prices]` | `spread_bps` (#4) | $(P^{ask}_0-P^{bid}_0)/\text{mid} \times 10000$ | Not currently in `execution.q` - `eff_spread`/`slippage` are trade-relative, this is quote-relative. |
| `depth_ratio[bid_sizes;ask_sizes]` | `depth_ratio` (#6) | $(V^{bid}_0+V^{ask}_0)/\sum_{i=1}^{4}(V^{bid}_i+V^{ask}_i)$ | High = fragile top-of-book; needs >=5 levels present. |
| `vwmp_skew[bid_prices;bid_sizes;ask_prices;ask_sizes;n_levels]` | `vwmp_skew` (#7) | volume-weighted mid over `n_levels`, minus simple mid, divided by L0 spread | |
| `book_slope[prices;sizes]` (one function, called once per side) | `bid_ask_slope` (#8-9) | $(P_0-P_{n-1})/\sum V_i$ | Already shaped like `sweep_price`'s `(prices;sizes)` args - could literally share validation logic with it. |
| `book_convexity[prices;side]` | `book_convexity` (#17) | $(P_0-P_1)-(P_1-P_2)$, mirrored for asks | Needs >=3 levels. |
| `vamp[bid_prices;bid_sizes;ask_prices;ask_sizes;notional]` | `price_vamp` (#22, "VAMP") | walk each side to `notional`, avg the two volume-weighted prices | **Can be built directly on the existing `sweep_price`** - VAMP is `avg` of `sweep_price[ask_prices;ask_sizes;bid_notional]` and `sweep_price[bid_prices;bid_sizes;ask_notional]`'s `avg_price`, converting `notional` to a size via price rather than calling it a fresh algorithm. Cheapest of the new-alpha-tier features to add given what's already in `execution.q`. |

## Tier 2 - rolling / consecutive-snapshot (needs a time-ordered `quotes` slice for one `sym`)

Same as Tier 1 but operates on two-or-more consecutive rows (typically via
`deltas`/`prev`-style lag over a `quotes` subselect), returning a vector
aligned to the input rows. First row is conventionally 0/null (no prior
snapshot to diff against) - same convention `markout_at_horizons` already
uses for "no quote yet" cases.

| Candidate function | Source feature | Formula sketch | Notes |
|---|---|---|---|
| `mid_price_velocity[quotes;sym]` | `mid_price_velocity` (#14) | first difference of mid | |
| `mid_price_acceleration[quotes;sym]` | `mid_price_acceleration` (#15) | second difference of mid | |
| `queue_depletion_rate[quotes;sym;side]` | `queue_depletion` (#13) | $\max(V_{t-1}-V_t,0)/V_{t-1}$ at L0 | |
| `ofi[quotes;sym]` | `ofi` (#11) | signed L0 queue-building flow (Cont-Kukanov-Stoikov) | Needs only consecutive L0 price/size, no trade tape - genuinely computable from `quotes`. |
| `ofi_multilevel[quotes;sym;n_levels]` | `ofi_multilevel` (#24) | OFI extended across `n_levels`, handling level appear/disappear | Natural follow-on to `ofi`. |
| `rolling_ofi[ofi_series;window]` | `ofi_rolling` (#12) | `window`-sum of `ofi` | Thin wrapper; same "give it multiple window instances" idea as `hit_ratio_by`'s `bucket_size`. |
| `spread_ratio[quotes;sym;window]` | `spread_ratio` (#16) | `spread_bps / rolling_mean(spread_bps, window)` | Depends on `spread_bps`. |
| `inter_event_time[quotes;sym]` | `inter_event_time` (#15/log-gap) | `log(1 + ts - prev_ts)` | Purely from the `ts` column, no book fields at all. |
| `ofi_autocorrelation[ofi_series;window]` | `ofi_autocorrelation` (#28) | rolling lag-1 corr of `ofi` | Diagnostic on top of `ofi`, likely lowest priority of this tier. |

## Priority suggestion

1. `book_pressure_at_level` / `order_book_imbalance` - the concrete case
   raised, cheap, and the header/summary stat most eFX desks would recognise
   first.
2. `microprice` / `microprice_divergence` / `spread_bps` - small, foundational,
   reused by several others above (`vwmp_skew`, `spread_ratio`).
3. `vamp` - near-free given `sweep_price` already exists.
4. `ofi` / `ofi_multilevel` - highest literature-cited predictive power, but
   needs the rolling/lag machinery Tier 2 requires; do after the Tier 1
   primitives it doesn't actually depend on are in place and tested.
5. Everything else in Tier 2, roughly in the table's order.

## Explicitly out of scope for now

These source features require a genuine order/trade *event* tape
(`action` in `{A,C,T}`, aggressor `side`, per-event `size`) rather than
periodic book snapshots - uqf has no such ingestion path today, and adding
one is a bigger scope decision than adding a function:

- `signed_trade_flow` / cumulative delta (#19)
- `vpin` (#25)
- `trade_arrival_rate` (#26)
- `large_trade_ratio` (#27)
- `cancel_to_trade_ratio` (#23)
- `odd_lot_trade_ratio` / `odd_lot_imbalance` (#20-21)
- `order_count_imbalance` (#18) - needs resting-order *counts* per level
  (`bid_ct_NN`/`ask_ct_NN`), which `quotes` doesn't carry alongside
  `bid_sizes`/`ask_sizes`

If/when uqf grows a trades/order-event ingestion path (e.g. alongside the
`trades` table shape `markout_at_horizons` already consumes -
`` `sym`time`side`trade_price`pip_factor ``), these become straightforward
rolling-window counts/ratios over that table, mirroring `hit_ratio_by`'s
windowed-groupby shape.
