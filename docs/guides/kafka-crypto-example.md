# Kafka to kdb+: a crypto example you can run and verify

Synthetic BTC and ETH trades go through a **real Kafka broker**, a consumer, the
tickerplant and a decoder job into a declared table in the **real RDB**, and a
verifier queries that RDB and says PASS or FAIL for every stage. It covers the
parts that go wrong in practice: which address a client connects to, what a
permission denial looks like, what a restart and a redelivery do. No exchange
account, no credentials and no production broker are involved.

The transport, mapping and recovery code is the [Kafka client
flow](../services/kafka.md); this guide adds a broker you can throw away, a
publisher with known data, and the checks.

## What is in it

  | piece                                                                                                                                                                                                                                    | what it is                                                                                                     |
  | ---                                                                                                                                                                                                                                      | ---                                                                                                            |
  | [`sidecars/kafka_crypto_example`](../../sidecars/kafka_crypto_example/)                                                                                                                                                                  | the decoder job `cryptotrades1`, its two topics, mapping and the table `crypto_trade`                          |
  | [`uqs.external.kafka_example.broker`](../../python/uqs/src/uqs/external/kafka_example/broker.py)                                                                                                                                         | a disposable Apache Kafka (KRaft, one node) in a scratch directory, with explicit advertised listeners         |
  | [`...kafka_example.batch`](../../python/uqs/src/uqs/external/kafka_example/batch.py)                                                                                                                                                     | the deterministic batch: 24 valid trades and 5 deliberately invalid records                                    |
  | [`...kafka_example.verify`](../../python/uqs/src/uqs/external/kafka_example/verify.py), [`phases`](../../python/uqs/src/uqs/external/kafka_example/phases.py), [`refusals`](../../python/uqs/src/uqs/external/kafka_example/refusals.py) | the staged verifier                                                                                            |

**Why a bundle and not a job in the tree.** `crypto_trade` exists to be
demonstrated; the tree's plant tables are the production schema and should not
grow a table for a demo. A [sidecar bundle](sidecar-bundles.md) adds a table, a
mapping, topics and a job without editing the tree, and `sidecars/kafka_example`
is the precedent. Everything it does is declared the way a production decoder is
(`.qetl.kafka.topic.define` with `job` set, a `.qetl.transform` mapping verified
by the test suite, a literal `publishes`), so lifting it into the tree is a file
move.

## The data

Each record is one JSON object, all fields required:

```json
{"sym":"BTC-USD","trade_id":"BTC-000007","ts":"2026-10-20T09:00:01.750Z","price":65003.5,"qty":0.25,"side":"sell"}
```

`crypto_trade` has the plant's `time`, then `event_time` (the trade's own
clock), `broker_time`, `sym`, `trade_id`, `price`, `qty`, `side` and the
record's `topic`, `partition` and `offset`. The batch publishes to two topics,
`example.crypto.trades` (BTC-USD to partition 0, ETH-USD to partition 1) and
`example.crypto.trades.b`, so **the same offset exists on several
topic-partitions**: a decoder that keyed its deduplication on the offset alone
would drop most of it. The key is `(topic;partition;offset)`.

`trade_id` is a string on the wire and never a number: JSON numbers are floats
in q, so a long id past 2^53 would be silently rounded. A numeric id is
dead-lettered. The decoder's typed fields have no string type, so the id lands
as a symbol with its case kept; a production table of millions of distinct ids
would want a string column and a decoder type to match.

The five invalid records, and what each must become on `kafka_dead_letter`:

  | record                   | `reason`        |
  | ------------------------ | --------------- |
  | not JSON                 | `malformed`     |
  | no payload (a tombstone) | `empty_payload` |
  | `price` missing          | `malformed`     |
  | `trade_id` a number      | `malformed`     |
  | `qty` negative           | `mapping`       |

## Run it

The consumer is either of the two that publish the same `kafka_message` envelope
(they differ only in what they need): **python** (`confluent-kafka`, runs
anywhere, including on PeachQ) or **native** (`kafka_feed1`, KX's kfk library,
needs a kdb+/KDB-X host - [building it](../services/kafka.md)). The verifier
takes `--feed python|native`.

Java 17 or newer and `uv pip install confluent-kafka` (not a dependency of this
project: nothing in the default test run needs a broker).

