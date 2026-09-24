# An example pipeline architecture, composed from what is here

What a trading desk's system looks like when it is built out of the services
this tree implements. Not the running demo — [the stack pages](../integrations/torq/README.md)
draw that, with ports and every process — but the *shape*, so the pieces can
be seen as one system rather than as a list of files.

![A desk system in seven bands, top to bottom: sources, the plant, normalizers, engines, storage, on-demand analytics, surfaces](../diagrams/pipeline-architecture-example.svg)

Read it top to bottom, and read the **middle** first. The plant is a narrow
waist that everything passes through *twice*: a job subscribes to one table
and publishes another back onto it, and **nothing reads another job's output
directly**. That is why those arrows run both ways against a single spine
instead of chaining box to box — and it is what makes a new engine a
subscriber rather than a change to a producer.

The normalizers are the second waist. Above them each market arrives in its
own shape; below them there is one.

One arrow deliberately bypasses the plant: a bounded worker writes through
`.qio` straight into storage and records what it covered. Its rows are
history, not ticks, and a plant appends.

## 1 · Sources

Four kinds of thing put rows on the plant, and they are deliberately not
alike:

| Source | Implemented by | Shape it arrives in |
|---|---|---|
| FX venue feeds | [`fx_feed`](../../src/etl/streaming/fx_feed.q), [`quotes_feed`](../../src/etl/streaming/quotes_feed.q), [`fx_trades_feed`](../../src/etl/streaming/fx_trades_feed.q) — synthetic here, a venue adapter in production | `quote` (one bid, one ask), `trades` (sym, side, price, size, pip_factor) |
| Crypto venues | cryptorust's two kdb recorders, or [`crypto_mock`](../../src/etl/streaming/crypto_mock.q) standing in | `crypto_book` (a ladder per venue), `crypto_trades` (venue, fee, exchange id) |
| Databento MBP-10 | [`external/databento_feed.py`](../../python/uqs/src/uqs/external/databento_feed.py) → [`databento_book`](../../src/etl/streaming/databento_book.q) | raw MBP-10, folded by the same `.qxf` transform the ODBC backfill applies |
| History | bounded workers under [`src/etl/workers/`](../../src/etl/workers), run by [`.qbw`](../../src/etl/core/bounded_worker.q) | whatever the upstream holds, written through `.qio` — never via the plant |

The last row is the one to notice. A backfill writes into storage directly
and records what it covered in the [coverage ledger](../../src/etl/core/materialisation.q),
bitemporally. It never traverses the tickerplant, which is why the two halves
of the ETL tree look alike in the code and take different paths in the
diagram.

## 2 · The plant

One tickerplant, every table. In the stack it is TorQ's `stp1`; on stock
kdb+ it is [`.qtick`](../../src/etl/core/tick.q). The jobs do not
know which — they call `publish` in their own namespace and a runner wires
it — and the three invariants a job must respect are the same on both:

1. the **plant** stamps `time`, never the publisher;
2. keyed tables are refused, because a plant appends;
3. the row count comes from column length, so every column is a list.

A job never calls `.u.upd`. It calls `publish` in its own namespace, and
that is what lets the same file run under TorQ, under `.qtick`, or against a
recorder in a test — the runner decides the transport.

## 3 · Normalizers

The second waist. [`.qnorm`](../../src/etl/core/normalizer.q) is a job kind
whose instances take several tables carrying the same fact in different
shapes and publish one canonical table, with one declared `.qxf` transform
per source — refused at load if its output drifts from the canonical schema.

| Normalizer | Sources | Output |
|---|---|---|
| [`executions`](../../src/etl/streaming/executions.q) | `trades`, `crypto_trades` | one fill table: source_time, sym, venue, side, size, price, fee, fee_ccy, fill_id |
| [`marks`](../../src/etl/streaming/marks.q) | `quote`, `crypto_book` | one mid per instrument: source_time, sym, venue, mid |
| [`market_data`](../../src/etl/streaming/market_data.q) | `quote`, `quotes` | one book shape: source and source_time preserved, so a merge can tell whose liquidity it is |

A third market — a new venue, a futures feed — is a mapping in one of these,
not a change to anything downstream.

## 4 · Engines

Subscribe, hold state, republish. Each is one file under
[`src/etl/streaming/`](../../src/etl/streaming), TorQ-free, run by a generic
runner.

