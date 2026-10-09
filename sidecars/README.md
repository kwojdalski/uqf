# Sidecars

Each folder here is a [sidecar bundle](../docs/guides/sidecar-bundles.md): jobs
kept apart from the tree's own, installed only when asked for.

  | Bundle                  | What it is                                                              |
  | ---                     | ---                                                                     |
  | [`mockups`](mockups/)   | Processes that run entirely on generated data, for exercising the stack |

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
UQF_SOURCE_CRED_MOCK_TRADES=42 uqs backfill mock_trades_backfill \
    --version v1 --from 2026-09-01 --to 2026-09-08
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
python3 scripts/test.py bundles
```

Each bundle is installed into a temporary copy of the checkout and the whole q
suite runs there, its own tests included.
