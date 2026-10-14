# Book liquidity transforms

What a book can fill at a requested size, and its one-minute averages, computed
from a normalized book and nothing else (#1397). The code is
`src/etl/transforms/book_liquidity.q` (the pure steps, and below them the
adapters that turn a source's rows into the contract; one file because a
declaration may not depend on another file of its directory at load time); the
tests are `tests/q/test_book_liquidity.q`.

## Layers

  | Layer                             | Declared transform                | Reads                                      | Gives                                                      |
  | ---                               | ---                               | ---                                        | ---                                                        |
  | adapter, `market_data`            | `liquidity_book_from_market_data` | the `market_data` table                    | `liquidity_book` rows (FX books only)                      |
  | adapter, one row per ladder level | `liquidity_book_from_levels`      | `level_shape` rows (provider, table label) | `liquidity_book` rows, ladders rebuilt best-first          |
  | snapshot (pure)                   | `book_liquidity_snapshot`         | books and requested sizes                  | per book and size: sweep price, quantity, full-fill flag   |
  | minute (pure)                     | `book_liquidity_minute`           | snapshots plus `bar_start`                 | one row per book identity, size and minute                 |

The pure steps contain no table name, IPC call, credential, cursor or
source-namespace reference, and a test holds that on the source text. A source's
transport, credentials, cursor and physical table stay in its source and job
declarations; an adapter only reshapes rows and decides eligibility.

## The `liquidity_book` contract

`source_time`, `sym`, `source`, `settlement`, `category`, then the four
best-first float ladders `bid_prices`, `bid_sizes`, `ask_prices`, `ask_sizes`.
It is the `market_data` contract plus two eligibility dimensions.

`market_data` itself is **not** extended. It replaces a book per
`(sym, source)`, and posbook marks positions to the level-0 mid across sources,
so a second population under one source would overwrite the first or be mixed
into one mid. Here `(sym, source, settlement, category)` is a book's identity
and part of every output key, so populations are never merged.
`liquidity_book_from_market_data` cannot know a settlement or category and says
`unspecified`; a feed that knows them passes its own values through.

## Semantics

  | Concept                          | Behaviour                                                                                                                                                                                                                                             |
  | ---                              | ---                                                                                                                                                                                                                                                   |
  | source label vs provider         | `source` is the independent liquidity provider. A physical table label is not identity: `liquidity_book_from_levels` takes one and ignores it. The transforms cannot know two `source` values are one liquidity; an adapter gives them one value.     |
  | settlement, category             | Identity columns, never collapsed. Distinct populations of one provider are distinct books and distinct output rows. An adapter that wants one population filters it (`eligible_settlements`, `eligible_categories`); empty means all, kept apart.    |
  | ladder ordering                  | Best-first: bids descending, asks ascending. The levels adapter sorts. The snapshot does not re-sort: a side whose usable levels are not best-first is unusable (null price, zero quantity, not full) rather than priced wrongly.                     |
  | quantity units                   | Base-currency units, requested sizes in the same units. The levels adapter converts its feed's units (`qty_scale`) once; the pure steps never convert.                                                                                                |
  | insufficient depth               | Quantity is what the sweep filled, never more than the request. A partial sweep is never full. Its price is the blended price of the partial fill.                                                                                                    |
  | withdrawal, empty side           | An empty ladder is a side with nothing: null price, zero quantity, not full. The book is still a snapshot: zero liquidity, not no data.                                                                                                               |
  | invalid levels                   | Null, non-positive or infinite price or size are skipped (`.qbook.level_ok` inside `.qbook.sweep_price`). Ragged ladders make that side unusable; the adapters drop a ragged row. A non-positive or non-finite requested size is refused.             |
  | duplicate liquidity              | Rows with one `(sym, source, settlement, category, source_time)` are one observation (greatest content wins, independent of delivery order). A repeated price in one book side is a redelivery, the last row wins; quantities are not summed.         |
  | malformed rows                   | Dropped by the adapter, never repaired: null time, sym, provider, settlement or category; a side other than bid/ask; a ladder that is not an aligned pair of lists.                                                                                   |

Cross-provider consolidation is not done here (`superbook1` merges FX books by
pair); summing providers is the caller's decision.

## Minute aggregation

Per `(sym, source, settlement, category, req_size, bar_start)`, windows
half-open `[bar_start, bar_start + 1m)` on the snapshot's `source_time`, cut by
`.qetl.job.stream.bars.assign`.

  | Column                             | Meaning                                                                                              |
  | ---                                | ---                                                                                                  |
  | `snapshots`                        | snapshots in the window, withdrawn books included                                                    |
  | `bid_obs`, `ask_obs`               | snapshots that priced that side                                                                      |
  | `bid_price`, `ask_price`           | mean of the snapshots' prices over the priced snapshots only; null if none, never a zero             |
  | `bid_qty`, `ask_qty`               | mean available quantity over all snapshots; an empty side is a real zero                             |
  | `bid_full_ratio`, `ask_full_ratio` | full snapshots over all snapshots: insufficient and empty snapshots count against                    |

**The average is over snapshots, each weighing the same: it is not a
duration-weighted average.** A book that stood for a minute and a book that
stood for a second weigh the same. Time-weighting would be a separate semantic
change. A window with no snapshot has no row; a window of only withdrawn books
has a row with `bid_obs` and `ask_obs` of zero and null prices.

The aggregation does not depend on arrival order. Late data is handled by the
caller: the streaming bars kind drops, and logs, a snapshot arriving after the
window's lateness; a backfill reads the whole window and so includes it, and its
rows replace the live job's. That difference is intended.

## The registered job

`book_liquidity_1m1` (`src/etl/streaming/book_liquidity_1m.q`, a bars job) reads
`market_data` and publishes `book_liquidity_1m`; its backfill twin
`hdb_book_liquidity_1m_backfill1` reads the HDB's `market_data` through the
`hdb_market_data` source. Both apply `.qpipe.transform.book_liquidity_1m.bars`,
which is the market_data adapter, `snapshot` at the sizes in
`.qpipe.transform.book_liquidity_1m.sizes` (1m, 5m, 10m), the window cut, and
`minute`. A test replaces those three with recorders and drives the registered
job to show it calls them.

The bars kind needed no extension: it already supplies half-open windows on an
event-time column, lateness, end-of-day close and replay, and its transform is
the aggregation. Its `by` columns must be in the events table, so the window
groups are `(sym, source)`; `settlement`, `category` and `req_size` are columns
of the result.

Process: `book_liquidity_1m1`, in no profile and not in `uqs start`
(`python/uqs/src/uqs/model/profiles.py`, `UNPROFILED`): a new standing process
costs one plant connection against the community licence's sixteen, and the fx
profile that runs `marketdata1` already holds them all.
