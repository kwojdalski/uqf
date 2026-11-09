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

import pytest

from uqs.external.kafka_streamer import (
    ACCEPT_Q,
    KAFKA_RAW_TABLE,
    contract_fields,
    publish_message,
    rows_from_messages,
)
from uqs.paths import UqsError

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
    """Undecoded: non-JSON is q's to dead-letter, not ours to repair."""
    raw = b"\x00not json \xc3\xa9"
    msg = FakeMessage(raw, 0, 0, 1, _key=b"k1")
    cols = rows_from_messages([msg], contract_fields(str(REPO)))
    assert cols["payload"] == [raw]
    assert cols["msg_key"] == [b"k1"]


def test_bytes_that_are_not_utf8_are_refused_not_repaired() -> None:
    """kola cannot carry them as received (below), so they are refused naming the
    record - never replaced by other bytes that would then decode."""
    msg = FakeMessage(b"\xff\x00not json", 0, 0, 1)
    with pytest.raises(UqsError, match=r"uqf\.client\.flow\[0\]@0: the payload is not valid UTF-8"):
        rows_from_messages([msg], contract_fields(str(REPO)))


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


class RecordingQ:
    """A stand-in for kola.Q that remembers what it was asked to run."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def sync(self, *args: object) -> None:
        self.calls.append(args)


def test_a_message_is_sent_as_a_list_of_columns_in_the_contracts_order() -> None:
    """#1492: a dict reached the segmented tickerplant as a q dictionary, its update
    chain's `(enlist time),x` answered `type`, and the consumer - whose `sync` still
    returned - committed the offset of a record the plant never stored."""
    fields = contract_fields(str(REPO))
    q = RecordingQ()
    publish_message(q, "kafka_message", a_message(), fields)
    ((func, table, payload),) = q.calls
    assert (func, table) == (ACCEPT_Q, "kafka_message")
    assert isinstance(payload, list) and not isinstance(payload, dict)
    assert len(payload) == len(fields)
    assert all(len(col) == 1 for col in payload)
    assert payload[fields.index("topic")] == ["uqf.client.flow"]
    by_name = rows_from_messages([a_message()], fields)
    assert [c[0] for c in payload] == [by_name[f][0] for f in fields]


def test_the_publish_runs_the_same_acceptance_check_as_qtorq_publish() -> None:
    """ACCEPT_Q is torq_pipeline.q's `accept_fn`, which `.qtorq.publish` sends (#2192):
    the same body, so the two consumers judge a refused update the same way."""
    import re

    src = (REPO / "scripts" / "processes" / "torq_pipeline.q").read_text()
    start = src.index("accept_fn:{[t;x]")
    body = src[start + len("accept_fn:") : src.index("\n    r}", start) + len("\n    r}")]
    assert re.sub(r"\s+", " ", body) == re.sub(r"\s+", " ", ACCEPT_Q)


# ------------------------------------------------- through kola into a real q
#
# The tests above stop at Python values. What reaches the plant is what kola
# makes of them, and two of the claims above - "invalid UTF-8 reaches q to be
# dead-lettered" and "no timestamp is a null" - were never true on the wire.
# These drive `publish_message` through a real kola handle into a real q whose
# `.u.upd` is TorQ's segmented tickerplant in its default error mode
# (lib/torq/code/processes/segmentedtickerplant.q, `errmode:1b`): a failing
# update is handed to `.stplg.badmsg` and the call RETURNS, which is how a
# refused record used to look like a stored one (#2192).

_PLANT_Q = """
.qetl.plant.nested:{[t;d]};
{PLANT_LINE}
kafka_message:.qetl.plant.kafka_message;
badmsgs:();
.stplg.badmsg:{[e;t;x] `badmsgs set badmsgs,enlist e};
.stp.upd:{[t;x] t insert (enlist(count first x)#.z.p),x};
.u.upd:{[t;x] .[.stp.upd;(t;x);{.stplg.badmsg[x;y;z]}[;t;x]]};
hex:{$[count x;raze string "x"$x;""]};
stored:{[] .j.j select topic:string topic, partition, offset,
    msg_key:hex each msg_key, broker_time:"j"$broker_time, payload:hex each payload,
    payload_is_string:{10h=type x} each payload from kafka_message};
"""


def _plant_line() -> str:
    """kafka_message's declaration, as plant_tables.q writes it - not a copy."""
    src = (REPO / "src" / "etl" / "plant_tables.q").read_text().splitlines()
    (line,) = [s for s in src if s.startswith(".qetl.plant.kafka_message:")]
    return line


@pytest.fixture
def plant(tmp_path, start_q):
    import kola

    boot = tmp_path / "plant.q"
    boot.write_text(_PLANT_Q.replace("{PLANT_LINE}", _plant_line()))
    with start_q(str(boot)) as port:
        conn = kola.Q("127.0.0.1", port, user="", passwd="")
        conn.connect()
        try:
            yield conn
        finally:
            conn.disconnect()


def _stored(conn) -> list[dict]:
    import json

    return json.loads(conn.sync("stored", None))


Q_EPOCH_MS = 946_684_800_000  # 2000.01.01 in Unix milliseconds


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(b'{"sym": "EURUSD", "px": 1.5}', id="json"),
        pytest.param(b"x", id="one-character"),
        pytest.param("café €".encode(), id="multibyte-utf8"),
        pytest.param(None, id="tombstone"),
    ],
)
def test_a_record_reaches_the_plant_byte_for_byte_with_its_coordinates(plant, payload) -> None:
    """Kafka record -> kola -> `.u.upd` -> kafka_message, and back out as bytes."""
    fields = contract_fields(str(REPO))
    ts_ms = 1_760_950_801_750
    msg = FakeMessage(payload, 1, 7, ts_ms, _topic="example.crypto.trades", _key=b"BTC-USD")
    publish_message(plant, KAFKA_RAW_TABLE, msg, fields)
    (row,) = _stored(plant)
    assert (row["topic"], row["partition"], row["offset"]) == ("example.crypto.trades", 1, 7)
    assert bytes.fromhex(row["msg_key"]) == b"BTC-USD"
    assert bytes.fromhex(row["payload"]) == (payload or b"")
    assert row["payload_is_string"], "a payload is a string even when it is one character"
    assert row["broker_time"] == (ts_ms - Q_EPOCH_MS) * 1_000_000, "the record's ms, exactly"
    assert plant.sync("exec t from meta kafka_message") == "psjjCpC"


def test_a_record_with_no_broker_timestamp_is_stored_with_a_null_time(plant) -> None:
    """A null broker_time, not a refused update: `[None]` went over kola as `enlist ::`,
    which the plant's timestamp column refused - in error mode, silently."""
    fields = contract_fields(str(REPO))
    msg = FakeMessage(b"{}", 0, 3, -1, _ts_type=0)
    publish_message(plant, KAFKA_RAW_TABLE, msg, fields)
    (row,) = _stored(plant)
    assert row["offset"] == 3
    assert plant.sync("exec null broker_time from kafka_message").to_list() == [True]
    assert plant.sync("count badmsgs") == 0


def test_a_payload_that_is_not_utf8_is_refused_naming_the_record(plant) -> None:
    """kola carries a Python `bytes` to q characters only by decoding it as UTF-8,
    so these bytes cannot reach kafka_message as received. Refused - the offset is
    then never committed - rather than crashing inside kola with no coordinates."""
    fields = contract_fields(str(REPO))
    msg = FakeMessage(b"\xff\x00not json", 2, 9, 1, _topic="t.raw")
    with pytest.raises(UqsError, match=r"t\.raw\[2\]@9: the payload is not valid UTF-8"):
        publish_message(plant, KAFKA_RAW_TABLE, msg, fields)
    with pytest.raises(UqsError, match=r"t\.raw\[2\]@9: the key is not valid UTF-8"):
        bad_key = FakeMessage(b"{}", 2, 9, 1, "t.raw", b"\xfe")
        publish_message(plant, KAFKA_RAW_TABLE, bad_key, fields)
    assert _stored(plant) == []


def test_an_update_the_plant_refuses_is_raised_not_committed(plant) -> None:
    """The commit must follow a publish the plant STORED. In error mode a refused
    `.u.upd` answers success, so a bare `.u.upd` let the consumer commit a record
    that is only in the tickerplant's error log (#2192)."""
    fields = contract_fields(str(REPO))
    plant.sync("`kafka_message set update extra:`long$() from kafka_message")
    with pytest.raises(UqsError, match="the tickerplant refused .* NOT stored"):
        publish_message(plant, KAFKA_RAW_TABLE, a_message(0, 4), fields)
    assert plant.sync("count badmsgs") == 1, "still in the error log, as TorQ keeps it"


def test_several_records_keep_their_order_and_coordinates_in_the_plant(plant) -> None:
    fields = contract_fields(str(REPO))
    msgs = [a_message(0, 5), a_message(1, 0), a_message(0, 6), a_message(1, 0)]
    for m in msgs:
        publish_message(plant, KAFKA_RAW_TABLE, m, fields)
    rows = _stored(plant)
    assert [(r["partition"], r["offset"]) for r in rows] == [(0, 5), (1, 0), (0, 6), (1, 0)]
    assert len(rows) == len(msgs), "every delivery, the repeat included: the audit trail"


_NATIVE_Q = """
\\l src/init.q
\\l src/etl/init.q
/ What kafka_feed1 publishes for the same record: its kfk message, through
/ .qetl.kafka.from_messages (kfk hands the key and payload as bytes).
native:{[tp;p;o;k;ts;v] .qetl.kafka.from_messages flip
    `mtype`topic`partition`offset`msgtime`data`key!(
    enlist `;enlist tp;enlist `int$p;enlist o;enlist ts;enlist "x"$v;enlist "x"$k)};