```bash
# 1. a disposable broker; the first run downloads Kafka 4.3.1 and checks its SHA-512
uv run python -m uqs.external.kafka_example broker start --decoy
#   directory   /tmp/uqf-kafka-example-xxxx
#   bootstrap   127.0.0.1:50199
#   advertised  127.0.0.1:50199   (what the broker tells clients to connect to next)
#   decoy       127.0.0.1:47127   (its metadata advertises broker-unresolvable.invalid)
#   topics      example.crypto.trades, example.crypto.trades.b
DIR=/tmp/uqf-kafka-example-xxxx

# 2. the stack, as run for this guide (PeachQ; the bundle's job runs wherever the tree does)
uqs --runtime peachq-etl runtime prepare --bundle sidecars/kafka_crypto_example
uqs --runtime peachq-etl start --profile capture
uqs --runtime peachq-etl start cryptotrades1
uqs --runtime peachq-etl summary       # the stp1 and rdb1 ports (6550 and 6552 here)
#   on a KDB-X host: uqs job install sidecars/kafka_crypto_example --yes, then
#   uqs start --profile essential and uqs start cryptotrades1

# 3. publish, consume, and verify every stage against the real RDB
uv run python -m uqs.external.kafka_example verify all --dir $DIR \
    --rdb-port <rdb1 port> --stp-port <stp1 port> --feed python --stop-feed
#   (native: --feed native --uqs "uqs" with UQF_KFK_HOME set)

# 4. clean up: stops only the broker this directory's pid file names, removes only this directory
uv run python -m uqs.external.kafka_example broker stop --dir $DIR
uqs --runtime peachq-etl stop cryptotrades1; uqs --runtime peachq-etl stop
```

`verify` prints one line per stage and exits non-zero unless all pass. Every
wait has a deadline (`--timeout`, default 60 s); after the first FAIL the stages
that depend on it print SKIP, so one broken stage is one line of cause:

```
== basic
PASS  library                  confluent-kafka 2 on librdkafka 2.16.0
PASS  bootstrap                127.0.0.1:50199 answered: 1 broker(s), topics example.crypto.trades, example.crypto.trades.b
PASS  advertised               advertised 127.0.0.1:50199 resolve and accept connections (bootstrap was 127.0.0.1:50199)
PASS  rdb                      RDB :6552 answers; crypto_trade has 0 rows and the declared types
PASS  fresh                    topics are empty and the RDB tables hold nothing
PASS  publish                  29 records acknowledged by the broker (24 valid, 5 invalid) across 3 topic-partitions; an acknowledgement is not an fsync
PASS  feed_started             python feed started in group uqf-example-g1
PASS  group_joined             group uqf-example-g1 is STABLE with 1 member (membership only: reading is proved below)
PASS  rows_visible             24 rows match the delivered coordinates, values and event times
PASS  dead_letters             5 dead letters with the expected coordinates and reasons (empty_payload, malformed, mapping)
PASS  raw_envelopes            kafka_message holds all 29 records (29 deliveries) with their payloads as sent
PASS  committed                committed == end on example.crypto.trades[0]=15, example.crypto.trades[1]=10, example.crypto.trades.b[0]=4
PASS  offset_identity          24 rows, no duplicate coordinate; 7 offsets occur on several topic-partitions (offset 3 on 3 of them) and none was dropped as a repeat
PASS: 13 passed, 0 failed, 0 skipped
```

The expectation is built from the coordinates Kafka reported at publish time,
not from a prediction, and compared with what the RDB holds column by column,
with its `meta` types. By hand:

```
uqs query "select from crypto_trade where partition=1, offset<3" --port <rdb1 port>
uqs query "select n:count i by topic, reason from kafka_dead_letter" --port <rdb1 port>
```

### The other phases

  | `verify ...`  | what it shows                                                                                                                                                                         |
  | ---           | ---                                                                                                                                                                                   |
  | `restart`     | stop the feed, publish six more, start it again: `kafka_message` grows by exactly six, not by the topic - **a committed offset beats `earliest`**                                     |
  | `redelivery`  | a consumer publishes to the tickerplant and dies **before committing**: its successor is redelivered the same six, `kafka_message` holds each twice, `crypto_trade` once              |
  | `groups`      | two fresh groups each read all records at once; each commits the end; the feed's group's offsets do not move                                                                          |
  | `denied`      | on a `--secure` broker: a group with no group permission, and one with no topic permission, each get their own diagnosis                                                              |
  | `advertised`  | bootstrap through the decoy listener: the metadata advertises a name that cannot resolve; one report, however many times the client repeats it                                        |

`restart` and `redelivery` need the stack; `groups`, `denied` and `advertised`
need only a broker (`denied` needs `broker start --secure`, then
`publish --dir $DIR --identity admin`, then
`verify denied --dir $DIR --identity reader`).

## Connectivity: bootstrap is not advertised

