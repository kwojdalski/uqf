# Markout contract

**Status:** proposed for approval (#1923). Nothing on this page is built yet. It
is the contract that #1924 to #1939 implement, and each of those issues links
back here. Where the decision record in #1923 leaves something open, the page
proposes an answer and lists it under [Open questions](#open-questions); the
proposal stands only once it is approved.

## Why this exists

The tree has two markout implementations, and each has its own horizon
expansion, reference lookup, units and clock:

  | Job              | Scoring                                                                        | Reference                                         | Unit | Clock                                                                     |
  | ---              | ---                                                                            | ---                                               | ---  | ---                                                                       |
  | `fx_markout`     | `src/etl/transforms/fx_markout.q:359-370`                                      | best bid/ask across sources, routed by convention | pips | `source_time` on both inputs (`src/etl/streaming/fx_markout.q:14-15`)     |
  | `crypto_markout` | `src/etl/streaming/crypto_markout.q:77-89`                                     | best mid across venues, single leg                | bps  | plant receipt `time` on both (`crypto_markout.q:26-31`)                   |

Only `fx_markout` reads the canonical `executions` and `market_data`, and it is
hard-wired to FX. It routes currency pairs, scores in pips and attributes by
leg. It also excludes every other instrument as `no_route`
(`fx_markout.q:151-157`, `:204`). Adding a measure, an asset class or a layout
means writing another copy. This contract replaces the two with one core.

## Layering

```
core            src/etl/transforms/   knows no source, no vendor, no implementation
  ^
domain modules  FX, crypto, equity    resolver, presentation measures, optional attribution
  ^
implementations jobs, workers, sources, datasets, site-owned sidecars
```

- **The core** is a pure transform over `executions` and `market_data`. It
  expands horizons, calls the resolver, computes the move and the money,
  converts to the reporting currency, assigns statuses, enforces the clock rule
  and validates the spec. No file in the core names a source, a vendor or an
  implementation. A test enforces this, as `tests/q/test_fx_markout_calc.q`
  already does for `fx_markout.q` (`fx_markout.q:10-12`).
- **A domain module** (FX, crypto, equity) plugs into the core. It supplies the
  resolver, its presentation measures (pips, bps, pip value), its own spec keys
  and, optionally, leg attribution.
- **An implementation** consumes a domain module: a streaming job, a bounded
  worker, a source, a dataset in `plant_tables.q`, or a site-owned sidecar. Each
  owns its conformance tests.

### Scope

The core lives in `src/etl/transforms/` and is cross-asset. `.qf.exec` stays
FX-only. The core does not call it: `.qf.exec.markout`
(`src/execution/execution.q:24`) is `side*pip_factor*(ref-price)`, which is pips
and therefore FX. The core may use the cross-asset book primitives in `.qf.mkt`
that `fx_markout` and `crypto_markout` already share: `.qf.mkt.top_sides`
(`src/market_data/book.q:268`, taken at write time into `market_data`'s flat
`bid` and `ask`, #2288), `.qf.mkt.best_across_venues`
(`src/market_data/microstructure_venues.q:93`, restated over the flat top as
reference variant #1, `.qetl.reference`) and `.qf.mkt.reference_max_age`
(`microstructure_venues.q:61`).

**The market input is the flat top of book** (#2288): `sym`, `source`,
`source_time`, `bid`, `ask`, `clock`, sorted by `sym`, `source`, `source_time`
with `p#` on `sym`. The core checks that order and reads the table as it is - it
never sorts or copies it - so a twin can hand it a book read from a
memory-mapped partition. A (`sym`, `source`) is on one clock.

**The reference is a declared variant** (#2288): SELECT (venue set, staleness,
offset) over the flat keys by `aj`, TRANSFORM of one book, COMBINE across
venues. The spec's `reference` key names it (default `best_top`, variant #1:
every venue, the latest book within `max_age`, level 0, the best bid and ask).
Variant #1 is what `contract_version` 1 means, so it adds nothing to
`config_id`; any other variant is recorded in full in the configuration
snapshot.

## Datasets

There is one dataset per asset class. Each one is the core columns plus that
asset class's domain columns.

  | Asset class | Dataset                                                                                                | Domain module | Issue        |
  | ---         | ---                                                                                                    | ---           | ---          |
  | FX          | `fx_markout` (long), with `fx_markout_wide` derived from it                                            | #1933         | #1935        |
  | crypto      | `crypto_markout` (long), replacing `crypto_execution_quality`                                          | #1934         | #1936        |
  | equity      | `eq_markout` (long)                                                                                    | #1939         | #1981        |

The long rows are the stored form. A wide or nested-wide layout is derived from
the long rows by key and is never recalculated (#1932). This is already true of
`fx_markout_wide` (`src/etl/transforms/fx_markout_outputs.q:15-20`).

## Clocks

Every row of `executions` and `market_data` carries `clock`, which is `venue` or
`receipt` (#1925). The core never scores an execution against a reference on a
different clock, and **there is no override flag**: no spec key, job key or
environment variable lets a job mix clocks.

Today `source_time` means different clocks depending on the mapping, and nothing
records which:

  | Mapping                                                    | `source_time` is                                                | Where                                                  |
  | ---                                                        | ---                                                             | ---                                                    |
  | `executions.from_trades`, `from_orders`                    | receipt                                                         | `src/etl/streaming/executions.q:73-75`, `:90-93`       |
  | `executions.from_crypto_trades`                            | venue, or receipt where the recorder sent no venue time (#1936) | `executions.q`; the fill feed is polled                |
  | `market_data.from_quote`, `from_fx_orderbook`              | receipt                                                         | `src/etl/streaming/market_data.q:6-8`, `:30-45`        |
  | `market_data.from_crypto_book`                             | venue                                                           | `market_data.q:47-60`                                  |
  | `market_data` from `eq_orderbook` (databento bundle)       | venue                                                           | `sidecars/databento/eq_orderbook.q`, `to_market_data`  |
  | `fx_source_adapters` (deal tape and depth)                 | venue                                                           | `src/etl/transforms/fx_source_adapters.q:37`, `:120`   |

So the rule has consequences today:

- Crypto fills used to carry receipt time while crypto books carry venue time,
  so the two could not be scored against each other until the cryptorust
  recorder stamped the venue's trade time. It now does
  (`crypto_trades.source_time`, cryptorust#1062), the executions mapping puts a
  fill that has one on the `venue` clock and one that has none (a row from
  before the column, an older recorder) on `receipt`, and #1936 is built: the
  retired `crypto_markout` job avoided the problem only by reading plant `time`
  on both sides.
- A row whose clock is `unknown`, the value #1925 gives an ambiguous historical
  row, is never scored.

## Money

The core computes a quote-currency amount: `side * size * (ref - price)`. `size`
is in base units and `price` in quote units
(`src/etl/streaming/executions.q:28-29`), so the product is in the instrument's
quote currency. The core stores the move in price units, `side * (ref - price)`,
and the money is `size` times that.

Pips, bps and pip value are **domain presentation**. They are derived from the
move and stored as domain columns:

- FX pips are `pip_factor * move`. Today's `pips` is
  `side*pip_factor*(ref-price)` (`fx_markout.q:308-312`).
- Crypto bps are `10000 * move % price` (`crypto_markout.q:86-87`).

## The horizon grid

A job declares its horizons once, in its spec, and every consumer reads that one
validated list: the reference fetch (`markout_core.reach`, `lookback`), the
calculation, the configuration snapshot, the live holdback (`markout_core.wait`)
and any output naming. A grid is refused, by name, unless it is a non-empty list
of distinct, finite timespans in whole milliseconds.

`markout_core.horizon_labels[horizons]` names each horizon by its exact
milliseconds in input order: `-0D00:00:01` is `neg_1000`, `0D` is `0`, `+500ms`
is `500`. `horizon_columns[stem;horizons]` builds `stem_label` column names from
the same labels. The shipped layouts carry a `horizon` column, not a column per
horizon, so nothing existing is renamed, and no stored table changes: a job that
wants per-horizon columns regenerates its schema explicitly.

## Conversion to a reporting currency

The spec declares:

- the reporting currency;
- the rate source: either a `market_data` source and pair, or an explicit fixed
  rate. A fixed rate, such as USDT = 1 USD, is never assumed: it is declared;
- the timing: `execution` or `horizon`.

Each row records the rate, its source, its time and the timing used. The rate is
looked up through the resolver contract, so the conversion pair joins the
readiness legs of the live job and the symbol filter of the backfill source
(#1931).

**Timing.**

- `execution` reads the rate at the execution's `source_time`, the same rate for
  every horizon.
- `horizon` reads the rate at `source_time + horizon`.

In both cases there is one rate per `(execution, horizon)`, shared by every
component row of that pair. Conversion is therefore linear, and FX's
reconciliation identity `total = execution + Σ leg + residual` holds in the
reporting currency as it does in pips.

**The FX translation effect.** Under `horizon` timing, the reporting amount
includes the move of the conversion rate itself, not only the move of the
instrument. The difference between the two timings is
`quote_amount * (rate_h - rate_0)`. That is the FX translation effect, and it is
not execution quality. A consumer that wants execution quality alone uses
`execution` timing.

Worked example: a USDJPY buy of 1,000,000 at 150.00, with the reference at
150.05 ten seconds later, reported in USD.

- The move is 0.05 JPY and the money is 50,000 JPY. That is 5 pips, since
  USDJPY's `pip_factor` is 100.
- With `execution` timing the rate is 1/150.00, so the amount is 333.33 USD.
- With `horizon` timing the rate is 1/150.05, so the amount is 333.22 USD.
- The 0.11 USD difference is the translation effect.

**A missing rate.** When no rate is found, the reporting amount is null and the
row's status is `no_rate`. The price-unit measures are kept: the move, and the
domain's pips or bps.

## Versioning

The contract has a version, and **a row does not carry it**: the configuration
snapshot the row's `config_id` names does (see [Configuration
snapshots](#configuration-snapshots), #1999). Consumers pin it: a consumer that
reads version N resolves every `config_id` it reads to its snapshot and refuses
any other (`.qpipe.transform.config_markout.require_version`), and a row naming
a snapshot nobody recorded is refused with the id, never read as version N. A
change to anything this page fixes bumps the version: a core column, its type,
its order, a formula or the status vocabulary. A version bump lands in one
change, together with:

- the schema in `plant_tables.q`;
- the history rewritten or refilled;
- each bounded twin's `revision` bumped, so that coverage is replanned
  ([`pipeline-declarations.md`](../reference/pipeline-declarations.md#bounded-worker-----qetljobboundeddefine),
  the `revision` key);
- every consumer updated, including the pinned sidecars (#1938).

There are **no backward-compatibility shims**: no alias columns, no dual
writing, and no reader that branches on the version.

The contract version is not FX's existing `version` column. That column holds
the attribution convention's version (`proposed_v1`, written at
`fx_markout.q:277` from `.qf.mkt.attribution_convention`,
`src/pricing/attribution_routes.q:42`). It stays as an FX domain column.

## Core columns

Every markout dataset carries these columns with these types, in this relative
order. A long dataset also carries `kind` and `step` (see [Long layout
key](#long-layout-key)). Domain columns may sit between core columns, so
`fx_markout` gains the core columns without reordering what it already has.
#1930 tests exactly this.

  | #   | Column             | Type        | Meaning                                                                                                         | Replaces or comes from                                                                                                                                                                                    |
  | -:  | ---                | ---         | ---                                                                                                             | ---                                                                                                                                                                                                       |
  |   1 | `time`             | `timestamp` | the plant's stamp; a backfill writes `source_time + max horizons`                                               | `fx_markout.time` (`src/etl/plant_tables.q:673`); backfill stamp at `fx_markout_outputs.q:141-142`                                                                                                        |
  |   2 | `source_time`      | `timestamp` | the execution's time, on its clock                                                                              | `executions.source_time` (`plant_tables.q:542`); replaces `trade_time` in `crypto_execution_quality` (`:662`)                                                                                             |
  |   3 | `clock`            | `symbol`    | `venue` or `receipt`; the execution's, and so every reference's                                                 | new, `executions.clock` (#1925)                                                                                                                                                                           |
  |   4 | `venue`            | `symbol`    | identity, with `fill_id`                                                                                        | `fx_markout.venue`; `crypto_execution_quality.venue`                                                                                                                                                      |
  |   5 | `fill_id`          | `symbol`    | identity                                                                                                        | `fx_markout.fill_id`; `crypto_execution_quality.fill_id`                                                                                                                                                  |
  |   6 | `sym`              | `symbol`    | the instrument, as `executions` spells it (`` `g# `` on the plant)                                              | `fx_markout.sym`                                                                                                                                                                                          |
  |   7 | `side`             | `long`      | `1` buy, `-1` sell                                                                                              | `fx_markout.side`; `crypto_execution_quality.side`                                                                                                                                                        |
  |   8 | `size`             | `float`     | base units                                                                                                      | `fx_markout.size`; absent from both execution-quality tables today                                                                                                                                        |
  |   9 | `horizon`          | `timespan`  | signed; zero and negative allowed                                                                               | `fx_markout.horizon`                                                                                                                                                                                      |
  |  10 | `price`            | `float`     | the fill price, in quote units                                                                                  | `fx_markout_wide.price` (`plant_tables.q:679`); `fx_markout.price_t0` on `total` rows; `trade_price` in both execution-quality tables                                                                     |
  |  11 | `quote_ccy`        | `symbol`    | the currency `price` is in, from the resolver                                                                   | new                                                                                                                                                                                                       |
  |  12 | `ref_t0`           | `float`     | the reference at the execution                                                                                  | `fx_markout_wide.ref_t0`; `fx_markout.price_t1` on `execution` rows                                                                                                                                       |
  |  13 | `ref_t1`           | `float`     | the reference at the horizon                                                                                    | `fx_markout_wide.ref_t1`; `fx_markout.price_t1` on `total` rows; `ref_price` in both execution-quality tables                                                                                             |
  |  14 | `move`             | `float`     | this row's signed move in price units; positive means the market moved in the fill's favour                     | new; FX `pips` is `pip_factor * move` (`fx_markout.q:308-312`), crypto `markout_bps` is `10000 * move % price` (`crypto_markout.q:86-87`)                                                                 |
  |  15 | `quote_amount`     | `float`     | `size * move`, in `quote_ccy`                                                                                   | new                                                                                                                                                                                                       |
  |  16 | `rate`             | `float`     | units of the snapshot's `report_ccy` per unit of `quote_ccy`                                                    | new                                                                                                                                                                                                       |
  |  17 | `rate_source`      | `symbol`    | the `market_data` source the rate was read from, or `fixed`                                                     | new                                                                                                                                                                                                       |
  |  18 | `rate_sym`         | `symbol`    | the `market_data` pair read; null for a fixed rate                                                              | new                                                                                                                                                                                                       |
  |  19 | `rate_time`        | `timestamp` | the `source_time` of the book the rate came from; null for a fixed rate                                         | new                                                                                                                                                                                                       |
  |  20 | `report_amount`    | `float`     | `quote_amount * rate`; null when `status` is `no_rate` or the row is unpriced                                   | new                                                                                                                                                                                                       |
  |  21 | `status`           | `symbol`    | see [Status vocabulary](#status-vocabulary)                                                                     | `fx_markout.status`; `fx_markout_wide.status`                                                                                                                                                             |
  |  22 | `config_id`        | `symbol`    | the configuration snapshot the row was produced under (see [Configuration snapshots](#configuration-snapshots)) | new (#1999); replaces `report_ccy`, `rate_timing` and `contract_version`, which moved into the snapshot                                                                                                   |

In a long dataset, columns 2 to 13, 16 to 19 and 22 are per
`(execution, horizon)` and repeat on every component row. Columns 14, 15, 20 and
21 belong to the row's own component. In a wide dataset there is one row per
`(execution, horizon)`, so `move`, the amounts and `status` are the `total`
component's. Every other component is a prefixed domain column, as
`execution_pips` and `leg1_pips` are today (`plant_tables.q:679`).

### Long layout key

  | Column | Type     | Meaning                                                                                | Comes from                                       |
  | ---    | ---      | ---                                                                                    | ---                                              |
  | `kind` | `symbol` | the component: core `total` and `execution`; a domain adds more (FX `leg`, `residual`) | `fx_markout.kind` (`fx_markout.q:92`)            |
  | `step` | `long`   | a leg's place in its route from 1; 0 for every other kind                              | `fx_markout.step` (`fx_markout.q:31-32`)         |

They sit after `horizon`. `(venue; fill_id; horizon; kind; step)` is the long
key with no nulls, as it is today: it is the `target_key` of
`hdb_fx_markouts_backfill` (`src/etl/workers/hdb_fx_markouts_backfill.q:70`).

### Domain columns, for orientation

These are not part of the contract. They are listed so a reader can see where
each of today's columns goes.

- **FX:** `leg`, `invert`, `price_t0`, `price_t1`, `pips`, `pip_factor` and
  `version` (the convention version), as `fx_markout` has them today
  (`plant_tables.q:673`). Pip value per trade in quote currency is added by
  #1933.
- **Crypto:** `markout_bps` and a fee-adjusted measure computed from `fee` and
  `fee_ccy` (#1934). Both columns are on `executions` already
  (`plant_tables.q:542`).
- **Equity:** the declared benchmark and session fields (#1939).

## Configuration snapshots

A result row used to repeat three of its configuration's fields
(`contract_version`, `rate_timing`, `report_ccy`), which did not identify the
rest: the horizons, the reference-age limit, the declared rates, the rules that
price an instrument. A row now carries **one** reference, `config_id`, and the
snapshot it names holds the whole configuration, once (#1999).

A snapshot records:

  | Field                                                       | Meaning                                                                                       |
  | ---                                                         | ---                                                                                           |
  | `contract_version`, `code_revision`                         | this page's version, and the revision of the core's calculation                               |
  | `domain`, `domain_revision`, `datasets`                     | the domain that priced the instrument, the revision of its rules, the datasets it produces    |
  | `horizons`, `max_age`, `report_ccy`, `rate_timing`, `rates` | the core's validated parameters, the rates as declared                                        |
  | `domain_spec`                                               | the domain's own spec keys (FX's convention and tolerance)                                    |
  | id, digest, creation time, actor, reason                    | the id is the kind and 16 digits of the digest; the digest is the md5 of the rendered content |

Source versions and input-snapshot identifiers are not in it: they belong to the
materialisation (the run ledger and the coverage ledger). A snapshot establishes
the conditions of a calculation, not reproducibility; that also needs the code
at `code_revision` and the source data.

- **The id is the content's.** `config_id` is a pure function of the domain and
  the validated spec, so `calc` stamps it without reading a store, and two
  producers that ran the same configuration agree on it. The creation time,
  actor and reason are not in the digest.
- **Write-once, at the application level.** A new configuration is a new
  snapshot; recording the same one again returns it unchanged; an id reused with
  different content is refused; nothing updates or deletes a snapshot; the
  digest is recomputed on every load, and a store that fails it is refused by id.
  The store is a table in the status directory, beside the run ledger and under
  the same lock (`src/etl/core/config_snapshot.q`). It is not tamper-proof: an
  administrator can rewrite the file, and the next load says so.
- **Pinned before calculating.** A live job's `on_start` and a bounded worker's
  `pin` record the snapshot before the first calculation. The publish path
  refuses a batch whose rows name a snapshot that is not durable (the job's
  `check`, which holds the batch), so a result never becomes visible before the
  configuration that explains it. An interruption between the two leaves a
  snapshot with no results, never results with no snapshot.
- **Lineage survives a partial backfill.** The reference is on the row, so a
  window re-scored under another configuration replaces its own rows and leaves
  the others naming the snapshot that produced them; a superseded snapshot stays
  recorded.
- **Lookups never substitute.** A row whose snapshot is not recorded is an error
  that names the id. The enriched read,
  `.qpipe.transform.config_markout.enrich`, joins the producing snapshot's
  fields next to the results; `uqs data snapshot list` and `show` read the
  store.
- **History.** A partition written before snapshots holds the three columns.
  `uqs data check --apply` records one *legacy* snapshot per distinct triple in
  each partition (`markout_legacy_...`, `legacy` set, everything else unknown),
  writes `config_id`, and drops the three columns, in that order. A partition
  older than the columns reads as contract version 1 with the rate timing and
  reporting currency unknown.

The pure calculation, `markout_core.calc`, does not depend on any of this
storage: it stamps an id and reads nothing.

## Resolver contract

The resolver is the one place where a domain tells the core how an instrument is
priced. The core calls it. It never parses a symbol.

**Inputs.** The spec, and the distinct instruments (`sym`) of the executions
being scored. For conversion it also takes the instrument's quote currency and
the spec's reporting currency.

**Outputs, per instrument:**

  | Output      | Type          | Meaning                                                                                                                             |
  | ---         | ---           | ---                                                                                                                                 |
  | `ok`        | boolean       | whether the domain can price it. False means the execution is excluded with reason `no_route`                                       |
  | `quote_ccy` | symbol        | the currency `price` is quoted in                                                                                                   |
  | `legs`      | symbol list   | the `market_data` syms the reference is chained from, in chain order. A single-leg instrument is one leg                            |
  | `invert`    | boolean list  | aligned with `legs`: whether each leg's book is inverted before chaining                                                            |

**Outputs, for conversion:** the rate's leg, a `market_data` sym, and whether it
is inverted to give `report_ccy` per `quote_ccy`. When `quote_ccy` equals
`report_ccy` there is nothing to read.

**How the quote currency is supplied:**

- FX: the quote leg of the pair (`.qf.px.ccy_pair_legs`,
  `src/foundation/ccy.q:68`).
- Crypto: parsed from the symbol, so `BTC-USDT` gives `USDT` (#1934).
- Equity: declared per instrument, since a ticker does not spell its currency
  (#1939).

A resolver that cannot supply a quote currency reports the instrument as not
`ok`.

**Purity.** The output depends on the instrument and the spec only, never on
which books exist. That is today's FX rule (`fx_markout.q:40-47`), and it is
what lets readiness and scoring agree.

**The reference.** The core builds it the way `fx_markout` does today, with no
FX knowledge:

- each leg's level-0 book per source, at or before the instant
  (`fx_markout.q:224-227`);
- the best bid and ask across sources no older than `max_age`;
- each leg oriented by `invert`, then chained;
- the mid of the chain (`fx_markout.q:238-263`).

A single leg with no inversion is the plain best mid across sources, which is
`crypto_markout`'s reference (`crypto_markout.q:85`).

**Legs for readiness.** For each execution, the readiness legs are its resolver
`legs`, plus the conversion leg when the rate comes from `market_data`. The same
list serves three readers:

- the live horizon job's `legs` callback. It already works this way for FX
  (`src/etl/streaming/fx_markout.q:53-57`, `fx_markout.q:176-180`);
- the job's `keep` filter on `market_data`
  (`src/etl/streaming/fx_markout.q:47-51`);
- the bounded source's symbol filter (see [Reading
  references](#reading-references)) and its readiness hook (see
  [Readiness](#readiness)).

**Optional domain components:**

- **attribution:** extra component rows, FX's `leg` and `residual`
  (`fx_markout.q:298-344`);
- **availability:** marks an `(execution, horizon)` unpriced for a domain
  reason. For example, an equity horizon that crosses the session close is
  `unavailable` (#1939).

## Status vocabulary

### Row statuses

  | Status           | Meaning                                                                                                                               | Comes from                                                                   |
  | ---              | ---                                                                                                                                   | ---                                                                          |
  | `ok`             | priced and converted                                                                                                                  | `fx_markout.q:332`                                                           |
  | `unavailable`    | a leg has no book within `max_age` at the execution or the horizon, or the domain marked it unpriced. `move` and the amounts are null | `fx_markout.q:63-66`, `:332`                                                 |
  | `clock_mismatch` | a leg's only book within `max_age` is on another clock than the execution's. Unpriced, like `unavailable`, but naming the cause       | new (#1923's clock rule)                                                     |
  | `no_rate`        | priced, but no conversion rate. `move`, `quote_amount` and the domain measures are kept; `report_amount` is null                      | new (#1931)                                                                  |
  | `reconciled`     | FX `residual` rows only: the residual is within `tolerance`                                                                           | `fx_markout.q:334` (domain)                                                  |
  | `unreconciled`   | FX `residual` rows only: the residual is outside `tolerance`                                                                          | `fx_markout.q:334` (domain)                                                  |

When more than one applies, the first in the order `unavailable`,
`clock_mismatch`, `no_rate`, `ok` wins. A residual row takes `reconciled` or
`unreconciled` when it is priced, and `unavailable` when it is not.

### Wide statuses

A wide row adds `partial`: the markout is priced but a component is not. Its
statuses are therefore `ok`, `partial`, `unavailable` and `unreconciled`
(`fx_markout_outputs.q:62-65`, `:90-92`), plus `clock_mismatch` and `no_rate`
under the same precedence.

### Exclusion reasons

These are not row statuses. An excluded execution produces no rows. The core
reports it with its reason, as `fx_markout.q:201-208` does today: a bounded run
counts the exclusions and a live job logs them.

  | Reason        | Meaning                                                        | Comes from              |
  | ---           | ---                                                            | ---                     |
  | `no_route`    | the resolver cannot price the instrument                       | `fx_markout.q:204`      |
  | `no_identity` | `venue` or `fill_id` is null                                   | `fx_markout.q:205`      |
  | `no_time`     | `source_time` is null                                          | `fx_markout.q:206`      |
  | `bad_side`    | `side` is not `1` or `-1`                                      | `fx_markout.q:207`      |
  | `bad_price`   | `price` is not positive and finite                             | `fx_markout.q:207`      |
  | `no_clock`    | `clock` is null or `unknown`                                   | new (#1925)             |

## Spec keys

The core validates its own keys. A domain validates its own keys too, before the
core runs. A failed check throws with the first problem named, as `require_spec`
does today (`fx_markout.q:113-126`).

  | Key           | Owner | Type and rule                                                                                                                                                                                                                                                                 | Default                                                                      |
  | ---           | ---   | ---                                                                                                                                                                                                                                                                           | ---                                                                          |
  | `horizons`    | core  | a non-empty list of timespans, none null, all distinct; signed (`fx_markout.q:117-120`)                                                                                                                                                                                       | none: a job declares it                                                      |
  | `max_age`     | core  | a positive timespan; `0Wn` means however old (`fx_markout.q:124`)                                                                                                                                                                                                             | `.qf.mkt.reference_max_age`, 5s (`fx_markout.q:106`)                         |
  | `report_ccy`  | core  | a non-null symbol                                                                                                                                                                                                                                                             | none                                                                         |
  | `rates`       | core  | a dictionary from quote currency to rate source. A rate source is either `` `source`sym `` (two non-null symbols) or `` `fixed `` (a positive, finite float). A quote currency equal to `report_ccy` needs no entry. Each `sym` must be accepted by the resolver's conversion | empty                                                                        |
  | `rate_timing` | core  | `` `execution `` or `` `horizon ``                                                                                                                                                                                                                                            | none                                                                         |
  | `convention`  | FX    | a `.qf.mkt` attribution convention with `version`, `currencies`, `direct` and `exceptions` (`fx_markout.q:121-123`)                                                                                                                                                           | `.qf.mkt.attribution_convention`                                             |
  | `tolerance`   | FX    | a non-negative float, in pips (`fx_markout.q:125`)                                                                                                                                                                                                                            | `1e-6`                                                                       |

A key that neither the core nor the domain declares is refused, so a misspelled
`rate_timing` fails at load instead of being ignored. Today's `require_spec`
checks only that its own keys are present (`fx_markout.q:115`).

A quote currency that has no entry in `rates` is not a spec error. It is the
`no_rate` status for that execution, so a new quote currency is visible in the
data instead of stopping the job.

## Reading references

These rules apply to every bounded source that reads `market_data` for a
markout. They are #1926's scope. Today the rules are hard-coded in
`src/etl/sources/hdb_fx_markouts.q`.

**Date skew.** The HDB is partitioned by the plant's `time`, but the window is
on `source_time`. The source declares how many days apart the two can be. Today
this is a fixed `(-1 1)` at `hdb_fx_markouts.q:68` and `:78`. The declared skew
must be a non-negative whole number of days, and at least what the live job's
`lateness` allows for (`fx_markout_outputs.q:119`).

**Symbol filter.** Only the readiness legs are read: for the window's
executions, the union of the resolver `legs` and the conversion legs. Today the
filter is the route legs only (`hdb_fx_markouts.q:71-72`).

**Lookback.** The read reaches back from the window's start by:

- `max_age` plus the most negative horizon, when `max_age` is finite. A book
  older than that can set no reference at any instant the window needs, because
  `.qf.mkt.best_across_venues` discards it. No separate "last book before the
  window" query is needed;
- a cap the source declares, when `max_age` is `0Wn`. Within that cap, the last
  book per `(sym, source)` before the window is the as-of anchor.

Today the lookback is a fixed `7D` (`hdb_fx_markouts.q:55`), and a separate
query rescans those seven days for every window (`:74-76`). The read reaches
forward to the window's end plus the furthest non-negative horizon
(`hdb_fx_markouts.q:81-82`), and that rule is unchanged.

**Clock.** The source reads both clocks. The core decides what each execution
may see, so a source never needs to know the clock rule.

## Readiness

#1927 confirmed that a bounded markout window whose `market_data` has not landed
is scored `unavailable`, recorded as covered, and never replanned at the same
`source_version`. Planning consults only the job's own ledger. The contract
separates two cases:

- **No reference data for the window is not claimable.** The window stops before
  publish, writes no rows and no coverage, and is replanned on the next run.
- **No book for one leg is a legitimate `unavailable` row.** The window has its
  reference data, and the market did not quote that leg. The window is covered.

The readiness legs of the [resolver contract](#resolver-contract) are the input
to an optional readiness hook on the source or bounded-worker declaration. The
hook is being built under #1927, so this page states only what it is given and
what it must refuse:

- It is given the window and the window's readiness legs.
- It refuses a window that has executions but no `market_data` for those legs
  over `[window start - lookback, window end + furthest horizon)`.
- It refuses a window whose end plus the furthest horizon lies beyond what
  `market_data` holds. That limit is the reference's `complete_through`
  ([`pipeline-declarations.md`](../reference/pipeline-declarations.md#source-----qetlsourcedefine))
  or the HDB's latest `market_data` time.

The live job needs no hook. Its `ready_on` `both` already waits for each leg to
cover the horizon, and an execution whose legs never do is expired, not
published
([`pipeline-declarations.md`](../reference/pipeline-declarations.md#horizon-job-----qetljobstreamat_horizons)).

## Decisions beyond the record

Things the decision record in #1923 does not settle and a later maintainer
decision has. Unlike the open questions below, these are decided.

1. **The configuration moves out of the rows (#1999).** `contract_version`,
   `rate_timing` and `report_ccy` are no longer result columns. They are fields
   of the configuration snapshot a row's `config_id` names, with the horizons,
   `max_age`, the declared rates and the revisions that the three never
   identified. This changes the approved contract: the core is 22 columns, not
   24, and `config_id` is its last. The contract version is still a `long`
   starting at 1 (open question 5), now recorded in the snapshot.
2. **The link is a compact column, not the run ledger.** The run and coverage
   ledgers identify a window's producer, but a live row has no run, and a
   partial backfill replaces rows inside a window the ledger describes as one. A
   row-level reference is the only one that stays true of each row.

## Open questions

The decision record in #1923 does not settle these. Each has a proposal above;
none is decided until the maintainer approves it.

1. **Core order: subsequence or prefix?** The proposal is that core columns keep
   their relative order with domain columns between them. That lets #1935 add
   columns to `fx_markout` without reordering it. The alternative is a
   contiguous block after `time`, which is simpler to read but reorders
   `fx_markout` in the HDB.
2. **Do wide datasets carry the core columns?** #1935 adds them to
   `fx_markout_wide`. The proposal is that a wide row's `move`, amounts and
   `status` are the `total` component's. Confirm that #1930's conformance test
   covers wide datasets with that meaning.
3. **Are `kind` and `step` core?** The proposal is that they are required in
   every long dataset but are not core columns, because a wide row has neither.
4. **Duplicated prices in `fx_markout`.** Adding `price`, `ref_t0` and `ref_t1`
   duplicates `price_t0` and `price_t1` on `total` and `execution` rows. #1933
   must reproduce today's rows exactly, so the proposal keeps both. Should
   `price_t0` and `price_t1` be dropped in a later contract version?
5. **The contract version's name and type.** The proposal is `contract_version`,
   a `long` starting at 1, kept apart from FX's `version` (the convention's). It
   is recorded in the configuration snapshot, not on the rows (#1999).
6. **"Null money" under `no_rate`.** The record says a missing rate gives null
   money. The proposal nulls only `report_amount` and keeps `quote_amount`,
   which needs no rate. Confirm.
7. **Several quote currencies in one dataset.** The record speaks of one rate
   source. The proposal keys `rates` by quote currency, because one crypto
   dataset can hold USDT, USDC and BTC-quoted pairs.
8. **`report_ccy` required or optional?** The proposal is required, with no
   default currency. It is recorded in the configuration snapshot (#1999).
9. **Rate staleness.** Which `max_age` limits a rate book: the spec's `max_age`,
   or a separate rate age?
10. **Clock rule for the rate.** Must the conversion book be on the execution's
    clock too? The proposal is yes, by the same rule. Then a missing same-clock
    rate is `no_rate`.
11. **Readiness under `execution` timing.** Under `ready_on` `both`, a
    conversion leg must advance to `source_time + horizon`, though `execution`
    timing reads it only at `source_time`. A sparse conversion pair can then
    expire executions. Should the conversion leg get its own readiness
    condition?
12. **`clock_mismatch` as a row status.** The proposal names the cause on the
    row. The alternative is plain `unavailable`, with the mismatch counted in
    the run's facts.
13. **A `bad_size` exclusion.** Money needs `size`, but today `fx_markout`
    scores a null size (`fx_markout.q:190-193`). Adding the reason would change
    which rows FX produces, so #1933 could not reproduce today's rows unchanged.
14. **Refusing undeclared spec keys.** This is new behaviour. Today
    `require_spec` ignores extra keys.
15. **How the core receives a domain module.** As an argument, as a spec entry,
    or as a registered name? The record fixes the layering but not the call
    shape, so #1929 proposes it.
16. **Date skew and `lateness`.** Should the source's date skew be derived from
    the live job's `lateness`, instead of declared beside it?
17. **The crypto dataset's name.** `crypto_markout` (#1936): the table takes the
    job's name, as `fx_markout` does. It is a long dataset; the live job is
    `crypto_markout1` and its bounded twin `hdb_crypto_markout_backfill1`.
