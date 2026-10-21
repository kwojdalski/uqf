# Streaming cryptorust's order books over Kafka

cryptorust's `kafka-market-data-recorder` publishes sampled full books to a
Kafka topic. `kafka_feed1` consumes it and `crypto_book_decoder1` decodes it
into `crypto_book_snapshot`. How the pieces work is on the [Kafka
page](../services/kafka.md#cryptorust-book-snapshots).

## One-time setup

```
brew install kafka && brew services start kafka        # localhost:9092
kafka-topics --create --topic cryptorust.book_snapshots --bootstrap-server localhost:9092
```

- The `kfk` v1.6.6 library in `UQF_KFK_HOME` (for example `~/.local/kfk`,
  holding `kfk.q`, `libkfk.so` and `librdkafka.1.dylib`). Build steps: [Kafka
  page](../services/kafka.md#the-q-feed-kafka_feed1).
- After a pull that changes `python/uqs/pyproject.toml`, reinstall the CLI.
  Without it, a command such as `--interactive` fails with "No module named
  textual".

```
uv tool install --editable ./python/uqs --reinstall
```

## Run

The variables must be set in the shell that runs `uqs start`. They are listed in
[`environment.md`](../reference/environment.md).

```
export UQF_KFK_HOME=$HOME/.local/kfk
export UQF_KAFKA_BROKERS=localhost:9092
export UQF_KAFKA_TOPICS=cryptorust.book_snapshots
export UQF_KAFKA_GROUP=uqf-crypto-books          # its own group
export UQF_KAFKA_OFFSET_RESET=latest             # default: earliest

uqs start --profile essential
uqs start kafka_feed1
uqs start crypto_book_decoder1
```

In the cryptorust checkout:

```
cargo run --locked --features standalone,kafka --bin kafka-market-data-recorder -- \
  --brokers localhost:9092 --topic cryptorust.book_snapshots
```

## Check

```
uqs logs -f kafka_feed1          # readiness: every stage ok
uqs logs -f crypto_book_decoder1 # first rows published table=`crypto_book_snapshot
```

```
uqs query "select n:count i, last_time:max time by sym, venue from crypto_book_snapshot"
uqs query "select [-5] time, sym, venue, bid_px0, bid_sz0, ask_px0, ask_sz0 from crypto_book_snapshot" --proc rdb1
uqs query "select [-5] from kafka_dead_letter" --proc rdb1
```

`time` is UTC. The table has `sym`, `venue`, `source_time`, `local_time`,
`sampled_time`, `bid_px0..4`, `bid_sz0..4`, `ask_px0..4`, `ask_sz0..4`, `topic`,
`partition` and `offset` ([definition](../../src/etl/plant_tables.q)).

cryptorust sends a book only when it has changed, and re-sends every book after
it restarts. The re-sent books are new Kafka messages, so they are not
deduplicated (dedup is on topic, partition and offset).

## When it breaks

  | Symptom                                                                                                    | Cause                                                                         | Fix                                                                                                                                                                                                                                                                                                                                                              |
  | ---                                                                                                        | ---                                                                           | ---                                                                                                                                                                                                                                                                                                                                                              |
  | Feed or decoder `down`; err log "qtorq.assert_publishable: the tickerplant defines no table kafka_message" | The stack was started before the pull that added the table                    | Restart the stack                                                                                                                                                                                                                                                                                                                                                |
  | "bootstrap stage failed ... localhost:9092 ... Connection refused"                                         | No broker                                                                     | `brew services start kafka`                                                                                                                                                                                                                                                                                                                                      |
  | More than 16 processes                                                                                     | Past the licence connection cap                                               | Start only what you need                                                                                                                                                                                                                                                                                                                                         |
  | Query error "not valid servers: rdb"                                                                       | `rdb1` is down                                                                | Read `err_rdb1`. One cause is "could not replay the log file: length", after a pull changed a table's columns mid-day. Replace that table's old-segment tplog with an empty journal (`` `:path set () ``), keeping the original elsewhere, then `uqs start rdb1`. Do not just move it: the STP still lists it and the error becomes "No such file or directory". |
  | `nyi` from the HDB's port                                                                                  | The gateway also ran the query on the HDB, where `select [-n]` is unsupported | `--proc rdb1` or `--servers rdb`                                                                                                                                                                                                                                                                                                                                 |
  | `'assign` in a query                                                                                       | A q builtin such as `last` used as a column name                              | Rename the column                                                                                                                                                                                                                                                                                                                                                |
  | `hostname: illegal option -- I`                                                                            | Harmless on macOS (vendored `torq.sh`)                                        | None                                                                                                                                                                                                                                                                                                                                                             |