"""


@pytest.mark.parametrize(
    "payload",
    [b'{"sym": "EURUSD"}', b"x", b"", "é".encode()],
    ids=["json", "one-character", "empty", "multibyte"],
)
def test_both_consumers_publish_the_same_envelope(tmp_path, start_q, payload) -> None:
    """kafka_flow1 must not be able to tell the consumers apart: the row the Python
    consumer stores equals what kafka_feed1 builds for the same record."""
    import kola

    boot = tmp_path / "plant.q"
    boot.write_text(_NATIVE_Q + _PLANT_Q.replace("{PLANT_LINE}", _plant_line()))
    fields = contract_fields(str(REPO))
    ts_ms = 1_760_950_801_750
    msg = FakeMessage(payload, 1, 7, ts_ms, _topic="example.crypto.trades", _key=b"BTC-USD")
    with start_q(str(boot), startup_seconds=60) as port:
        conn = kola.Q("127.0.0.1", port, user="", passwd="")
        conn.connect()
        try:
            publish_message(conn, KAFKA_RAW_TABLE, msg, fields)
            # `g# on topic is the plant table's attribute, not the envelope's.
            same = conn.sync(
                '{[v] n:native[`example.crypto.trades;1;7;"BTC-USD";'
                "2025.10.20D09:00:01.750;v]; p:update `#topic from delete time from kafka_message;"
                " (n~p; (meta n)~meta p)}",
                payload,
            )
        finally:
            conn.disconnect()
    assert list(same) == [True, True]
