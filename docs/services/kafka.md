# Kafka: a topic into the tickerplant

A worked example of consuming a Kafka topic: an external Python consumer
publishes raw records, and `kafka_flow1` drops the ones the broker has already
delivered. Starting, stopping and inspecting the stack as a whole is in
[running the uqf stack](../guides/uqs.md).

```
uqs kafka start                                      # localhost:9092, uqf.client.flow
uqs kafka start --brokers broker:9092 --topic fx.deals --group uqf-kafka-flow
uqs kafka status
uqs kafka stop
```

Needs a reachable broker and `confluent-kafka`, and **neither ships with this
repository**. That follows the rule
[`singlestore_odbc.q`](../../src/etl/core/singlestore_odbc.q) states for the
ODBC driver — a public single-host demo cannot require infrastructure nobody
has — and it binds harder here, because a broker is heavier than a driver. The
q half is therefore demonstrable on its own: `tests/q/test_kafka_flow.q` proves
the deduplication with fixtures, no broker, no network and no Python.

## Why this exists, given the stack already has feeds

`fxfeed1`, `cryptomock1` and `databento1` already show what a subscriber looks
like, so a fourth earns little. What Kafka brings that none of them face is that
**a topic and a tickerplant log are the same idea** — two ordered, replayable
records of what happened — and joining one to the other forces a question with
no comfortable answer.

## Where the offset commit goes

There are two places to put it, and neither is free.

| | what a crash in the gap does | can anything tell? |
| --- | --- | --- |
| commit, then publish | the record is lost permanently | **no** — the plant never sees a row that existed |
| publish, then commit | the record is delivered twice | **yes** — the coordinates are unique in the topic |

This takes the second, on the rule that a recoverable failure beats an
undetectable one. `enable.auto.commit` is off — left on, librdkafka commits on
its own timer and the ordering becomes a race — and `consumer.commit` runs only
after `q.sync` has returned.

That choice is only half an answer. Committing after publishing *guarantees*
duplicates on any restart, so something has to remove them, and that is
`kafka_flow1`: it compares each record's `(partition;offset)` against a
per-partition high-water mark and republishes only what is above it. **Neither
half is correct alone.** The consumer's ordering without the dedupe is just
duplicates; the dedupe without the ordering has nothing to work on.

**That is why `partition` and `offset` are columns.** They are not consumer
bookkeeping that should have stayed in Python — the plant is the thing that has
to survive a redelivery, so the coordinates travel with the row. They stay on
`client_flow` too, so any row can be traced back to the exact Kafka record. They
come from the broker rather than the payload, so a producer cannot forge them.

```
uqs query "select from client_flow" --port 6052      # rdb1
uqs query "select sym, price, partition, offset from client_flow" --port 6052
```

Rows carry **both** clocks, for the reason `databento_book` does: `time` is
stamped by the tickerplant on receipt, `broker_time` is the broker's own, and
their difference is the consumer's lag.

## What it does not survive

The high-water marks are `kafka_flow1`'s own process state. Restart it and they
are empty, so a replay straddling the restart is **not** caught. Seeding them
from the plant on startup is the obvious fix and is deliberately not done: it
needs a query against `rdb1` at wire time, which no other streaming job does,
and inventing that seam for an example would be the tail wagging the dog.

The payload is JSON, one client trade per message. A real deployment would more
likely carry Avro or protobuf against a schema registry — decoded in the
consumer either way, so q only ever sees typed columns and never a format
library. That is also why this needs none of KX's `2:`-loaded format
interfaces: they matter when q itself holds the subscription, which is a
different design and one the bundled interpreter cannot run.
