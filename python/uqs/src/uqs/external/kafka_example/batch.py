"""The synthetic batch: deterministic, generic, and deliberately partly invalid.

Everything is derived from the record's position, with no clock and no random
source, so a run is reproducible and the verifier can state exactly which rows
and dead letters it expects. The coordinates are NOT predicted here: Kafka
assigns offsets, so the publisher reports where each record landed
(`publisher.Delivered`) and the expectation is built from that.

Partitions are explicit. BTC-USD goes to partition 0 and ETH-USD to partition 1
of the first topic, and the second topic's trades go to its partition 0, so the
same offset exists on several (topic, partition) pairs. A decoder that keyed its
deduplication on the offset alone would drop most of this batch; the batch is
built to catch that.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

TOPIC_A = "example.crypto.trades"
TOPIC_B = "example.crypto.trades.b"
TOPICS = (TOPIC_A, TOPIC_B)
PARTITIONS = 2

#: The trading clock of every synthetic trade: a fixed day, so a re-run is the same data.
EPOCH = datetime(2026, 10, 20, 9, 0, 0, tzinfo=UTC)

#: symbol -> (id prefix, opening price, price step, quantity)
MARKETS = {
    "BTC-USD": ("BTC", 65000.0, 0.5, 0.25),
    "ETH-USD": ("ETH", 3200.0, 0.25, 1.5),
}

#: The five invalid payloads and the dead-letter reason each earns. The same
#: strings are in sidecars/kafka_crypto_example/test_kafka_crypto_example.q, where
#: the decoder is shown to give these reasons without a broker.
INVALID: tuple[tuple[str, bytes | None, str], ...] = (
    ("not_json", b"not json at all", "malformed"),
    ("empty", None, "empty_payload"),
    (
        "missing_price",
        b'{"sym":"BTC-USD","trade_id":"BTC-X1","ts":"2026-10-20T09:00:00.000Z",'
        b'"qty":0.25,"side":"buy"}',
        "malformed",
    ),
    (
        "numeric_trade_id",
        b'{"sym":"BTC-USD","trade_id":1234567,"ts":"2026-10-20T09:00:00.000Z",'
        b'"price":65000.5,"qty":0.25,"side":"buy"}',
        "malformed",
    ),
    (
        "negative_quantity",
        b'{"sym":"BTC-USD","trade_id":"BTC-X2","ts":"2026-10-20T09:00:00.000Z",'
        b'"price":65000.5,"qty":-0.25,"side":"buy"}',
        "mapping",
    ),
)


@dataclass(frozen=True)
class Record:
    """One record to publish, and what the pipeline must do with it."""

    topic: str
    partition: int
    key: str
    value: bytes | None
    #: "row" for a trade that must reach crypto_trade, else its dead-letter reason.
    expect: str
    #: The trade's fields as the verifier will compare them; None for an invalid record.
    trade: dict | None = None


def trade(symbol: str, seq: int, tag: str = "") -> dict:
    """The seq-th trade of a symbol, as the payload's fields."""
    prefix, open_px, step, qty = MARKETS[symbol]
    when = EPOCH + timedelta(milliseconds=250 * seq)
    return {
        "sym": symbol,
        "trade_id": f"{prefix}-{tag}{seq:06d}",
        "ts": when.strftime("%Y-%m-%dT%H:%M:%S.") + f"{when.microsecond // 1000:03d}Z",
        "price": open_px + step * seq,
        "qty": qty,
        "side": "buy" if seq % 2 == 0 else "sell",
    }


def _valid(topic: str, partition: int, symbol: str, seq: int, tag: str = "") -> Record:
    fields = trade(symbol, seq, tag)
    return Record(topic, partition, symbol, json.dumps(fields).encode(), "row", fields)


def initial_batch() -> list[Record]:
    """The first batch: 24 valid trades and 5 invalid records.

    Topic A partition 0 holds ten BTC-USD trades with all five invalid records
    among them; partition 1 ten ETH-USD trades; topic B partition 0 four ETH-USD
    trades. The invalid records sit after trades 1, 2, 3, 5 and 7 of partition 0,
    so good neighbours surround every bad one.
    """
    inserts = {1: 0, 2: 1, 3: 2, 5: 3, 7: 4}
    records: list[Record] = []
    for seq in range(10):
        records.append(_valid(TOPIC_A, 0, "BTC-USD", seq))
        if seq in inserts:
            _, value, reason = INVALID[inserts[seq]]
            records.append(Record(TOPIC_A, 0, "BTC-USD", value, reason))
        records.append(_valid(TOPIC_A, 1, "ETH-USD", seq))
    records.extend(_valid(TOPIC_B, 0, "ETH-USD", seq, "B") for seq in range(4))
    return records


def extra_batch(tag: str, per_partition: int = 3) -> list[Record]:
    """A later batch of valid trades only, ids tagged so they never repeat the first's.

    `tag` is one or two characters; the restart and redelivery stages each use their own.
    """
    records: list[Record] = []
    for seq in range(100, 100 + per_partition):
        records.append(_valid(TOPIC_A, 0, "BTC-USD", seq, tag))
        records.append(_valid(TOPIC_A, 1, "ETH-USD", seq, tag))
    return records
