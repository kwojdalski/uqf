# Kafka client flow

A worked example of consuming Kafka topics: a consumer publishes every record
undecoded, and `kafka_flow1` decodes each declared topic into its plant table,
drops the records the broker has already delivered, and keeps the ones it cannot
decode. Starting, stopping and inspecting the stack as a whole is in [running
the uqf stack](../guides/uqs.md).

![The consumer polls the topic, publishes to stp1 and only then commits the offset; kafka_flow1 subscribes, compares each record with a per-topic, per-partition high-water mark held in process state, publishes only new records as client_flow and the ones it cannot decode as kafka_dead_letter](../diagrams/kafka.svg)

```
uqs connector start kafka                                      # localhost:9092, uqf.client.flow
uqs connector start kafka --brokers broker:9092 --topic fx.deals --group uqf-kafka-flow
uqs connector status kafka
uqs connector stop kafka
```

Needs a reachable broker and `confluent-kafka`, and **neither ships with this
repository**. That follows the rule
[`singlestore_odbc.q`](../../src/etl/core/singlestore_odbc.q) states for the
ODBC driver --- a public single-host demo cannot require infrastructure nobody
has --- and it binds harder here, because a broker is heavier than a driver. The
q half is therefore demonstrable on its own: `tests/q/test_kafka_ingest.q` and
`tests/q/test_kafka_flow.q` prove the decoding, the deduplication, the commit
order and the recovery with a fake transport, no broker, no network and no
Python.

## The q feed: `kafka_feed1`

A q process holds the subscription with KX's
[`kfk`](https://github.com/KxSystems/kafka) interface (a wrapper over
`librdkafka`) and publishes the envelope; the Python consumer above publishes
the same table and is the alternative where the native library is not built.
**Run one per consumer group** --- two members of a group split the partitions,
so each would see half the topic. It is a declared feed
([`src/etl/streaming/kafka_feed.q`](../../src/etl/streaming/kafka_feed.q)), so
TorQ starts it like `fxfeed1`, with a tickerplant handle, port, heartbeat and
process row; it is on demand (`uqs start kafka_feed1`), never in the default
start. Consumption stays outside the tickerplant: this is a client of it.

```
export UQF_KFK_HOME=$HOME/.local/kfk          # holds kfk.q and libkfk.so
export UQF_KAFKA_BROKERS=broker:9092
uqs start kafka_feed1
```

**Version pin.** KxSystems/kafka **v1.6.6** (commit `d5840faa`, Apache-2.0), the
last release for kdb+ 4.x, loaded with `2:` from `libkfk`. KX's 2.0.0 is a KDB-X
*module* (loaded with `use` in KDB-X) with a different API and is not supported
here --- there is no 2.0.0 code path. v1.6.6 was built against librdkafka 2.16.0
and run on KDB-X 5.0 (2026.07.23, macOS arm64) against Apache Kafka 4.3.1;
**kdb+ 4.x itself was not available to test on.**

**Building the library** (into a directory of your choosing; nothing is
installed system-wide, and `lib/` is untouched):

```
# librdkafka v2.16.0
./configure --prefix=$PREFIX --disable-sasl --disable-lz4-ext --disable-ssl && make && make install
# kfk v1.6.6 (curl -O the k.h from KxSystems/kdb c/c/k.h)
cc kfk.c -m64 -DKXVER=3 -shared -fPIC -L$PREFIX/lib -lrdkafka -lz -lpthread -O2 \
   -I$PREFIX/include -I. -undefined dynamic_lookup -o libkfk.so      # Linux: drop -undefined..., add -lrt
cp kfk.q libkfk.so $UQF_KFK_HOME/
```

(`--disable-ssl --disable-sasl` is for a plaintext test broker; omit both for
TLS or SASL.)

**Configuration is environment only** (`UQF_KAFKA_<SETTING>`, listed in
[`environment.md`](../reference/environment.md)): brokers, topics, consumer
group (default `uqf-kafka-q`, never a group another application uses), initial
offset policy (`OFFSET_RESET`, default `earliest`), security protocol, SASL
mechanism and user, CA file, and `SASL_PASSWORD_ENV` --- the *name* of the
variable holding the password, so a secret is never in a file or a process
table. `enable.auto.commit` is forced off.

**Optional.** Nothing loads the library at start-up of any other process or in
any test. `kafka_feed1` loads it in `on_start`; with `UQF_KFK_HOME` or
`UQF_KAFKA_BROKERS` unset it refuses to start and says which. The unit tests use
a fake `kfk` ([`test_kafka_feed.q`](../../tests/q/test_kafka_feed.q)).
[`tests/q/smoke_kafka_feed.q`](../../tests/q/smoke_kafka_feed.q) runs the feed
against a real broker and the real library and prints `SKIPPED: <reason>` when
either is absent.

**Shutdown, reconnect, rebalance.** On exit the feed publishes what it holds,
commits it, unsubscribes and closes the client, which leaves the group cleanly.
librdkafka reconnects on its own; the error callback logs. When the group
rebalances, offsets of partitions the consumer no longer holds are not
committed, and the new owner's re-read is absorbed by `kafka_flow1`'s marks.

