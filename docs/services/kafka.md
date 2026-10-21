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

A runnable end-to-end example - a disposable broker, synthetic crypto trades, a
decoder in a sidecar bundle and a verifier that queries the RDB - is
[`kafka-crypto-example.md`](../guides/kafka-crypto-example.md).

Needs a reachable broker and `confluent-kafka`, the optional `kafka` extra of
`uqs` (`uv sync --extra kafka`); `uqs connector start kafka` refuses without it.
**Neither is installed by default**. That follows the rule
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
librdkafka reconnects on its own; the error callback reports each *cause* once
(below). When the group rebalances, offsets of partitions the consumer no longer
holds are not committed, and the new owner's re-read is absorbed by
`kafka_flow1`'s marks.

**Readiness is stage by stage, and "subscribed" is not one of them.** The feed
logs a `readiness` line (library, bootstrap, advertised, group, consume,
published, committed - each `ok`, `pending` or `failed`) whenever it changes.
`ok` needs proof: an assignment proves the bootstrap broker answered, the
addresses *in its metadata* (the advertised listeners, which are not the
bootstrap list) are reachable and the group joined; a polled record proves the
topic may be read. A failure is reported **once per cause** - stage, endpoint,
code, and the broker's own text - not once a second: an unresolvable advertised
hostname is one `ERROR` naming `host:port`, with the repeats counted and a
`recovered` line when it clears. A cause clears on evidence about *its own*
subject --- a record from the refused topic --- or once it has gone quiet (30 s
unreported) while the stage is proved elsewhere; another topic or broker
delivering does not clear it, and readiness says `failed` for as long as it is
still being reported.
[`kafka_diagnostics.q`](../../src/etl/core/kafka_diagnostics.q) has the stages;
[the crypto example](../guides/kafka-crypto-example.md) runs each failure
against a real broker, with a verifier that checks every stage against the RDB.

## In a release

Both clients are opt-in at `uqs deploy build` time; a default release carries
neither, and the decoders need neither. Brokers, groups and credentials stay
runtime configuration (`UQF_KAFKA_*`), and no check connects to a broker.

```
uqs deploy build --kafka-home $HOME/kfk-linux     # the native client, for kafka_feed1
uqs deploy build --with-kafka-connector           # confluent-kafka, for uqs connector start kafka
```

**`--kafka-home DIR`** copies `kfk.q`, `libkfk.so`, every shared library `ldd`
resolves it to apart from glibc's (into `lib/`), and every `LICENSE*`, `NOTICE*`
or `COPYING*` file under `DIR` (into `notices/`) to `.release/kafka/`. Build on
Linux: the build refuses a missing file, a `DIR` with no licence, a library
`ldd` cannot find, and any library that is not an ELF object for `--arch`. On
the server, `deploy.env` sets `UQF_KFK_HOME` to that copy and puts its `lib/`
first on `LD_LIBRARY_PATH`, for every process the release starts, so any job
using the native loader finds it with no setting of its own. Before anything
starts, push loads `kfk.q` in the server's q from that copy (the `kafka` stage)
and stops naming the reason when it does not load.

**`--with-kafka-connector`** exports the lock with the `kafka` extra, so
confluent-kafka's wheel for the target is installed offline with the rest.

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

What a payload *means* is decided in q, per topic, by a declaration. The mapping
is
[`src/etl/transforms/kafka_client_flow.q`](../../src/etl/transforms/kafka_client_flow.q);
the declaration sits in the job that owns the topic,
[`src/etl/streaming/kafka_flow.q`](../../src/etl/streaming/kafka_flow.q):

