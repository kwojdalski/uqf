# Databento: a live market-data feed

A live market-data feed: an external Python handler publishes raw MBP-10 rows,
and `databento1` folds them into the book shape. Starting, stopping and
inspecting the stack as a whole is in [running the uqf stack](../guides/uqs.md).

`uqs databento start`/`stop`/`status` subscribe to
[Databento](https://databento.com) and stream MBP-10 into this stack. It is the
live counterpart to the ODBC backfill in
[`new-pipeline.md`](../guides/new-pipeline.md): same vendor, same schema, same
fold - the difference is only whether the rows arrive from a historical query or
a subscription.

```
uqs databento start                                  # XNAS.ITCH, AAPL/MSFT
uqs databento start --dataset XNAS.ITCH --symbols AAPL,TSLA
uqs databento status
uqs databento stop
```

Needs `$DATABENTO_API_KEY` (Databento's own variable, so an existing export
works). It refuses to start without one rather than failing on its first call.

**Two halves, and the split is the point.** A Python handler holds the
subscription and publishes raw MBP-10 onto `databento_mbp10` - forty per-level
columns, exactly as Databento sends them. `databento1`, an ordinary streaming
job, subscribes to that and republishes `databento_book`, folding the forty
columns into four level-0-first vectors with **the same `.qetl.transform`
transform the backfill uses**. The fold exists once, in q, with its own worked
examples; the Python side decides nothing about what a book is.

The handler is not a process.csv row, for the same reason cryptorust is not: a q
process cannot hold a Databento subscription, so it gets a pidfile and a
subprocess rather than a `torq.sh` entry. `databento1` *is* a normal row and
starts with the stack.

```
uqs query "select from databento_book" --port 6052   # rdb1
uqs query "select time, ts_event, sym, price from databento_book" --port 6052
```

Rows carry **both** clocks: `time` is stamped by the tickerplant on receipt,
`ts_event` is Databento's own. Their difference is the feed's latency - and a
feed whose venue clock is wrong is visible instead of silent, which a Binance
book stamped 1973 in the crypto recorder was not.
