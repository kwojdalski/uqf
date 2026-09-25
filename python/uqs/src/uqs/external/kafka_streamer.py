"""Hold a Kafka subscription and push its records at a tickerplant.

Run by `kafka_feed.start_kafka_feed`, not imported by the CLI: it is a
process, and its dependency (``confluent-kafka``) is optional - the rest of
the orchestrator must import without it, the same standing
``databento_streamer`` has next door.

## The commit ordering, which is the whole point of this example

A Kafka topic and a tickerplant log are the same idea - two ordered,
replayable logs - and this file is the seam between them. There are exactly
two places to put the offset commit relative to ``.u.upd``:

1. **Commit first.** At-most-once. A crash in the gap loses the record
   permanently and *undetectably*: the plant simply never sees a row that
   existed, and no downstream query can tell a lost trade from a trade that
   was never sent.
2. **Commit after the publish returns.** At-least-once. A crash in the gap
   replays the record and the plant sees it twice - but a duplicate is
   *detectable*, because the record carries coordinates unique in the topic.

This takes the second, on the rule that a recoverable failure beats an
undetectable one. ``enable.auto.commit`` is therefore off - left on, librdkafka
commits on its own timer and the ordering above is decided by a race - and
``consumer.commit`` runs synchronously only after ``q.sync`` has returned.

The other half of the bargain lives in q: ``src/etl/streaming/kafka_flow.q``
drops anything it has already seen, keyed on ``(partition;offset)``. Neither
half is correct alone. Commit-after without the dedupe is just duplicates.

## The three tickerplant rules

Transport concerns, and this is the transport (``torq_pipeline.q``):

1. ``.u.upd`` stamps its own ``time`` on receipt, so a publisher must not
   send one. The broker's clock travels as ``broker_time`` and survives as a
   column of its own - ``time`` says when we *heard*, not when it happened.
2. ``.u.upd`` derives the row count from column length, so every column is a
   list, never a bare atom, even for a single record.
3. Column ORDER must match the table, because ``.u.upd`` positions by index,
   not by name. The order is read from the q declaration at runtime rather
   than restated here, so a field added in q and forgotten here cannot
   silently transpose two columns of the same type.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from typing import Any

#: The plant's declaration of the raw table, read at runtime for rule 3.
#: `uqs_tables.q` rather than a `.qsrc` source contract, because this feed has
#: no ODBC backfill to share a contract with - replaying a topic from offset 0
#: is Kafka's own answer to backfill, and it needs a broker.
CONTRACT_Q = "scripts/processes/uqs_tables.q"

#: The raw table this pushes onto. `kafka_flow1` subscribes to it.
KAFKA_RAW_TABLE = "kafka_client_flow"


def contract_fields(repo_root: str) -> list[str]:
    """The raw table's column order, parsed out of its q declaration.

    Reading the q rather than duplicating the list is the discipline
    `databento_streamer.contract_fields` follows for the same reason: one
    place to change, so a mismatch is impossible rather than merely unlikely.

    `time` is dropped because the plant stamps it (rule 1).
    """
    path = os.path.join(repo_root, CONTRACT_Q)
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    # The `]` is part of the prefix: q's table literal opens `([]`, and a
    # prefix of `([` leaves the first field named "]time".
    prefix = f"{KAFKA_RAW_TABLE}:([]"
    line = next(line for line in text.splitlines() if line.startswith(prefix))
    body = line[len(prefix) : line.rindex(")")]
    fields = [chunk.split(":", 1)[0].strip() for chunk in body.split(";")]
    return [f for f in fields if f and f != "time"]


def rows_from_messages(messages: list[Any], fields: list[str]) -> dict[str, list]:
    """Kafka messages as one list per column, in the contract's order.

    Separate from everything that touches a socket, so the column order, the
    coordinate extraction and the one-list-per-column rule are testable with
    no broker and no tickerplant. That is the whole of the logic here; `main`
    is plumbing.

    The payload is JSON, one client trade per message. A real deployment
    would more likely carry Avro or protobuf against a schema registry -
    decoded HERE either way, so q only ever sees typed columns and never a
    format library.

    The coordinates do NOT come from the payload. `partition` and `offset`
    are the broker's, read off the message, and a producer cannot forge them
    into telling the dedupe that a replay is fresh.
    """
    out: dict[str, list] = {f: [] for f in fields}
    for msg in messages:
        payload = json.loads(msg.value())
        _, ts_ms = msg.timestamp()
        for f in fields:
            if f == "partition":
                out[f].append(msg.partition())
            elif f == "offset":
                out[f].append(msg.offset())
            elif f == "broker_time":
                # A datetime, not the raw millisecond int: the q column is a
                # timestamp, and handing .u.upd a long would either throw on
                # insert or - worse - land a millisecond count in a column
                # every reader will format as a date.
                out[f].append(datetime.fromtimestamp(ts_ms / 1000, tz=UTC))
            else:
                out[f].append(payload.get(f))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--credential", required=True, help="user:password for stp1")
    parser.add_argument("--brokers", required=True, help="bootstrap.servers")
    parser.add_argument("--topic", required=True)
    parser.add_argument("--group", required=True, help="consumer group id")
    parser.add_argument("--table", required=True)
    parser.add_argument("--repo-root", default=os.getcwd())
    args = parser.parse_args(argv)

    try:
        import kola
        from confluent_kafka import Consumer  # ty: ignore[unresolved-import]
    except ImportError as exc:  # pragma: no cover - depends on the extra
        print(
            f"missing dependency: {exc}. Install the live-feed extra:\n"
            "    uv pip install confluent-kafka kola",
            file=sys.stderr,
        )
        return 1

    fields = contract_fields(args.repo_root)
    user, _, password = args.credential.partition(":")
    # passwd, not password: kola.Q's own keyword.
    q = kola.Q(args.host, args.port, user=user, passwd=password)
    q.connect()

    consumer = Consumer(
        {
            "bootstrap.servers": args.brokers,
            "group.id": args.group,
            # Off, and the reason is the module docstring: with auto-commit on,
            # librdkafka commits on its own timer and whether a record was
            # published before its offset was committed becomes a race.
            "enable.auto.commit": False,
            # A restart with no committed offset starts at the OLDEST record
            # rather than the newest. Losing the backlog silently is the
            # failure this example is about; replaying it is the one q handles.
            "auto.offset.reset": "earliest",
        }
    )
    consumer.subscribe([args.topic])

    try:
        while True:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                print(f"kafka error: {msg.error()}", file=sys.stderr)
                continue
            cols = rows_from_messages([msg], fields)
            # PUBLISH, THEN COMMIT, and never the other way round. `sync`
            # rather than `asyn`: an asynchronous publish returns before the
            # plant has the row, which would make the commit below a lie.
            q.sync(".u.upd", args.table, cols)
            consumer.commit(msg, asynchronous=False)
    finally:
        consumer.close()
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