A client dials the **bootstrap** list once, to ask for metadata. Every later
connection - the group coordinator, each partition leader - goes to the
addresses the *broker* puts in that metadata, its **advertised listeners**. They
are different settings, and they fail differently:

  | symptom                                                              | stage         | meaning                                                                                  |
  | ---                                                                  | ---           | ---                                                                                      |
  | cannot connect to the bootstrap address                              | `bootstrap`   | wrong host or port, firewall, tunnel down, wrong security protocol                       |
  | bootstrap answers, then `Failed to resolve 'host:port'` on node `1`  | `advertised`  | the broker told us to use a name this host cannot resolve, or a port nothing forwards    |
  | `Group authorization failed` (`GROUP_AUTHORIZATION_FAILED`)          | `group`       | the principal may not use this consumer group                                            |
  | `Topic authorization failed` (`TOPIC_AUTHORIZATION_FAILED`)          | `consume`     | the principal may not read the topic                                                     |
  | group stable, topic readable, nothing in the RDB                     | later stages  | see the stages below                                                                     |

A TCP connection, or a log line saying *subscribed*, proves none of this. The
feed does not claim readiness on either: `kafka_feed1` logs one `readiness`
line, re-logged only when it changes, with each stage `ok`, `pending` or
`failed`:

```
library=ok bootstrap=ok advertised=ok group=ok consume=ok published=ok committed=ok
```

A stage turns `ok` only on proof: an **assignment** proves bootstrap, advertised
and group (metadata came back, the coordinator answered, partitions were handed
out); a **polled record** proves consume; the tickerplant's acknowledgement
proves published; a **commit** proves committed. A group *description* is not on
the list on purpose: a principal can describe a group it cannot consume with,
and `verify denied` demonstrates exactly that.

### An unresolvable advertised hostname is reported once

`librdkafka` retries a broker it cannot reach every second or so, and reports
each retry through the error callback. Logging each was one `WARNING` a second,
for as long as the problem lasted, burying the line that names the cause. The
callback now classifies the error
([`kafka_diagnostics.q`](../../src/etl/core/kafka_diagnostics.q)) and logs a
*cause* - stage, endpoint and code - the first time it sees it:

```
ERROR|kafka|advertised stage failed: the broker's metadata sends clients to broker-unresolvable.invalid:43687, which this host cannot reach - the broker's advertised.listeners must name an address reachable from here (or the name must resolve, and the port be forwarded, through a tunnel) stage=`advertised kind=`connect endpoint="broker-unresolvable.invalid:43687" code=-193i error="broker-unresolvable.invalid:43687/1: Failed to resolve 'broker-unresolvable.invalid:43687': Name or service not known (after 1ms in state CONNECT)"
```

Repeats are counted, not logged; the `all brokers are down` that follows is the
same cause, not a second one; and when the stage recovers one `recovered` line
says how many repeats were suppressed, so a *later* failure reports afresh. The
verifier's `advertised` phase reproduces it against a real broker: 236 client
errors in 12 seconds, one report.

The text after `Failed to resolve` is the resolver's
(`No address associated with hostname`, `Name or service not known`): it is the
C library's, so it differs by host.

## SSH tunnels

Direct is the default. When the broker is only reachable over SSH, forward the
broker's port to the **same number** locally and make the broker advertise
`localhost`:

```bash
ssh -N -L 9092:127.0.0.1:9092 you@jump-host        # one -L per broker advertised
UQF_KAFKA_BROKERS=localhost:9092 ...               # bootstrap through the tunnel
```

with `advertised.listeners=PLAINTEXT://localhost:9092` on the broker (the
disposable one: `broker start --advertised-host localhost`). If the broker
advertises its real internal name instead, bootstrap works through the tunnel
and then **every data connection fails to resolve** - the `advertised` stage
above. For several brokers, each advertised `host:port` needs its own forward,
and each host name must resolve to this machine (`/etc/hosts`).

SSH authenticates you to the jump host. It says nothing about Kafka: the broker
still authenticates the client (SASL or TLS) and then authorises it, topic by
topic and group by group, and a tunnel makes the client look like the jump
host's network, not like a principal. A tunnel that is up is not a permission.

This variant needs an SSH server and was not run here; the broker side of it
(`--advertised-host localhost`) is the same code path the direct run uses.

## Groups

The committed offsets belong to the **consumer group**, so:

- give every independent reader **its own group** (`uqf-example-g1`, `-g2`,
  `-g3` here). Two members of one group *split* the partitions, so each sees
  part of the topic; two groups each see all of it.
- **never reuse an existing application's group.** Joining it triggers a
  rebalance that takes partitions from the real consumer, and committing under
  it moves *its* offsets. The example's groups are disposable, and the default
  feed group is `uqf-kafka-q`, not anything a pilot uses.