```q
decl:`mapping`output`job!(`kafka_client_flow;`client_flow;`kafka_flow)
.qetl.kafka.topic.define[`uqf.client.flow;decl]
```

Declaring it beside the job is what makes a process that loads only its own
declarations (every TorQ process does) have the topic's mapping.

The mapping is a declared [`.qetl.transform`](../../src/etl/core/transform.q),
so its examples are verified on every test run. Its input is the record's four
provenance columns followed by the payload's fields, each typed; its output is
the payload's columns only, and the framework appends the provenance (next
section). A second topic with its own wire shape is another mapping like that
one, declared by a decoder job (below) --- nothing in the consumer, the envelope
or `kafka_flow` changes. A second wire *format* (Avro, protobuf) is a function
added to `.qetl.kafka.decoders`; the mapping above it does not change.

JSON is read as the payload's declared fields; extra fields are ignored. Because
q reads every JSON number as a float, a `long` field refuses a number from 2^53
upwards and accepts the same value sent as a string --- refusing is the point, a
rounded trade id is worse than a dead-lettered record.

## Provenance: what every decoded row says about its record

Four kinds of metadata, four owners. **Kafka metadata** (topic, partition,
offset, the record timestamp, key, headers) belongs to the transport and is kept
once, on the envelope. **Payload metadata** (the exchange's clock, a trade id)
belongs to the decoder and is a column of its table. **Storage metadata**
(`time`) is the tickerplant's. **Operational metadata** (consumer group, lag,
decoder version, errors) is job diagnostics, never a per-row column.

Every decoded output table carries the same four columns, checked when the topic
is declared (`.qetl.kafka.topic.define` refuses a table that lacks one or types
it differently):

  | column        | q type      | meaning                                                         |
  | ---           | ---         | ---                                                             |
  | `topic`       | `symbol`    | the record's topic                                              |
  | `partition`   | `long`      | its partition                                                   |
  | `offset`      | `long`      | its offset                                                      |
  | `broker_time` | `timestamp` | the Kafka **record's** timestamp; null when the record has none |

`broker_time` is never a payload time and never stands in for one: a payload
that carries no time leaves the payload's own column null, and a record without
a timestamp leaves `broker_time` null. Nothing substitutes the receipt time or
the plant's `time`.

A mapping does not build these. It returns the payload's columns, and
`.qetl.kafka.propagate` ([`kafka_ingest.q`](../../src/etl/core/kafka_ingest.q))
appends the four from the record the row came from and puts the table's columns
in order. Every mapping returns a leading `message_index` (`long`): which record
of the batch each row came from. Provenance follows it, never the row's
position, so a mapping that sorts, groups or drops a duplicate still gives each
row its own record's coordinates. A one-to-one mapping must return exactly one
row per record, `message_index` naming each once, in any order (anything else is
dead-lettered `mapping`). The columns a mapping returns are
`.qetl.kafka.topic.payload_schema[table;expand]`, `message_index` first.

**One record, many rows.** Declare the topic with `expand` (1b) and give the
table a `row_index` (`long`) column. The mapping's `message_index` is then
non-decreasing (a record's rows together) and the framework sets `row_index`, 0
for a record's first row. The index depends only on the mapping's output for
that record, so a retry, a replay or another batching gives the same one.
`(topic, partition, offset, row_index)` is unique. Deduplication still keys on
the record's coordinates: a record's rows are published in one call, so a
redelivered record is dropped whole, and a restart reads the handled records
back from the published rows. A record that expanded to no rows leaves nothing
to read back, and is decoded again to nothing. One-to-one tables do not carry
`row_index`.

**Keys and headers** stay on the raw envelope (`msg_key`) and are not copied
into decoded tables: join on the coordinates. **Headers are not carried:** the
consumer does not read them and nothing in this tree uses one, and a column
added to `kafka_message` or `kafka_dead_letter` is a live-table migration for no
current reader. If a topic needs one, add a typed payload column from the
payload, or open an issue to carry headers on the envelope.

**Migrating an existing table.** The contract added one column to a table that
existed: `crypto_book_snapshot` gained `broker_time` (before `topic`). A running
RDB replays the day's tickerplant log into the new schema and fails with
`length`, as for any column change mid-day. Roll the day, or set the old segment
of that table's log aside (the recipe is in [streaming cryptorust's
books](../guides/cryptorust-kafka-books.md#when-it-breaks)), then restart the
decoder. Rows already in a persisted HDB partition lack the column; queries
across the change see it null before. A table you own that lacks one of the four
columns, or a mapping returning provenance columns itself, is refused at load:
add the column to the table and drop it from the mapping.

## A record is never silently dropped

`kafka_flow1` sends a record it cannot map to `kafka_dead_letter`, with its
coordinates, key and payload as received:

  | `reason`        | when                                                                                                               |
  | ---             | ---                                                                                                                |
  | `no_mapping`    | no decoder job owns the topic (`kafka_flow1` dead-letters these)                                                   |
  | `empty_payload` | the record had no payload (a tombstone, or a producer bug)                                                         |
  | `malformed`     | not JSON, not an object, a declared field absent, null or mistyped, or a record with no topic, partition or offset |
  | `mapping`       | decoded, but the mapping refused the row (a non-positive quantity)                                                 |

One refused row does not take its batch with it. The count by `reason` is the
health of a topic.

```
uqs query "select n:count i by topic, reason from kafka_dead_letter" --port 6052
```

## Adding topics from a sidecar

A [sidecar bundle](../guides/sidecar-bundles.md) adds topics without editing
this tree by declaring a decoder job of its own.
[`sidecars/kafka_example`](../../sidecars/kafka_example/example_quotes_decoder.q)
is the generic worked one. The job file holds the topic's mapping (a
`.qetl.transform`), the `.qetl.kafka.topic.define` with `job` set to the job,
and the job itself, with its own `procname`, a literal `publishes` and a literal
branch in `publish_decoded` for each output table. The table goes in the
bundle's `tables.q` and must carry the provenance columns below. The machinery ---
marks, deduplication, dead letters, restart replay --- is
[`.qetl.kafka.decoder`](../../src/etl/core/kafka_decoder.q), keyed by the job's
name, and the job's state lives in its own namespace, so
`.qetl.job.stream.reset` restores it.

**Who decodes a record.** A topic is owned by exactly one job, the one whose
file declares it. A decoder handles only the records of topics it owns: it
marks, publishes or dead-letters them, and ignores the rest, so two decoders
never both handle or both dead-letter one record. Two jobs declaring one topic
is refused when the load plan is generated (and in a process that loads both).
`kafka_flow` is the catch-all: besides its own topic it takes every topic *no*
job owns and dead-letters it as `no_mapping`. The load plan
(`src/etl/generated/load_plan.q`) lists every owned topic of the tree and its
installed bundles, which is how `kafka_flow1` knows to leave a sidecar's topics
to the sidecar. The consumer subscribes to those topics too. If `kafka_flow1` is
not running, a record for an unowned topic is kept raw on `kafka_message` and
nothing reports it; a decoder started later catches up from the log.

## cryptorust book snapshots

cryptorust's `kafka-market-data-recorder` publishes sampled full books to
`cryptorust.book_snapshots` (key `venue:symbol`). `crypto_book_decoder1`
([`crypto_book_decoder.q`](../../src/etl/streaming/crypto_book_decoder.q),
mapping [`kafka_crypto_book.q`](../../src/etl/transforms/kafka_crypto_book.q))
decodes them into `crypto_book_snapshot`. Subscribe with a consumer and the
decoder side by side (a walkthrough: [streaming cryptorust's
books](../guides/cryptorust-kafka-books.md)):

```
UQF_KAFKA_TOPICS=cryptorust.book_snapshots uqs start kafka_feed1
uqs start crypto_book_decoder1
```

A record is JSON with `schema_version: 1`, `kind: "book_snapshot"`, three
epoch-millisecond clocks, `venue`, `symbol` and `bids`/`asks` as `[price, size]`
pairs, best first. The table has `sym`, `venue`, `source_time` (exchange),
`local_time`, `sampled_time`, `bid_px0..4`, `bid_sz0..4`, `ask_px0..4`,
`ask_sz0..4` and the record's `broker_time`, `topic`, `partition`, `offset`. A
side with fewer than five levels, or none, leaves null floats; levels past five
are cut. Not `crypto_book`, the kdb recorder's vector-shaped book. An unknown
`schema_version` or `kind`, a level that is not a pair, a missing field or a
payload that is not JSON goes to `kafka_dead_letter`.

The decoder's typed fields cover this record: `F` is a JSON array of numbers and
a blank (`" "`) an array of arrays of numbers, so a mapping reads
`[[price, size], ...]` without parsing JSON itself.

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
would call the gap done. A record is identified by its coordinates *and* its
`broker_time`, because a partition whose offsets restart (below) reuses them.

**A partition whose offsets restart** (a truncated log, a recreated topic) is
recognised by the feed and by every decoder with the same rule
(`.qetl.kafka.restarted`): a partition whose polled rows all sit below its mark
has restarted, so its mark is reset, a warning is logged, and the new records
are published and decoded. A redelivery that reaches the mark is a plain
redelivery and is dropped. The cost of the rule is that a redelivery *wholly*
below the mark is taken for a restart and decoded again; after a decoder restart
the replay marks such a partition by the top of its last run of offsets.

## Limits worth knowing

- A topic maps to one output table. Routing several event types from one topic
  to several tables is a different declaration, not yet written.
- A publish that throws inside `kafka_flow1` leaves its marks where they were
  and its batch unhandled; the rows are in the log, so the next restart's replay
  handles them.
- Only the current day's log is replayed, as for every replaying job.
