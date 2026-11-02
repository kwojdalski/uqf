# Sidecars

Each folder here is a [sidecar bundle](../docs/guides/sidecar-bundles.md): jobs
kept apart from the tree's own, installed only when asked for.

  | Bundle                                          | What it is                                                                                             |
  | ---                                             | ---                                                                                                    |
  | [`mockups`](mockups/)                           | Processes that run entirely on generated data, for exercising the stack                                |
  | [`cryptorust`](cryptorust/)                     | The q side of the cryptorust integration: its recorded DuckDB capture and its Kafka book snapshots     |
  | [`databento`](databento/)                       | The q side of the Databento integration: the MBP-10 ODBC source, the book fold, `databento1`           |
  | [`flink`](flink/)                               | The q side of the Apache Flink integration: `flink_vwap1` deduplicating the windows a Flink job closes |
  | [`kafka_example`](kafka_example/)               | A Kafka decoder for a bundle's own table, the precedent for a topic in a bundle                        |
  | [`kafka_crypto_example`](kafka_crypto_example/) | The runnable end-to-end Kafka example (docs/guides/kafka-crypto-example.md)                            |

A job belongs in `src/etl/` when it runs on what this repository provides:
generated feeds, the stack's own tables, or a reference example of a transport.
It belongs in a bundle when it is tied to **one outside system** that may not be
present: another project, a vendor account, an external engine. The three
integration bundles below are that second kind. `databento` and `flink` also
carry their Python connector (`*_feed.py` and the `*_streamer.py` it runs),
installed with their q into `src/etl/connectors/<bundle>/`: until the bundle is
installed, `uqs connector start databento|flink_vwap` is an unknown connector
(#1776). cryptorust's recorders stay in `uqs`
(`uqs connector start crypto|crypto-fills`), because the tables they publish
stay in the tree.

## cryptorust

  | Job                           | Kind      | Reads                                                                    | Writes                                      |
  | ---                           | ---       | ---                                                                      | ---                                         |
  | `crypto_market_data_backfill` | backfill  | the `crypto_market_data` source (cryptorust's DuckDB capture, over ODBC) | `crypto_market_data`                        |
  | `crypto_book_decoder`         | streaming | `kafka_message` (`cryptorust.book_snapshots`)                            | `crypto_book_snapshot`, `kafka_dead_letter` |

The five-level fold (`crypto_market_data`) and the snapshot mapping
(`kafka_crypto_book`) sit in the files of the source and the decoder, because a
bundle's file is placed by what it declares and a transform has no directory of
its own. `crypto_book`, `crypto_trades`, `crypto_sim_fills`, `crypto_mock`,
`crypto_markout`, `crypto_book_1s` and `hdb_crypto_book` stay in the tree: they
read tables any crypto publisher fills.
[Guides](../docs/guides/cryptorust-kafka-books.md),
[recorders](../docs/services/crypto-recorder.md).

## databento

  | Job                                           | Kind      | Reads                                           | Writes          |
  | ---                                           | ---       | ---                                             | ---             |
  | `eq_orderbook` (databento1)                   | streaming | `databento_mbp10` (the feed handler's raw rows) | `eq_orderbook`  |
  | `eq_orderbook_backfill` (databento_backfill1) | backfill  | the `databento_mbp10` source (DuckDB over ODBC) | `eq_orderbook`  |

Both apply the one `eq_orderbook` fold, which sits in the source's file.
[Service page](../docs/services/databento.md).

## flink

  | Job          | Kind      | Reads            | Writes       |
  | ------------ | --------- | ---------------- | ------------ |
  | `flink_vwap` | streaming | `flink_vwap_raw` | `flink_vwap` |

The Flink job itself (`uqs connector start flink_vwap`, needing `apache-flink`
and Java) publishes `flink_vwap_raw`; this job keeps each `(sym; window_end)`
once.

## mockups

Four jobs, none of which reads anything real:

  | Job                    | Kind      | Reads                    | Writes           |
  | ---                    | ---       | ---                      | ---              |
  | `mock_trades_backfill` | backfill  | the `mock_trades` source | `mock_trades`    |
  | `mock_positions`       | reaction  | `mock_trades`            | `mock_positions` |
  | `mock_ticks`           | feed      | nothing                  | `mock_ticks`     |
  | `mock_mids`            | streaming | `mock_ticks`             | `mock_mids`      |

- **The `mock_trades` source** uses the `mock` transport: its credential is an
  integer seed, and each window's rows are generated from the seed and the
  window's start. The same seed and window always give the same rows, over any
  date range. Without a seed, the worker reads the source's fixture: one hour of
  rows on 2026-01-02.
- **`mock_positions`** nets each published window's trades per pair.
- **`mock_ticks`** publishes one quote per pair a second, each mid a random walk
  seeded from the tick's time. **`mock_mids`** turns each quote into its mid and
  spread.

Install it, then backfill a week with seed 42:

```bash
uqs job install sidecars/mockups --dry-run
uqs job install sidecars/mockups --yes
UQF_SOURCE_CRED_MOCK_TRADES=42 uqs job backfill mock_trades_backfill \
    --source-version v1 --from 2026-09-01 --to 2026-09-08
```

Installing writes into tracked tree files (`plant_tables.q`, the catalog,
`src/etl/`). Do it in a checkout you will not commit from, or declare the bundle
for a runtime in `runtime_bundles.json` instead. The feed and the streaming job
start only when named: `uqs start mock_ticks1 mock_mids1`.

## Adding a job

Scaffold it into the bundle, then implement what the scaffold leaves failing:

```bash
uqs job new mock_quotes --publishes mock_quotes \
    --columns "source_time:timestamp, sym:symbol, px:float" --bundle sidecars/mockups --dry-run
```

## Testing

```bash
uv run pytest tests/qrun/test_bundle_suites.py -m release
```

Each bundle is installed into a temporary copy of the checkout and the whole q
suite runs there, its own tests included.
