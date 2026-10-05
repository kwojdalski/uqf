# Execution quality (markouts)

How good was each fill? A markout compares a fill's price with the market mid a
short time after it. If you bought and the mid then rose, the fill was good. Two
processes write markouts, into one table:

- **`markout1`**, live, scores fills as they stream past.
- **`hdb_markouts_backfill1`**, bounded, scores a window of fills already in the
  HDB: the ones `markout1` never saw.

![The live and backfill markout paths: each scores fills with the same function and horizons, and both write execution_quality](../diagrams/markouts.svg)

## What both compute

For each fill and each horizon (1s and 10s), the reference price is the mid,
`(bid + ask) / 2`, of the latest quote at or before `trade_time + horizon`. The
markout is `side × pip_factor × (ref_price − trade_price)`, in pips: positive
means the market moved in your favour after the fill.

Both jobs use the same function and the same horizons:
`.qexec.markout_at_horizons`, with `.qpipe.job.markout.horizons`. The backfill
reads the horizons at run time, so changing them in `markout.q` changes both
jobs, and a fill scored by both comes out identical.

A fill with no quote after it keeps a row with a null `ref_price` and
`markout_pips`, rather than being dropped, so the gap shows.

## The table: `execution_quality`

  | column         | meaning                                                        |
  | ---            | ---                                                            |
  | `time`         | `trade_time + horizon`, the instant the reference mid was read |
  | `sym`          | the pair                                                       |
  | `trade_time`   | when the fill happened                                         |
  | `horizon`      | 1s or 10s                                                      |
  | `trade_price`  | the fill price                                                 |
  | `ref_price`    | the mid at `trade_time + horizon`                              |
  | `markout_pips` | the markout, in pips                                           |

The backfill writes keyed on **`sym, trade_time, horizon`**. A fill that both
jobs scored, or a window the backfill writes twice (a second `--version`), is
replaced, not counted twice.

## `markout1`, live

`markout1` (`src/etl/streaming/markout.q`) subscribes to `trades` and `quote` on
`stp1`. It can't score a fill the moment it arrives, because the quote at
`trade_time + 10s` doesn't exist yet. So it buffers fills and quotes, and once a
second it scores the fills whose longest horizon has passed. It publishes the
rows through `stp1`, and end of day saves them to the HDB.

The buffers live in its process. A fill it never saw, because it was down or
restarting, or because the fill predates it, is never scored by it.

## `hdb_markouts_backfill1`, the backfill

A bounded job: give it a range, and it scores every fill in the HDB inside it.

```
export UQF_SOURCE_CRED_HDB_MARKOUTS=localhost:<hdb1's port>   # 6053 at the default base port
uqs backfill hdb_markouts_backfill --version v1 --from 2026-09-01 --to 2026-09-30
uqs logs hdb_markouts_backfill1 -f
```

Without the variable it runs on its fixture, a small built-in data set. That is
what tests and `--mode plan`/`--mode dry-run` use. `--trace` shows the two
queries each window sends to the HDB.

For each window `[from, to)`, one hour wide by default, the source
`.qpipe.source.hdb_markouts` (`src/etl/sources/hdb_markouts.q`):

1. reads the fills with `trade_time` in `[from, to)` from the HDB's `trades`;
2. reads, for the traded pairs, the quotes in `[from, to + 10s)` from `quote`,
   so the last fill's 10s horizon has its quote, **and each pair's last quote
   before `from`**, looking back up to 7 days (`lookback`). Without that, a fill
   early in a window whose latest quote predates the window had nothing to be
   priced against. It scored null, and the target key then replaced the live
   job's correct row with that null. A window with no fills skips this query and
   returns an empty table;
3. scores them with the shared function.

The worker (`src/etl/workers/hdb_markouts_backfill.q`) then does what every
bounded worker does: - checks the rows (an infinite markout fails the window); -
writes them into `execution_quality`'s partitions; - records the window in the
coverage ledger, so a rerun of the same range is idle; - asks the HDB to reload.

### Why it is built this way

- **The scoring is in the source, not a transform.** A bounded worker's
  transform reads exactly one input, its source's rows (`require_transform`),
  and a markout needs two tables. The source's `query` runs in the backfill
  process; only its two selects run on the HDB, which doesn't load the uqf
  library.
- **Windows are cut on `trade_time`.** A fill at 10:59:58 in the 10:00-11:00
  window keeps its 10s markout, even though that lands after 11:00.
- **It reads `trades`, our fills.** They are already in the shape the markout
  needs: a signed `side`, `trade_price` and `pip_factor`. The deal tables
  (`duckdb_deals`, `demo_deals`) aren't marked out yet. They store sides as
  `buy`/`sell`, use `rate` rather than `trade_price`, and carry no `pip_factor`,
  so they'd need a mapping step first.
- **It's named after its source**, like the other workers. `markout_backfill`
  would make `uqs job remove markout` ambiguous, because that command reads
  `<name>_backfill.q` as belonging to job `<name>`.

### Limits

- **No fill id.** Two fills of one pair at the same instant share a key, in the
  live rows and the backfilled ones alike.
- **Partitions around midnight.** The backfill partitions rows by `trade_time`'s
  date, while the live job's rows land in the partition of the moment `stp1`
  stamped them. For a fill in the last 10 seconds of a day, the two can sit in
  different partitions. The key then can't replace the live row, and the fill
  appears twice.

## Tests

- **`tests/q/test_hdb_markouts_backfill.q`:**
  - every fill is scored at every live horizon, and the horizons follow the live
    job's;
  - a buy is marked against the later mid;
  - a fill with no later quote keeps null markouts;
  - a run writes all its rows, including a fill whose horizons cross midnight;
  - a restatement replaces rather than duplicates;
  - against a stand-in HDB, each window sends exactly two queries over the right
    bounds;
  - the live path scores exactly as the fixture does;
  - an empty window doesn't query quotes.
- **`tests/q/test_every_worker_runs.q`** runs this worker, with every other,
  from init to completion; a second run is idle, and a dry run writes nothing.