## Transport and decoding are two things

Whatever holds the subscription publishes one **envelope** row per record on
`kafka_message`, and decodes nothing:

  | column        | what                                                                |
  | ---           | ---                                                                 |
  | `topic`       | the record's topic --- from the broker, never the payload           |
  | `partition`   | its partition                                                       |
  | `offset`      | its offset in that partition                                        |
  | `msg_key`     | the message key as received (empty when there is none)              |
  | `broker_time` | the broker's own timestamp (null when the producer set none)        |
  | `payload`     | the payload as received --- a string, not interpreted               |

What a payload *means* is decided in q, per topic, by a declaration
([`src/etl/transforms/kafka_client_flow.q`](../../src/etl/transforms/kafka_client_flow.q)
is the worked one):

```q
decl:`mapping`output!(`kafka_client_flow;`client_flow)
.qetl.kafka.topic.define[`uqf.client.flow;decl]
```

The mapping is a declared [`.qetl.transform`](../../src/etl/core/transform.q),
so its examples are verified on every test run. Its input is the record's four
coordinates followed by the payload's fields, each typed; its output is rows for
a plant table that must carry `topic`, `partition` and `offset`. A second topic
with its own wire shape is another file like that one --- nothing in the
consumer, the envelope or `kafka_flow` changes. A second wire *format* (Avro,
protobuf) is a function added to `.qetl.kafka.decoders`; the mapping above it
does not change.

JSON is read as the payload's declared fields; extra fields are ignored. Because
q reads every JSON number as a float, a `long` field refuses a number from 2^53
upwards and accepts the same value sent as a string --- refusing is the point, a
rounded trade id is worse than a dead-lettered record.

## A record is never silently dropped

`kafka_flow1` sends a record it cannot map to `kafka_dead_letter`, with its
coordinates, key and payload as received:

  | `reason`        | when                                                                   |
  | ---             | ---                                                                    |
  | `no_mapping`    | no `.qetl.kafka.topic.define` names the topic                          |
  | `empty_payload` | the record had no payload (a tombstone, or a producer bug)             |
  | `malformed`     | not JSON, not an object, or a declared field absent, null or mistyped  |
  | `mapping`       | decoded, but the mapping refused the row (a non-positive quantity)     |

One refused row does not take its batch with it. The count by `reason` is the
health of a topic.

```
uqs query "select n:count i by topic, reason from kafka_dead_letter" --port 6052
```

## Where the offset commit goes

There are two places to put it, and neither is free.

  |                      | what a crash in the gap does   | can anything tell?                                  |
  | ---                  | ---                            | ---                                                 |
  | commit, then publish | the record is lost permanently | **no** — the plant never sees a row that existed    |
  | publish, then commit | the record is delivered twice  | **yes** — the coordinates are unique in the cluster |

This takes the second, on the rule that a recoverable failure beats an
undetectable one. `enable.auto.commit` is off --- left on, librdkafka commits on
its own timer and the ordering becomes a race --- and the commit runs
synchronously only after the publish has returned.

That choice is only half an answer. Committing after publishing *guarantees*
duplicates on any restart, so something has to remove them, and that is
`kafka_flow1`: it compares each record's `(topic;partition;offset)` against a
per-`(topic;partition)` high-water mark and decodes only what is above it.
**Neither half is correct alone.** The consumer's ordering without the dedupe is
just duplicates; the dedupe without the ordering has nothing to work on.

`kafka_message` therefore records **every delivery**, repeats included: it is
the audit trail the decoded tables are checked against. The decoded tables
record each record once.

**The topic is part of the key.** Offsets are per partition, so two topics'
offset 5 on partition 0 are two records; a key of `(partition;offset)` would
drop the second topic's records as repeats of the first's.

### What the tickerplant's acknowledgement does and does not promise

The publish is a synchronous `.u.upd` to the segmented tickerplant, so
"returned" means the tickerplant ran its update handler without error. In its
default `defaultbatch` mode that handler has also handed the record to the log
file's write handle (subscribers are published to on the next timer); in
`memorybatch` it has not even done that; in either, flushing the write to disk
is the operating system's. So the claim is *the tickerplant had the record when
we committed* --- **not** *the record survives a host crash*. The ordering buys
the direction of the failure: a crash, a refused commit or a rebalance after the
publish redelivers, and the marks absorb it. A publish that throws commits
nothing, and the polled records are held and published again on the next cycle.

## Restarts

The high-water marks are `kafka_flow1`'s own process state. It declares replay,
so on a restart it reads the day's log: the rows it already published (decoded
and dead-lettered) say which records were handled, and the raw `kafka_message`
rows are held until the whole log is read, then the ones *not* handled are
processed once.

The handled records are a **set**, not a mark, for the recovery: a crash between
two tables' publishes leaves a gap below the highest handled offset, and a mark
would call the gap done.

## Limits worth knowing

- A topic maps to one output table. Routing several event types from one topic
  to several tables is a different declaration, not yet written.
- A publish that throws inside `kafka_flow1` leaves its marks where they were
  and its batch unhandled; the rows are in the log, so the next restart's replay
  handles them.
- Only the current day's log is replayed, as for every replaying job.
