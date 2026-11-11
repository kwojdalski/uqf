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

This page is the contract and the decisions. The calculation is the markout core
(`src/etl/transforms/markout_core.q`) run under the FX domain module
(`src/etl/transforms/fx_domain.q`): the core owns the horizons, the reference,
the clocks, the identity and the money; the domain owns the convention route,
pips, leg attribution and pip value. `src/etl/transforms/fx_markout.q`,
namespace `.qpipe.transform.fx_markout`, is the FX declaration and spec. The
stored datasets carry the markout contract's columns ([the
contract](../architecture/markout-contract.md)) with FX's between them.

## The two inputs

Nothing new is invented: the existing contracts are sufficient.

  | Input         | Columns the calculation reads                                                                                               | Clock         |
  | ---           | ---                                                                                                                         | ---           |
  | `executions`  | `source_time`, `venue`, `sym`, `side` (+1 buy, -1 sell), `size`, `price`, `fill_id`, `clock`                                | `source_time` |
  | `market_data` | `source_time`, `sym`, `source`, `bid`, `ask`, `clock` (the flat top of a snapshot per row, arranged: see [Memory](#memory)) | `source_time` |

The plant's receipt `time` is **not** an input, so it cannot leak into the
clock. An execution is identified by `(venue; fill_id)`.

Both tables also carry `clock` (`venue` or `receipt`, #1925): whose clock
`source_time` is. The calculation reads it: an execution is scored only against
books on its own clock, a leg whose only book within `max_age` is on the other
clock is `clock_mismatch`, and a fill with a null or `unknown` clock is excluded
with the reason `no_clock`.

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

`pips` is `pip_factor × move`, where `move` (the contract's column) is the
signed move in price units. `pip_value` is the money one pip is worth per trade
in the quote currency, `size ÷ pip_factor`. `quote_amount` is `size × move`;
`report_amount` is that in the snapshot's `report_ccy` (USD in the jobs) at the
book of the execution's instant. A quote currency with no declared rate in the
spec's `rates` is `no_rate`: the pips and `quote_amount` are kept and
`report_amount` is null. A site declares the rate book each of its currencies
converts through.

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
the instant; level 0 only (the flat `bid` and `ask` market_data stores, which
are `.qf.mkt.top_sides` taken at write time: sizes decide whether a level is
usable, never what the price is, so there is no depth sweep). A source that has
withdrawn a side contributes nothing on it, and does not fall back to an older
book. Across sources, the best bid and best ask among those no older than
`max_age` - reference variant #1 (`.qetl.reference`, equal bit for bit to
`.qf.mkt.best_across_venues`; the spec's `reference` key names another).
`max_age` defaults to `.qf.mkt.reference_max_age` (5s), the age `crypto_markout`
and `posbook` use: one definition of how stale a reference may be. Both the live
jobs and the `hdb_fx_markouts*` backfills score with the job spec's `max_age`
(`.qpipe.transform.fx_markout_outputs.job_spec`), and the jobs pass the same
value to their horizon declaration, so a source that stopped quoting neither
sets a price nor is kept in the book history. A leg whose every source is older
than that has no book, so the row is `unavailable` (see below). `0Wn` ("as of
however old") is still accepted for a single-source site, but is not the
default: with several sources it lets a source that went quiet set the best bid
or ask indefinitely (#1410). The horizon then keeps the as-of history, one row
per source before the oldest waiting fill, rather than a max_age window (#1480).
A site with sparse cross-pair quoting declares a longer age in its spec.

**Policy change (#1410), before and after.** A buy at 1.1001; source `a` quotes
1.1000/1.1010 (mid 1.1005) every few seconds; source `s` quoted 1.1006/1.1008
once, 20s before the execution, and has been silent since. Before: the reference
is 1.1007 at every horizon (the stale `s` book wins both sides), markout +6
pips, status `ok`, nothing flagged. After: the reference is 1.1005, markout +4
pips. If `a` too had been silent past 5s, the leg is `unavailable` rather than
priced at 1.1007. Pinned by
`test_a_source_that_stopped_quoting_does_not_set_the_live_reference`,
`test_the_bounded_twin_ignores_the_dead_source_as_the_live_job_does` (live
equals bounded),
`test_the_history_drops_the_dead_sources_book_once_nothing_can_need_it` and
`test_both_markout_families_take_the_reference_age_from_one_definition`. Each
leg is oriented by the route and chained; the reference is the mid of the chain.
A best bid above the best ask across sources is not corrected: each source's own
crossed book is refused by `market_data`'s publish check, and a cross-source
cross is a market state, not bad data.

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

**Not migrated.** `crypto_markout` keeps its own, different policy (a best mid
in basis points). It is not rebuilt on this calculation, so its output is
unchanged.

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
  | `lateness`                     | 30s                                      | how late an execution may arrive and still find its books; bounds the history (the kind's default is a day)       |
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

Coverage is per source: every source with a book within `max_age` of the
furthest horizon must have advanced to it, not just the first to pass it. The
source wait and the reach back to the most negative horizon each last only until
`lateness` after the horizon on the plant clock; then a silent source, or books
that start inside the lookback, leave those horizons `unavailable` as a refill
does, rather than holding or dropping the execution.

Under the same rule a live job and a refill can differ in one place only: an
execution whose books never cover it is **not scored** live (and sits in
`expired`), and is scored with status `unavailable` by a refill. For executions
that are covered, the live row and the refill's are identical, and a test holds
them to that.

## Recovery

The pending queue, the book history and the deduplication memory are
**intentionally ephemeral**, as `crypto_markout`'s is: the job publishes derived
rows, not a book of record. Executions waiting for their horizons at a restart
are not scored by the restarted job. `hdb_fx_markouts_backfill1` and
`hdb_fx_markout_wide_backfill1` re-derive any window from the HDB with the same
calculation and `replace` the rows under the same key, so a gap is a refill, not
a loss. A failure in the calculation or in publishing leaves the eligible
executions queued for the next tick (the horizon kind publishes before it
evicts); a test forces each.

## Deduplication has a bound

Execution identity is `(venue; fill_id)`. A redelivery **before** scoring
replaces the pending execution (the last delivery wins, within a batch too). A
redelivery **after** scoring is dropped while the identity is remembered, which
is `remember` (a day, on the plant clock). Past that, a redelivery is a new
execution and is scored and published again; the bounded twin's key makes that
an idempotent replace in the HDB.

## Moving to the contract's columns (#1935)

`fx_markout` and `fx_markout_wide` gained the contract's columns: `clock`,
`price`, `quote_ccy`, `ref_t0`, `ref_t1`, `move`, `quote_amount`, `rate`,
`rate_source`, `rate_sym`, `rate_time` and `report_amount`, plus FX's
`pip_value` and `config_id` (below). The core columns keep their relative order;
FX's own sit between them. Both workers declare `revision` 1, so every window is
planned again under `<release>@r1` and the new columns are written.

History is filled, not recomputed. `hdb_fill.q` (the repair behind
`uqs data check --apply`) adds each missing column to every partition and
derives what the stored rows already say: `clock` is `unknown` (no stored fill
says whose clock it was scored on), `quote_ccy` is the pair's quote leg, `move`
is `pips ÷ pip_factor`, `pip_value` is `size ÷ pip_factor`, `quote_amount` is
`size × move`, and in the long table `price`, `ref_t0` and `ref_t1` are read off
the `total` and `execution` rows of the same `(execution, horizon)`. The rate
columns and `report_amount` stay null: no rate was ever declared for a stored
row. A refill under `revision` 1 replaces a window with rows that carry every
column.

## The configuration a row was produced under (#1999)

A row does not repeat `contract_version`, `rate_timing` or `report_ccy`. It
carries `config_id`, and the write-once snapshot that id names holds those three
with the horizons, `max_age`, the declared rates, the FX convention and
tolerance and the revisions of the code and of the pair rules.
`uqs data snapshot list --dataset fx_markout` lists them and
`uqs data snapshot show ID` prints one. The jobs record their snapshot before
they score, and a batch naming a snapshot that is not on disk is held, so a row
is never visible without it. See [the markout
contract](../architecture/markout-contract.md#configuration-snapshots).

History written before snapshots held the three columns.
`uqs data check --apply` (it asks first) records one legacy snapshot per
distinct triple in each partition, writes `config_id` and drops the three
columns; a partition older than the columns reads as contract version 1 with the
rate timing and reporting currency unknown. The bootstrap fill never does this
on its own.

## The bounded twins

`hdb_fx_markouts_backfill1` (long) and `hdb_fx_markout_wide_backfill1` (wide)
read `hdb_fx_markouts`: the HDB's `executions` for the window and the
`market_data` of the legs those executions route, from the window's start less
the lookback to its end plus the furthest horizon, and each source's last book
before that (looked back up to seven days). They apply the live job's
calculation and layout and add one thing the live job cannot have: `time` is
`source_time` plus the furthest horizon, the earliest a live row could have been
stamped (the core stamps it). The source reads `clock` with the rows and names
the conversion pair the spec's rates read from a book beside the route's legs;
its readiness gate is the core's (`markout_core.unmet`), so a window whose
conversion book is absent is not claimed. `on_conflict` is `replace` on
`source_time`, so a window the live job also scored is replaced, not doubled.
`q scripts/examples/hdb_fx_markouts_example.q` runs both on the source's
fixture, each with its own status directory and destination HDB, and reads
`fx_markout` and `fx_markout_wide` back from disk.

## Adding a source

1. Write the adapter (see above) and register it as a `.qetl.transform`.
2. Add a line to the `executions` or `market_data` normalizer's `input`:
   `` `your_table!`your_transform ``.
3. Nothing else. The calculation, the layouts, the jobs and the horizon engine
   are unchanged.

## Memory

A twin's memory grows with its executions, never with the market. `market_data`
can run to tens of terabytes, and the twin does not select it into its heap: the
as-of join runs where the partitions are memory-mapped and only the books the
executions are priced against come back.

- **Flat top of book.** Every `market_data` row carries `bid` and `ask`, the
  level-0 sides as plain floats (`.qetl.plant.with_flat`, written by every
  mapping), and `rid`, its position in the partition. The depth columns stay on
  disk, mapped and untouched: no markout reads a ladder.
- **Sorted for an on-disk `aj`.** A partition is sorted by `sym`, `source`,
  `source_time` with `p#` on `sym`: uqf's own finish and TorQ's end-of-day sort
  (`scripts/torqconfig/sort.csv`, with `scripts/processes/uqs_row_ids.q`
  numbering the rows after it) both write it, and `uqs data compress --report`
  names a partition that is not.
- **One date at a time.** `.qetl.mapped.tob_at`, run by the process that maps
  the partitions (hdb1 loads it), reads each date with
  `select ... from market_data where date=d` - the date and nothing else, so
  every column stays mapped - and `aj`s the executions' probes (each leg at the
  execution and at each horizon) against it, then against the earlier dates for
  a source that was quiet across midnight. The result, one row per distinct
  matched book, is what comes back to the worker.
- **The core checks, it never sorts.** `markout_core.calc` refuses a top of book
  that is not sorted and attributed (`.qetl.mapped.require_arranged`);
  `markout_core.score` arranges a small in-memory buffer first (the live job's),
  and passes an arranged table - what a source reads - through untouched.

`scripts/gates/check.py markout-mapped` refuses a markout file that selects
`market_data` with a `where` beyond the date, or names a depth column.
`docs/architecture/markout-memory.md` has the design and the measurements.
