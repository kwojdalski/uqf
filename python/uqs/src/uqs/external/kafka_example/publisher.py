"""Publish a batch to the broker and report where every record landed.

`acks=all` and a flush mean the broker acknowledged each record - with one
broker, that it is in the leader's log as the broker's flush policy treats it.
That is NOT an fsync and NOT a host-crash guarantee: Kafka flushes to disk on
its own schedule, so a power loss can lose an acknowledged record. The example
needs only the acknowledgement; the docs say plainly what it is not.
"""

from __future__ import annotations

from dataclasses import dataclass

from uqs.external.kafka_example.batch import Record
from uqs.external.kafka_example.broker import Broker
from uqs.external.kafka_example.kclient import client_config
from uqs.paths import UqsError


@dataclass(frozen=True)
class Delivered:
    """A record and the coordinates Kafka gave it."""

    record: Record
    partition: int
    offset: int


def publish(
    broker: Broker, records: list[Record], *, identity: str | None = None, timeout: float = 30.0
) -> list[Delivered]:
    """Send `records` and return each one's delivered (partition, offset), in send order.

    Raises, naming the record and the broker's error, when any is not acknowledged
    within `timeout`: a verifier must not build an expectation from a record that
    may not exist.
    """
    from confluent_kafka import Producer  # ty: ignore[unresolved-import]

    producer = Producer(
        client_config(broker, identity, **{"acks": "all", "enable.idempotence": True})
    )
    results: list[Delivered | str | None] = [None] * len(records)

    def report(i: int):
        def cb(err, msg) -> None:
            results[i] = (
                f"{records[i].topic}[{records[i].partition}] {records[i].key}: {err}"
                if err
                else Delivered(records[i], msg.partition(), msg.offset())
            )

        return cb

    for i, rec in enumerate(records):
        producer.produce(
            rec.topic, value=rec.value, key=rec.key, partition=rec.partition, on_delivery=report(i)
        )
    left = producer.flush(timeout)
    failed = [r for r in results if isinstance(r, str)]
    if left or failed or any(r is None for r in results):
        raise UqsError(
            f"{left} record(s) unacknowledged within {timeout:.0f}s"
            + (f"; first error: {failed[0]}" if failed else "")
        )
    return [r for r in results if isinstance(r, Delivered)]
