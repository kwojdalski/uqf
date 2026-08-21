# env/

Empty, typed table schemas for a broader eFX trading system's data model,
plus a script that seeds them with a small, coherent example scenario.

**This is scaffolding/reference material, not part of the `uqf` pricing
library.** `uqf` itself stays strictly scoped to eFX pricing, risk, and
execution *functions* (see the `kdb-q-conventions` skill's scope note) -
it has no positions, orders, connections, or any other stateful,
system-level concept of its own. `env/` exists alongside that library for
whoever wants a starting data model for the rest of a trading system uqf's
functions would sit inside, without uqf itself taking a position on how
that system is built.

Deliberately kept separate from `src/*.q` and its per-module `.q<abbrev>`
namespace convention (`.qstats`, `.qfwd`, `.qexec`, ...) - everything here
lives in its own single `.envschema` namespace instead, and neither file is
loaded by `src/init.q`.

## Files

- **`schemas.q`** - the 10 empty table definitions, one `\d .envschema` block.
  Not loaded by `src/init.q`; `\l env/schemas.q` on its own works under
  either interpreter (PeachQ or real KDB-X) with no other dependency.
- **`seed.q`** - loads `src/init.q` + `schemas.q` + `lib/log4q.q`, then
  populates every table with a few example rows that reference each other
  (same `order_id`/`trade_id`/`sym`/venue) so the result reads as one small
  realistic scenario, not ten unrelated tables. Requires real kdb+/KDB-X,
  not the local PeachQ binary, for the same log4q reason every
  `scripts/*.q` example does (see README's Licensing section). Run from
  the repository root: `q env/seed.q`.

## Tables

| Table | Shape notes |
|---|---|
| `market_data` | Identical to `forwards.q`'s `quotes` shape (`require_quotes_cols`/`cross_book_at`) - level-0-first vector columns, one row per (ts, sym) snapshot. |
| `positions` | One row per (account, sym) snapshot; `side`/`notional`/`avg_entry_rate` follow `risk.q`'s own `pnl`/`carry_pnl` naming and `1`/`-1` side convention. |
| `predictions` | One row per (ts, sym, horizon_ms, model); `horizon_ms` matches `cross_markout_at_horizons`' horizon convention. |
| `orders` | One row per order, updated in place as `status` changes (`new`/`filled`/`cancelled`/`rejected`). |
| `trades` | `sym`/`time`/`side`/`trade_price`/`pip_factor` are exactly `execution.q`'s `markout_at_horizons` input shape - select those five columns straight off this table and pass it in directly, no reshaping. |
| `markouts` | Exactly `markout_at_horizons`'s output shape (`ts`/`sym` leading, per its `col_precedence` convention). |
| `reference_data` | One row per pair; `base_ccy`/`quote_ccy` match `ccy.q`'s `ccy_pair_legs` field names. |
| `order_routing` | One row per (order, venue) an order was routed to - not 1:1 with `orders`, since an order can split across venues. |
| `connections` | Venue/process connection registry - up/down status, a much simpler stand-in for what `lib/torq/code/handlers/trackservers.q` does at production scale. |
| `economic_calendar` | One row per scheduled macro release; `actual` is null until the event fires. |

## Extending

Add a new table the same way: one more line in `schemas.q`'s `.envschema`
block (empty, explicitly typed, a short comment above it explaining the
shape and any column it deliberately mirrors elsewhere in the repo), then
a matching seed block in `seed.q` if you want it populated by default.
