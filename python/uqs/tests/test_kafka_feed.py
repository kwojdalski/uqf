"""Tests for the Kafka consumer.

The consumer's only real logic is turning Kafka messages into columns the
tickerplant will accept, and every way that can go wrong is silent:

  - a column in the wrong ORDER transposes two same-typed fields - `partition`
    and `offset` are both longs and sit next to each other, so swapping them
    produces a plausible dedupe that is wrong on every record;
  - a bare atom instead of a list makes `.u.upd` derive the wrong row count;
  - a `time` column makes every message one column too wide;
  - taking `partition`/`offset` from the PAYLOAD rather than the broker lets a
    producer decide what the dedupe believes.

None of those needs a broker, a network or a tickerplant to test, which is
exactly why `rows_from_messages` was separated from the socket. The poll loop
itself is not tested here: it is plumbing around a third-party client. What
the loop gets RIGHT - publish, then commit - is asserted by the q half, in
`tests/q/test_kafka_flow.q`, which proves the duplicates that ordering admits
are the ones it removes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from uqs.external.kafka_streamer import (
    KAFKA_RAW_TABLE,
    contract_fields,
    rows_from_messages,
)

REPO = Path(__file__).resolve().parents[3]


@dataclass
class FakeMessage:
    """Only the four accessors `rows_from_messages` uses.

    Methods rather than attributes because that is confluent-kafka's own
    shape: `msg.partition()`, not `msg.partition`. A fake with attributes
    would pass while the real thing returned a bound method.
    """

    _value: bytes
    _partition: int
    _offset: int
    _ts_ms: int

    def value(self) -> bytes:
        return self._value

    def partition(self) -> int:
        return self._partition

    def offset(self) -> int:
        return self._offset

    def timestamp(self) -> tuple[int, int]:
        return (1, self._ts_ms)


def a_message(partition: int = 0, offset: int = 0, sym: str = "EURUSD") -> FakeMessage:
    payload = (
        f'{{"sym": "{sym}", "side": "buy", "qty": 1000000.0, "price": 1.1,'
        ' "client": "ACME", "trade_id": 7}'
    )
    return FakeMessage(payload.encode(), partition, offset, 1_758_790_800_000)


def test_the_field_order_comes_from_the_q_declaration() -> None:
    fields = contract_fields(str(REPO))
    assert fields[:4] == ["broker_time", "partition", "offset", "sym"]
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

    The dedupe in `kafka_flow` trusts these two numbers completely, so the one
    thing that must never happen is a producer setting them.
    """
    msg = FakeMessage(b'{"sym": "EURUSD", "partition": 41, "offset": 999, "qty": 1.0}', 3, 12, 1)
    cols = rows_from_messages([msg], contract_fields(str(REPO)))
    assert cols["partition"] == [3]
    assert cols["offset"] == [12]


def test_the_broker_clock_arrives_as_a_timestamp_not_a_millisecond_count() -> None:
    """A long in a timestamp column is either a throw or a date in 1970."""
    cols = rows_from_messages([a_message()], contract_fields(str(REPO)))
    broker_time = cols["broker_time"][0]
    assert broker_time.year == 2025
    assert broker_time.tzinfo is not None


def test_several_messages_keep_their_order() -> None:
    fields = contract_fields(str(REPO))
    msgs = [a_message(0, 5, "EURUSD"), a_message(1, 0, "GBPUSD"), a_message(0, 6, "USDJPY")]
    cols = rows_from_messages(msgs, fields)
    assert cols["offset"] == [5, 0, 6]
    assert cols["partition"] == [0, 1, 0]
    assert cols["sym"] == ["EURUSD", "GBPUSD", "USDJPY"]


def test_the_raw_table_is_the_one_the_q_job_subscribes_to() -> None:
    """The two halves name the same table, or the feed publishes into the void."""
    job = (REPO / "src" / "etl" / "streaming" / "kafka_flow.q").read_text()
    assert f"`{KAFKA_RAW_TABLE}" in job


def test_a_missing_field_in_the_payload_is_a_null_not_a_dropped_column() -> None:
    """Short rows are the failure `.u.upd` cannot see: it derives the row
    count from column length, so one short column silently truncates."""
    msg = FakeMessage(b'{"sym": "EURUSD"}', 0, 0, 1)
    fields = contract_fields(str(REPO))
    cols = rows_from_messages([msg], fields)
    assert cols["qty"] == [None]
    assert len({len(c) for c in cols.values()}) == 1, "every column the same length"


@pytest.mark.parametrize("missing", ["partition", "offset"])
def test_the_dedupe_coordinates_are_declared_on_the_plant_table(missing: str) -> None:
    """Both must survive onto the published table, not just the raw one.

    `client_flow` without them is a table nothing can trace back to a record,
    and a future restart-seeding fix would have nothing to read.
    """
    tables = (REPO / "scripts" / "processes" / "uqs_tables.q").read_text()
    line = next(line for line in tables.splitlines() if line.startswith("client_flow:([]"))
    assert f"{missing}:" in line
