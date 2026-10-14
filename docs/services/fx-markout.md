# Normalized FX markouts

Markouts scored from the stack's two canonical contracts, `executions` and
`market_data`, rather than from any one source's tables. A deal tape or an order
book is brought to those contracts by an **adapter**; the calculation, the
horizon lifecycle and the outputs never name the source.

```
source tables ──adapter──► executions ┐
                                       ├─► calculation ──► long output
source tables ──adapter──► market_data ┘     (one result)   wide output
```

This page is the contract and the decisions. The calculation is
`src/etl/transforms/fx_markout.q`, namespace `.qpipe.transform.fx_markout`.

## The two inputs

Nothing new is invented: the existing contracts are sufficient.

  | Input         | Columns the calculation reads                                                                             | Clock         |
  | ---           | ---                                                                                                       | ---           |
  | `executions`  | `source_time`, `venue`, `sym`, `side` (+1 buy, -1 sell), `size`, `price`, `fill_id`                       | `source_time` |
  | `market_data` | `source_time`, `sym`, `source`, `bid_prices`, `bid_sizes`, `ask_prices`, `ask_sizes` (a snapshot per row) | `source_time` |

The plant's receipt `time` is **not** an input, so it cannot leak into the
clock. An execution is identified by `(venue; fill_id)`.

## The result

One long table, one row per `(execution, horizon, component)`:

  | `kind`      | `pips`                                                                                                               |
  | ---         | ---                                                                                                                  |
  | `total`     | `side × pip_factor × (ref_h − price)`: the markout                                                                   |
  | `execution` | `side × pip_factor × (ref_0 − price)`: the **execution residual**, the fill against the reference at its own instant |
  | `leg`       | `side × pip_factor × (a leg's contribution to ref_h − ref_0)`: the cross-leg allocation, one row per route step      |
  | `residual`  | `total − execution − Σ legs`: what the allocation did not account for                                                |

`step` numbers a leg within its route from 1 and is 0 for every other kind, so
`(venue; fill_id; horizon; kind; step)` is a key with no nulls.

So `total = execution + Σ leg + residual` for every `(execution, horizon)`.
Reference and allocation are priced along one route, so the residual is float
noise and `status` is `reconciled`. A long or wide output is a reshaping of this
table alone.

## Decisions

**Time.** Both inputs are read on `source_time`. A test changes the plant `time`
of every row and checks the result is identical.

**Cross routes.** The route of an instrument is a pure function of the
instrument under a declared convention (`.qcross.attribution_route_with`;
default `.qcross.attribution_convention`, which is *proposed and unapproved*, so
it is a parameter and every row records its `version`). The horizon job's `legs`
callback is `.qpipe.transform.fx_markout.legs_of`, the same function, so
readiness waits for exactly the legs scoring reads. One consequence to be aware
of: a pair that has its own direct book is still priced along its convention
route when the convention routes it so. `.qcross.cross_ref_price_at` would
instead take the shortest chain of the books present; the two agree when the
route is the chain available, and a test holds the calculation's allocation to
`.qexec.cross_markout_attribution` there.

**Reference price.** Per leg and per source, the latest snapshot at or before
the instant; level 0 only (`.qbook.top_sides`: sizes decide whether a level is
usable, never what the price is, so there is no depth sweep). A source that has
withdrawn a side contributes nothing on it, and does not fall back to an older
book. Across sources, the best bid and best ask among those no older than
`max_age` (`.qmicro.best_across_venues`). `max_age` defaults to none, "as of
however old", the same as `demo_markout`; a site that wants a bound declares it.
Each leg is oriented by the route and chained; the reference is the mid of the
chain. A best bid above the best ask across sources is not corrected: each
source's own crossed book is refused by `market_data`'s publish check, and a
cross-source cross is a market state, not bad data.

**Missing reference.** A leg with no book at the execution or the horizon gives
a null reference and null allocations with status `unavailable`. The rows are
kept so the gap shows. The execution residual survives a missing horizon, and
the markout survives a missing book at the execution's own instant.

**Horizons.** Declared timespans, signed, zero allowed. A negative horizon reads
the reference *before* the execution, so the job's `lookback` is
`0D | -min(horizons)` and its wait is `max(max(horizons), settle)`. Zero gives
`ref_h = ref_0`: the markout is the execution residual and the legs are zero.

**Identity.** `(venue; fill_id)`. Within one call the last delivery wins.
Executions that cannot be scored (no route for the instrument, a null identity
or time, a side other than +1/-1, a price that is not positive and finite) are
excluded, and `.qpipe.transform.fx_markout.unscoreable` names them with the
reason; the live job's `keep` drops the same rows so a queue never holds one.

**Not migrated.** `demo_markout` and `crypto_markout` keep their own, different
policies (a single-quote pip mid; a best mid in basis points). They are not
rebuilt on this calculation, so their outputs are unchanged.

## Source adapters

An adapter is a declared `.qetl.transform` from one source's table to
`executions` or `market_data`, written as `executions.q` and `market_data.q`
write theirs: the normalizer shell holds it to the canonical table, column for
column and type for type. It selects what is eligible in its source, names and
signs what that source calls things, keeps the source's own times and
identifiers, and refuses what it cannot represent. It owns no queue, no
reference-price choice and no markout arithmetic.

`src/etl/transforms/fx_source_adapters.q` holds two worked analogues, shaped to
differ from the tables the stack already normalizes (neither is a port of any
real source):

  | Adapter                               | Source shape                                                                                             | Decides                                                                                                                                                                                         |
  | ---                                   | ---                                                                                                      | ---                                                                                                                                                                                             |
  | `executions_from_deal_tape`           | a row per deal **event**: `id`, `security_code`, `side` B/S, `price`, `last_qty`, `cum_qty`, `exec_type` | only `fill` rows are executions; `status` rows carry the order's **cumulative** quantity and are dropped, so an order's fills sum to its final `cum_qty`; the id is kept exact from its integer |
  | `market_data_from_book_levels`        | a row per **level**, with a liquidity `category` and a `settle` code                                     | only `FULL_AMOUNT` spot levels are reference liquidity; levels are reshaped to a snapshot per (pair, feed, time), best first; an emptied or unlisted side is an empty ladder                    |

Eligibility is not malformation. A row the source carries but the contract is
not about (a status update, a forward, an equity) is dropped. A row that claims
to be about it and cannot be (a fill with an unknown side, a level with a
negative price) is refused, by name and count: a vendor changing a code is a
break to see, not rows to lose. Trade busts are not modelled; a site that needs
them filters at its source.

Adding a source is an adapter plus its line in the normalizer's `input`
dictionary: the markout calculation, the layouts and the horizon jobs do not
change. The tests hold two differently shaped sources to the claim: the deal
tape and a level-per-row book on one side, the stack's own `trades` and `quote`
on the other, normalize to the same `executions` and `market_data` rows and give
identical markouts from the same calculation.