- run one feed per group.

### `earliest`, `latest` and committed offsets

`auto.offset.reset` (`UQF_KAFKA_OFFSET_RESET`, default `earliest`) applies
**only to a group with no committed offset for a partition**. Once the group has
committed, the broker resumes from that offset and the setting is ignored: this
is what `verify restart` demonstrates, with `earliest` set and only the new
records read. `latest` for a new group means the example's backlog is skipped,
and the verifier's `basic` stage would FAIL on `rows_visible`, which is the
point of having it.

## Permissions

What the identities of `broker start --secure` may do, and what the broker
answers when they may not (SASL/PLAIN, an authorizer that denies by default,
fresh random passwords in a `0600` file that is never printed):

  | identity  | topics                 | groups     | result                                                                      |
  | ---       | ---                    | ---        | ---                                                                         |
  | `reader`  | Read, Describe         | Read       | consumes (`groups` phase, two groups at once)                               |
  | `nogroup` | Read, Describe         | none       | `GROUP_AUTHORIZATION_FAILED`, at the `group` stage                          |
  | `notopic` | none                   | Read       | `TOPIC_AUTHORIZATION_FAILED`, at the `consume` stage                        |

```
PASS  denied_nogroup           nogroup refused at the group stage - FindCoordinator response error: Group authorization failed.
PASS  denied_notopic           notopic refused at the consume stage - Subscribed topic not available: example.crypto.trades: Broker: Topic authorization failed
PASS  describe_is_not_consume  notopic can DESCRIBE group uqf-example-g1 (state DEAD) yet cannot read the topic (topic_auth): a group-description permission is not proof of consume permission
```

A consumer needs **Read on the topic**, **Describe on the topic** (metadata) and
**Read on the group** (join and commit). With `kafka-acls.sh`:

```
kafka-acls.sh --bootstrap-server B --command-config admin.properties --add \
  --allow-principal User:svc --operation Read --operation Describe --topic example.crypto.trades \
  --group uqf-example-g1 --operation Read
```

The feed takes the password from the variable `UQF_KAFKA_SASL_PASSWORD_ENV`
*names*, so it is never in a file or a process table; the verifier reads the
disposable file and prints neither the file's content nor a password.

## Restarts, redelivery and what "published" means

The consumer **publishes to the tickerplant, then commits** ([why that
order](../services/kafka.md)). A crash between the two redelivers, and the
decoder's `(topic;partition;offset)` marks absorb the repeat: `kafka_message`
records every delivery as an audit trail, `crypto_trade` records each trade
once. `verify redelivery` makes the crash explicit - a consumer that publishes
and exits without committing - rather than racing a `kill`.

**The publisher's acknowledgement is not an fsync.** The example publishes with
`acks=all` and waits for every acknowledgement, which means the broker has the
record - and nothing more. Kafka flushes to disk on its own schedule, so a host
crash can lose an acknowledged record, and the tickerplant's acknowledgement to
the consumer is likewise "the update handler ran", not "the log is on disk" (see
[what the tickerplant's acknowledgement does and does not
promise](../services/kafka.md#what-the-tickerplants-acknowledgement-does-and-does-not-promise)).
What the ordering buys is the direction of the failure: a repeat is detectable
and absorbed; a loss would not be.

One more thing the example found. The segmented tickerplant traps an update that
throws and logs `Bad message received` - the sender's `sync` still returns. The
Python consumer used to send a dict, which the tickerplant's update chain
rejected for every record while the consumer committed each offset. It now sends
a list of columns in the contract's order (`kafka_streamer.publish_message`),
and `verify rows_visible` is what would have caught it: **a committed offset and
a returned publish are not rows in the RDB.**

## What ran where

The Python consumer path, the broker, the publisher, every phase above and the
unit tests were run against a real broker and a real PeachQ stack (`stp1`,
`rdb1`, `cryptotrades1`). The native feed (`kafka_feed1`) needs KX's `kfk`
library, which needs a kdb+/KDB-X host: its readiness and diagnostics are
unit-tested over a fake `kfk` and over text read from a real broker, and the
commands to run it for real are the ones above with `--feed native`.

## Cleaning up

`broker stop --dir DIR` signals only the pid its own pid file recorded (after
checking the process is still the one it started), SIGKILLs it only if it
ignores SIGTERM, and removes `DIR` only if it carries the marker file
`broker start` wrote. The feed is stopped with SIGTERM so it leaves its group.
Nothing is installed system-wide; the Kafka distribution is cached under
`~/.cache/uqs/kafka`.
