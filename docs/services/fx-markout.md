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
instrument under a declared convention (`.qf.mkt.attribution_route_with`;
default `.qf.mkt.attribution_convention`, which is *proposed and unapproved*, so
it is a parameter and every row records its `version`). The horizon job's `legs`
callback is `.qpipe.transform.fx_markout.legs_of`, the same function, so
readiness waits for exactly the legs scoring reads. One consequence to be aware
of: a pair that has its own direct book is still priced along its convention
route when the convention routes it so. `.qf.mkt.cross_ref_price_at` would
instead take the shortest chain of the books present; the two agree when the
route is the chain available, and a test holds the calculation's allocation to
`.qf.exec.cross_markout_attribution` there.

**Reference price.** Per leg and per source, the latest snapshot at or before
the instant; level 0 only (`.qf.mkt.top_sides`: sizes decide whether a level is
usable, never what the price is, so there is no depth sweep). A source that has
withdrawn a side contributes nothing on it, and does not fall back to an older
book. Across sources, the best bid and best ask among those no older than
`max_age` (`.qf.mkt.best_across_venues`). `max_age` defaults to none, "as of
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

## The jobs

`fxmarkout1` (long layout, into `fx_markout`) and `fxmarkoutwide1` (wide layout,
into `fx_markout_wide`) are horizon jobs (`.qetl.job.stream.at_horizons`) over
`executions` and `market_data`. They differ in one name, the transform each
declares, and which table it publishes; the queue, timer, readiness, retention,
deduplication and expiry are the horizon kind's. Neither is in a start profile
(the connection budget is full, and each needs an `executions` and a
`market_data` source a site wires in).

  | Declared                       | Value                                    | Why                                                                                                               |
  | ---                            | ---                                      | ---                                                                                                               |
  | `event_time`, `reference_time` | `source_time`                            | the source's clock, never the plant's receipt time                                                                |
  | `ready_on`                     | `both`                                   | the horizon has elapsed on the plant clock **and** the books have covered it                                      |
  | `legs`                         | the route of each execution              | readiness waits on exactly the legs scoring prices                                                                |
  | `horizon`, `lookback`          | 10s, 1s                                  | the furthest horizon; the -1s horizon reaches one second back                                                     |
  | `by`, `retain_by`              | `sym`, `sym` + `source`                  | readiness waits on a leg; eviction keeps each source's last book of a leg                                         |
  | `identity`, `remember`         | `venue` + `fill_id`, 1 day               | a redelivery before scoring replaces the pending execution; one scored within a day is dropped                    |
  | `max_lateness`                 | 30s                                      | how late an execution may arrive and still find its books; bounds the history (the kind's default is a day)       |
  | `expire_after`                 | 5m10s                                    | an execution whose books never cover it is given up on, with the reason, in `.qpipe.job.<name>.expired`           |

The horizons are `-1s, 0, +1s, +10s` (`job_spec` in
`src/etl/transforms/fx_markout_outputs.q`). The job waits for the furthest, and
publishes every horizon of an execution together.

## What readiness costs, stated plainly

A book covers an instant only once a row at or after it has arrived: a book not
yet delivered could still be the one that supersedes the last, and scoring
against the last would be a guess. So an execution on a leg that goes quiet is
not scored until that leg next updates, and is given up on at `expire_after` if
it never does, with the reason `reference has not reached ...` (or
`no reference for ...` if a leg has never been quoted). This is the horizon
kind's existing `reference` readiness, kept; the wall condition added by `both`
stops an execution being scored early, it does not let one be scored without
coverage.

Under the same rule a live job and a refill can differ in one place only: an
execution whose books never cover it is **not scored** live (and sits in
`expired`), and is scored with status `unavailable` by a refill. For executions
that are covered, the live row and the refill's are identical, and a test holds
them to that.

## Recovery

The pending queue, the book history and the deduplication memory are
**intentionally ephemeral**, as `demo_markout`'s and `crypto_markout`'s are: the
jobs publish derived rows, not a book of record. Executions waiting for their
horizons at a restart are not scored by the restarted job.
`hdb_fx_markouts_backfill1` and `hdb_fx_markout_wide_backfill1` re-derive any
window from the HDB with the same calculation and `replace` the rows under the
same key, so a gap is a refill, not a loss. A failure in the calculation or in
publishing leaves the eligible executions queued for the next tick (the horizon
kind publishes before it evicts); a test forces each.

## Deduplication has a bound

Execution identity is `(venue; fill_id)`. A redelivery **before** scoring
replaces the pending execution (the last delivery wins, within a batch too). A
redelivery **after** scoring is dropped while the identity is remembered, which
is `remember` (a day, on the plant clock). Past that, a redelivery is a new
execution and is scored and published again; the bounded twin's key makes that
an idempotent replace in the HDB.

## The bounded twins

`hdb_fx_markouts_backfill1` (long) and `hdb_fx_markout_wide_backfill1` (wide)
read `hdb_fx_markouts`: the HDB's `executions` for the window and the
`market_data` of the legs those executions route, from the window's start less
the lookback to its end plus the furthest horizon, and each source's last book
before that (looked back up to seven days). They apply the live job's
calculation and layout and add one thing the live job cannot have: `time` is
`source_time` plus the furthest horizon, the earliest a live row could have been
stamped. `on_conflict` is `replace` on `source_time`, so a window the live job
also scored is replaced, not doubled.

## Adding a source

1. Write the adapter (see above) and register it as a `.qetl.transform`.
2. Add a line to the `executions` or `market_data` normalizer's `input`:
   `` `your_table!`your_transform ``.
3. Nothing else. The calculation, the layouts, the jobs and the horizon engine
   are unchanged.