| Engine | Reads | Holds | Publishes |
|---|---|---|---|
| [`posbook`](../../src/etl/streaming/posbook.q) | `executions`, `marks` | a [`.qpos`](../../src/portfolio/positions.q) book: position and P&L per sym, weighted-average cost | `position` — FX and crypto in one book |
| [`fx_positions`](../../src/etl/streaming/fx_positions.q) | `orders` | a `.qdesk` book: net exposure by (sym, book, product); `.qlimit` caps | `fx_position` snapshots, `fx_limit_breach` throttled alerts |
| [`markout`](../../src/etl/streaming/markout.q) | `trades`, `quote` | buffered fills awaiting their horizons | `execution_quality` |
| [`cross`](../../src/etl/streaming/cross.q), [`vectorize`](../../src/etl/streaming/vectorize.q) | `quotes`, `wide_book` | mirrors | synthetic crosses; a reshaped book |
| [`superbook`](../../src/etl/streaming/superbook.q) | `market_data` | the freshest ladder per source, expiring | `superbook` — quoted liquidity merged across sources |
| [`arbitrage`](../../src/etl/streaming/arbitrage.q), [`crossarb`](../../src/etl/streaming/cross_arbitrage.q) | `superbook` | — | crossed levels within the merged book; the direct book against a synthetic route |

`posbook` and `fx_positions` answer different questions and are deliberately
two engines: *what did we make*, per sym, marked; and *what are we holding*,
along the dimensions a desk reports on, with no marks.
[`fx-positions.md`](../services/fx-positions.md) argues why one module
cannot honestly do both.

## 5 · Storage

Two things, and the split is the point.

`rdb` holds today in memory and `hdb` holds what the EOD wrote down — every
table the plant carries, because a table not written down cannot be asked
about tomorrow.

[`etl_coverage`](../../src/etl/core/materialisation.q) holds something
different: not rows, but **what was claimed about them**. Which window of
which dataset was published, at which `source_version`, by which run — and
bitemporally, so a window re-run later does not erase what it replaced. A
window that produced zero rows is still recorded as covered, because "ran,
found nothing" and "never ran" must not look alike.

That ledger is why the history arrow on the diagram bypasses the plant. A
backfill's rows are history rather than ticks, so they go through `.qio` into
storage directly — and the claim goes in the ledger beside them.

## 6 · On demand

Pure functions over whatever the store holds — no state, no clock, no
sockets — called from a query, a notebook or a surface.

- [`.qalloc`](../../src/portfolio/allocation.q) — P&L attribution: which
  opening trade paid for which close, under FIFO, LIFO, HIFO or weighted
  average; carried-in positions; the book as of any instant.
- [`.qrisk`](../../src/portfolio/risk.q), [`.qpos.ccy_exposure_in`](../../src/portfolio/positions.q)
  — VaR, carry, and per-currency exposure revalued into one reporting
  currency through [`.qfwd`](../../src/pricing/forwards.q)'s cross-rate
  chaining.
- [`.qmicro`](../../src/market_data/microstructure.q),
  [`.qexec`](../../src/execution/execution.q) — book pressure, microprice,
  VPIN, markout: over a `quotes` snapshot series or the
  [event tape](event-tape.md), whichever the metric needs.

Restatement belongs here too: because the ledger in §5 is bitemporal, any of
these can be asked *as of* a past instant and get the answer that was true
then.

## 7 · Surfaces

- [`uqf_frontend`](../../python/uqf_frontend) — FastAPI and a web app:
  positions, coverage, the fleet, and a control view that can start the
  stack. Every query goes through the gateway, never to a process directly.
- [`uqf_airflow_provider`](../../python/uqf_airflow_provider) — an operator
  that starts a backfill and a sensor that reads the status files
  [`.qstatus`](../../src/etl/core/status.q) writes.
- [`uqf_client`](../../python/uqf_client) and the MCP server — kola IPC from
  Python, and the stack as tools an agent can call.

## What a day looks like through it

A fill on Binance reaches `crypto_trades` from the recorder; `executions`
spells it the way an FX fill is spelled; `posbook` folds it into the same book
the EURUSD position lives in and marks it to the mid `marks` last saw from the
Binance ladder. The frontend's position view shows both, through the gateway.
At the end of the day `.qalloc` says which of the morning's buys that
afternoon's sell actually closed, under whichever convention the desk
reports in — and if a venue's history has to be re-run, the backfill writes
the corrected window beside the old one, and the ledger says which is
which.

None of that required a producer to know about a consumer.
