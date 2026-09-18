# The FX positions service

A running book of what the desk is holding, along the dimensions the desk
reports on, with limits and breach alerts — and it runs on stock kdb+ with
no TorQ.

Modelled on Data Intellect's
[TorQ FX positions engine](https://dataintellect.com/blog/building-a-torq-fx-positions-engine-with-claude/),
built on this repository's own machinery instead.

## Running it

```bash
# Everything in one process: plant, feed and service. No ports needed.
q scripts/processes/run_stream.q -job fx_positions -feed fx_orders_feed

# Or as separate processes, the way it would run for real.
q scripts/processes/run_stream.q -plant 5010
q scripts/processes/run_stream.q -job fx_orders_feed -tp 5010
q scripts/processes/run_stream.q -job fx_positions  -tp 5010 -port 5011
```

Then, from any q session:

```q
h:hopen `::5011
h"0!.qsub.fx_positions.book"
```

`lib/torq` is never loaded on any of these paths. All four invocations are
exercised: the single-process one by `tests/q/test_fx_positions.q`, and the
three-process one live — it did not work until #266, because the runner
handed `.qstream.wire` a raw handle it refuses, and the subscribe call it
sent the plant was malformed.

## What it is made of

| Piece | Namespace | What it does |
|---|---|---|
| [`src/portfolio/desk_positions.q`](../../src/portfolio/desk_positions.q) | `.qdesk` | Net FX exposure along declared dimensions |
| [`src/portfolio/limits.q`](../../src/portfolio/limits.q) | `.qlimit` | Limits, breach detection, alert throttling |
| [`src/etl/core/tick.q`](../../src/etl/core/tick.q) | `.qtick` | A pub/sub tickerplant in stock kdb+ |
| [`src/etl/streaming/fx_positions.q`](../../src/etl/streaming/fx_positions.q) | `.qsub.fx_positions` | The service |
| [`src/etl/streaming/fx_orders_feed.q`](../../src/etl/streaming/fx_orders_feed.q) | `.qsub.fx_orders_feed` | Synthetic order flow |
| [`scripts/processes/run_stream.q`](../../scripts/processes/run_stream.q) | `.qproc.standalone` | Runs any registered job with no TorQ |

![The FX positions service](../diagrams/fx-positions-service.svg)

## The four decisions worth knowing

### 1. This is a risk engine, not a P&L engine

`.qpos` already tracks a book per sym at weighted-average cost, carrying
realised P&L, and `.qsub.posbook` already publishes it. This service answers a
different question — *what are we holding* — along dimensions `.qpos` cannot
be keyed on, and it deliberately has **no marks and no P&L**.

Marking needs a price source, a convention for a pair never quoted, and a
decision about which currency the answer is in. `posbook` makes all three.
Two modules marking the same book two ways would drift, and exposure is a fact
about the fills alone.

So the tree now has three position modules, and each earns its place:

| | Keyed on | Cost basis | Cost per fill |
|---|---|---|---|
| `.qpos` | `sym` | weighted average | O(1) |
| `.qalloc` | anything | matched lots (FIFO/LIFO/…) | O(trades), recomputed |
| `.qdesk` | anything | none — netted only | O(1) |

### 2. A position is two currencies

Every row carries `base_qty` **and** `quote_qty`. Reporting one of them is how
a cross position hides: a desk long EURUSD and short EURGBP is not long two
things, it is long GBP and roughly flat EUR, and only a per-currency netting
(`.qdesk.ccy_exposure`) says so. Both are running sums over fills, so
`quote_qty` is the cash that actually moved rather than `base_qty` revalued —
and the difference between them is the trading result, which is what
`break_even` reports.

### 3. Limits are data, and there is no hierarchy

A limit is a row — a scope, a metric and a cap — not a registered function.
Desks change them daily and they arrive from a risk system, so they have to be
loadable and diffable rather than editable.

There is deliberately no notion of a desk-level limit versus a pair-level one.
A limits table scoped on `(sym, book, product)` polices each position; one
scoped on `book` polices the desk. The caller rolls its book up to the level
its limits are written at — that is a `.qdesk.rollup` call — so one mechanism
serves every level.

### 4. A breach is a state, so alerting needs a throttle

A position over its limit is over it on every tick until someone trades out of
it. Without throttling the desk gets one alert per timer tick and stops
reading them. `.qlimit.throttle` turns the state back into an event.

The identity it throttles on is **the scope and the metric, never the observed
value**. Including the value would mean a breached position that merely *moved*
looked like a new breach and alerted again, which defeats the whole thing —
and that bug is a one-line mistake in `.qlimit.measure`, where taking the
scope from anything but the book's own key lets a fill count or an un-melted
metric into the identity. There is a test named after it.

## Recovery

The plant logs every message it carries. A job started against an existing log
replays it **before subscribing**, so a restart mid-session rebuilds the book
it had rather than starting flat:

```
run_stream: recovered 19 message(s) from :tplog/uqflocal20260918
```

Replaying *after* subscribing would interleave historical and live batches, so
the book would be right only if nothing traded during recovery.

One thing makes this trustworthy and is easy to get wrong: the plant logs the
**table**, which is exactly what subscribers receive — not the list-of-columns
form a feed may have published. Logging the raw form would give a job one shape
live and another on recovery, and everything would work until the day
something restarted.

## What this is not

`.qtick` is not a replacement for TorQ. No discovery, no process manager, no
HDB writedown, no chained plants, no access control. It is the part a single
service needs to stand on its own: subscribe, publish, log, replay. The TorQ
path still exists and still works — `scripts/processes/torq_stream.q` runs the
same jobs, unchanged, and [the stack integration](../integrations/torq/README.md)
describes it.

The three tickerplant invariants are TorQ's on purpose, so a job behaves
identically whichever plant carries it:

1. the **plant** stamps `time`, never the publisher;
2. keyed tables are refused — a plant appends;
3. the row count comes from column length, so every column must be a list.
