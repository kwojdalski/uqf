"""Tests for the (legacy) Python Kafka consumer.

The consumer's only real logic is turning Kafka messages into columns the
tickerplant will accept, and every way that can go wrong is silent:

  - a column in the wrong ORDER transposes two same-typed fields - `partition`
    and `offset` are both longs and sit next to each other, so swapping them
    produces a plausible dedupe that is wrong on every record;
  - a bare atom instead of a list makes `.u.upd` derive the wrong row count;
  - a `time` column makes every message one column too wide;
  - taking `topic`/`partition`/`offset` from the PAYLOAD rather than the broker
    lets a producer decide what the dedupe believes;
  - DECODING here would make the topic's wire format a Python concern again
    and lose the record that does not decode - the payload goes through as
    received and q dead-letters what it cannot read.

None of those needs a broker, a network or a tickerplant to test, which is
exactly why `rows_from_messages` was separated from the socket. The poll loop
itself is not tested here: it is plumbing around a third-party client. What
the loop gets RIGHT - publish, then commit - is asserted by the q half, in
`tests/q/test_kafka_ingest.q` and `tests/q/test_kafka_flow.q`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from uqs.external.kafka_streamer import (
    KAFKA_RAW_TABLE,
    contract_fields,
    rows_from_messages,
)

REPO = Path(__file__).resolve().parents[3]


@dataclass
class FakeMessage:
    """Only the accessors `rows_from_messages` uses.

    Methods rather than attributes because that is confluent-kafka's own
    shape: `msg.partition()`, not `msg.partition`. A fake with attributes
    would pass while the real thing returned a bound method.
    """

    _value: bytes | None
    _partition: int
    _offset: int
    _ts_ms: int
    _topic: str = "uqf.client.flow"
    _key: bytes | None = None
    _ts_type: int = 1

    def value(self) -> bytes | None:
        return self._value

    def key(self) -> bytes | None:
        return self._key

    def topic(self) -> str:
        return self._topic

    def partition(self) -> int:
        return self._partition

    def offset(self) -> int:
        return self._offset

    def timestamp(self) -> tuple[int, int]:
        return (self._ts_type, self._ts_ms)


def a_message(
    partition: int = 0, offset: int = 0, payload: bytes = b'{"sym": "EURUSD"}'
) -> FakeMessage:
    return FakeMessage(payload, partition, offset, 1_758_790_800_000)


def test_the_field_order_comes_from_the_q_declaration() -> None:
    fields = contract_fields(str(REPO))
    assert fields == ["topic", "partition", "offset", "msg_key", "broker_time", "payload"]
    assert "time" not in fields, "the plant stamps time; a publisher must not send one"


def test_every_column_is_a_list_even_for_one_message() -> None:
    fields = contract_fields(str(REPO))
    cols = rows_from_messages([a_message()], fields)
    assert set(cols) == set(fields)
    for name, col in cols.items():
        assert isinstance(col, list), f"{name} must be a list, not an atom"
        assert len(col) == 1


def test_the_coordinates_come_from_the_broker_not_the_payload() -> None:
    """A payload claiming to be offset 999 does not get to say so.

    The dedupe in `kafka_flow` trusts these three values completely, so the one
    thing that must never happen is a producer setting them.
    """
    msg = FakeMessage(b'{"topic": "evil", "partition": 41, "offset": 999}', 3, 12, 1)
    cols = rows_from_messages([msg], contract_fields(str(REPO)))
    assert cols["topic"] == ["uqf.client.flow"]
    assert cols["partition"] == [3]
    assert cols["offset"] == [12]


def test_the_payload_and_key_go_through_as_received() -> None:
    """Undecoded: invalid UTF-8 and non-JSON are q's to dead-letter, not ours to repair."""
    raw = b"\xff\x00not json"
    msg = FakeMessage(raw, 0, 0, 1, _key=b"k1")
    cols = rows_from_messages([msg], contract_fields(str(REPO)))
    assert cols["payload"] == [raw]
    assert cols["msg_key"] == [b"k1"]


def test_a_tombstone_is_an_empty_payload_not_a_dropped_message() -> None:
    cols = rows_from_messages([FakeMessage(None, 0, 4, 1)], contract_fields(str(REPO)))
    assert cols["payload"] == [b""]
    assert cols["msg_key"] == [b""]
    assert cols["offset"] == [4]


def test_the_broker_clock_arrives_as_a_timestamp_not_a_millisecond_count() -> None:
    """A long in a timestamp column is either a throw or a date in 1970."""
    cols = rows_from_messages([a_message()], contract_fields(str(REPO)))
    broker_time = cols["broker_time"][0]
    assert broker_time.year == 2025
    assert broker_time.tzinfo is not None


def test_a_record_with_no_broker_timestamp_has_a_null_not_1970() -> None:
    msg = FakeMessage(b"{}", 0, 0, -1, _ts_type=0)
    cols = rows_from_messages([msg], contract_fields(str(REPO)))
    assert cols["broker_time"] == [None]


def test_several_messages_keep_their_order() -> None:
    fields = contract_fields(str(REPO))
    msgs = [a_message(0, 5), a_message(1, 0), a_message(0, 6)]
    cols = rows_from_messages(msgs, fields)
    assert cols["offset"] == [5, 0, 6]
    assert cols["partition"] == [0, 1, 0]
    assert len({len(c) for c in cols.values()}) == 1, "every column the same length"


def test_the_raw_table_is_the_one_the_q_job_subscribes_to() -> None:
    """The two halves name the same table, or the feed publishes into the void."""
    job = (REPO / "src" / "etl" / "streaming" / "kafka_flow.q").read_text()
    assert f"`{KAFKA_RAW_TABLE}" in job
