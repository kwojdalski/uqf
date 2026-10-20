"""The Kafka crypto example's Python half (#1492), with no broker and no stack.

What needs a broker is gated: UQF_KAFKA_EXAMPLE_REAL=1 starts a real disposable
Kafka (Java, and archive.apache.org or a cache) and runs the publisher and two
groups against it. The default lane proves what can be proved without one: the
batch, the broker configuration, the diagnosis of real broker text, the report
and the expectations the verifier holds the RDB to.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import socket
from datetime import datetime
from pathlib import Path

import pytest

from uqs.external.kafka_example import batch, broker, diagnose, verify
from uqs.external.kafka_example.report import Fail, Report, wait_until
from uqs.paths import UqsError

#: Text read off a real Apache Kafka 4.3.1 broker by librdkafka 2.16.0 (see
#: tests/q/test_kafka_diagnostics.q, which holds the same strings).
REAL_RESOLVE = (
    "broker-unresolvable.invalid:43687/1: Failed to resolve "
    "'broker-unresolvable.invalid:43687': Name or service not known (after 1ms in state CONNECT)"
)
REAL_GROUP = "FindCoordinator response error: Group authorization failed."
REAL_TOPIC = (
    "Subscribed topic not available: example.crypto.trades: Broker: Topic authorization failed"
)


# ------------------------------------------------------------------------ the batch


def test_the_batch_is_deterministic_and_has_the_documented_shape() -> None:
    first, again = batch.initial_batch(), batch.initial_batch()
    assert first == again, "no clock and no random source: a re-run is the same data"
    valid = [r for r in first if r.expect == "row"]
    invalid = [r for r in first if r.expect != "row"]
    assert (len(valid), len(invalid)) == (24, 5)
    assert sorted(r.expect for r in invalid) == sorted(reason for _, _, reason in batch.INVALID)
    assert {r.topic for r in first} == set(batch.TOPICS)


def test_every_valid_record_is_a_complete_trade_with_a_string_id() -> None:
    for rec in batch.initial_batch():
        if rec.expect != "row":
            continue
        assert rec.value is not None and rec.trade is not None
        fields = json.loads(rec.value)
        assert set(fields) == {"sym", "trade_id", "ts", "price", "qty", "side"}
        assert isinstance(fields["trade_id"], str) and fields["trade_id"] == rec.trade["trade_id"]
        assert fields["side"] in ("buy", "sell") and fields["price"] > 0 and fields["qty"] > 0
        assert fields["ts"].endswith("Z")


def test_trade_ids_are_unique_across_every_batch() -> None:
    ids = [
        r.trade["trade_id"]
        for r in batch.initial_batch() + batch.extra_batch("R") + batch.extra_batch("D")
        if r.trade
    ]
    assert len(ids) == len(set(ids))


def test_the_same_offset_will_exist_on_several_topic_partitions() -> None:
    """Partitions are explicit, so offsets repeat across them: the batch exists to catch a
    decoder that keys its deduplication on the offset alone."""
    where = {(r.topic, r.partition) for r in batch.initial_batch() if r.expect == "row"}
    assert where == {(batch.TOPIC_A, 0), (batch.TOPIC_A, 1), (batch.TOPIC_B, 0)}


def test_each_invalid_payload_is_invalid_in_its_own_way() -> None:
    by = {name: value or b"" for name, value, _ in batch.INVALID}
    assert [v for n, v, _ in batch.INVALID if n == "empty"] == [None]
    with pytest.raises(json.JSONDecodeError):
        json.loads(by["not_json"])
    assert "price" not in json.loads(by["missing_price"])
    assert isinstance(json.loads(by["numeric_trade_id"])["trade_id"], int)
    assert json.loads(by["negative_quantity"])["qty"] < 0


# ------------------------------------------------------------------- the broker config


def props(
    *,
    advertised_host: str = "127.0.0.1",
    decoy_port: int | None = None,
    users: dict[str, str] | None = None,
) -> dict[str, str]:
    text = broker.server_properties(
        data=Path("/d"),
        port=9092,
        controller_port=9093,
        advertised_host=advertised_host,
        users=users,
        decoy_port=decoy_port,
    )
    return dict(line.split("=", 1) for line in text.splitlines())


def test_the_advertised_listener_is_exactly_the_host_given() -> None:
    p = props(advertised_host="localhost")
    assert p["advertised.listeners"] == "PLAINTEXT://localhost:9092"
    assert p["listeners"] == "PLAINTEXT://127.0.0.1:9092,CONTROLLER://127.0.0.1:9093"
    assert "CONTROLLER" not in p["advertised.listeners"], "the controller is never advertised"
    assert p["auto.create.topics.enable"] == "false"


def test_the_decoy_listener_advertises_a_name_that_cannot_resolve() -> None:
    p = props(decoy_port=9999)
    assert "DECOY://127.0.0.1:9999" in p["listeners"]
    assert f"DECOY://{broker.UNRESOLVABLE_HOST}:9999" in p["advertised.listeners"]
    assert broker.UNRESOLVABLE_HOST.endswith(".invalid"), "RFC 2606: never resolves"
    with pytest.raises(OSError):
        socket.getaddrinfo(broker.UNRESOLVABLE_HOST, 9999)


def test_a_secure_broker_denies_by_default_and_names_its_users() -> None:
    p = props(users={u: f"pw-{u}" for u in broker.IDENTITIES})
    assert p["advertised.listeners"].startswith("SASL_PLAINTEXT://")
    assert p["authorizer.class.name"].endswith("StandardAuthorizer")
    assert "allow.everyone.if.no.acl.found" not in p, "no ACL means no access"
    assert p["super.users"] == "User:admin;User:ANONYMOUS"
    jaas = p["listener.name.sasl_plaintext.plain.sasl.jaas.config"]
    assert all(f'user_{u}="pw-{u}"' in jaas for u in broker.IDENTITIES)


def test_a_directory_without_the_marker_is_never_loaded_or_removed(tmp_path: Path) -> None:
    (tmp_path / "broker.json").write_text("{}")
    with pytest.raises(UqsError, match="not a directory the kafka example made"):
        broker.Broker.load(tmp_path)


def test_a_checksum_mismatch_deletes_the_download_and_refuses(tmp_path: Path, monkeypatch) -> None:
    name = f"kafka_{broker.SCALA}-{broker.VERSION}"
    (tmp_path / f"{name}.tgz").write_bytes(b"not a tarball")

    def fake_urlopen(*args: object, **kwargs: object):
        return contextlib.nullcontext(io.BytesIO(b"00000000 " * 16))

    monkeypatch.setattr(broker.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(UqsError, match="SHA-512 does not match"):
        broker.fetch_distribution(tmp_path)
    assert not (tmp_path / f"{name}.tgz").exists()


# ----------------------------------------------------------------------- the diagnosis


def test_an_unresolvable_advertised_host_is_an_advertised_endpoint_failure() -> None:
    d = diagnose.classify(-193, REAL_RESOLVE, bootstrap=("127.0.0.1:44881",))
    assert (d.stage, d.kind, d.endpoint) == (
        "advertised",
        "connect",
        "broker-unresolvable.invalid:43687",
    )


def test_the_bootstrap_node_is_a_bootstrap_failure_and_brokers_down_has_no_endpoint() -> None:
    boot = diagnose.classify(
        -195, "127.0.0.1:9/bootstrap: Connect to ipv4#127.0.0.1:9 failed: Connection refused"
    )
    assert boot.stage == "bootstrap"
    assert diagnose.classify(-187, "1/1 brokers are down").endpoint == ""


def test_group_and_topic_refusals_are_different_stages_by_code_and_by_text() -> None:
    assert diagnose.classify(30, REAL_GROUP).stage == "group"
    assert diagnose.classify(29, REAL_TOPIC).stage == "consume"
    assert diagnose.classify(None, REAL_GROUP).kind == "group_auth"
    assert diagnose.classify("TOPIC_AUTHORIZATION_FAILED", "x").kind == "topic_auth"
    assert diagnose.classify(None, "something new").kind == "other"


def test_probing_names_whether_it_was_the_resolver_or_the_connection() -> None:
    assert "cannot resolve" in (diagnose.probe_endpoint("broker-unresolvable.invalid", 9) or "")
    with socket.socket() as closed:
        closed.bind(("127.0.0.1", 0))
        port = closed.getsockname()[1]
    assert "cannot connect" in (diagnose.probe_endpoint("127.0.0.1", port) or "")
    with socket.socket() as listening:
        listening.bind(("127.0.0.1", 0))
        listening.listen()
        assert diagnose.probe_endpoint("127.0.0.1", listening.getsockname()[1]) is None


# ------------------------------------------------------------------------- the report


def test_a_failed_stage_skips_the_ones_after_it_and_the_exit_is_not_ok() -> None:
    out = io.StringIO()
    rep = Report(out)
    assert rep.run("one", lambda: "fine") is True
    assert rep.run("two", lambda: (_ for _ in ()).throw(Fail("expected 3, saw 2"))) is None
    assert rep.run("three", lambda: "never") is None
    assert rep.run("cleanup", lambda: "still runs", always=True) is True
    lines = out.getvalue().splitlines()
    assert [ln.split()[0] for ln in lines] == ["PASS", "FAIL", "SKIP", "PASS"]
    assert "expected 3, saw 2" in lines[1] and not rep.ok
    assert rep.summary() == "FAIL: 2 passed, 1 failed, 1 skipped"


def test_a_stage_that_crashes_is_a_failure_not_a_traceback() -> None:
    out = io.StringIO()
    rep = Report(out)
    rep.run("boom", lambda: 1 / 0)
    assert not rep.ok and "ZeroDivisionError" in out.getvalue()


def test_an_expected_refusal_is_checked_even_after_an_earlier_failure() -> None:
    rep = Report(io.StringIO())
    rep.run("one", lambda: (_ for _ in ()).throw(Fail("x")))
    rep.expect("denied", lambda: "refused as diagnosed")
    assert [r[0] for r in rep.results] == ["FAIL", "PASS"]


def test_waiting_is_bounded_and_says_how_far_it_got() -> None:
    seen = iter(range(100))
    with pytest.raises(Fail, match=r"rows did not hold within 0s \(last seen: \d+\)"):
        wait_until(lambda: (False, next(seen)), 0, interval=0.01, what="rows")
    assert wait_until(lambda: (True, "x"), 5) == "x"


def test_waiting_for_a_child_that_exited_sees_it_gone_not_a_zombie() -> None:
    """A process this one started stays a zombie - and answers kill(pid, 0) - until it is
    reaped, so a wait that only signalled would sit out its whole deadline."""
    import signal
    import subprocess
    import time

    from uqs.external.kafka_example.lifecycle_wait import wait_gone

    child = subprocess.Popen(["sleep", "30"])  # noqa: S607
    assert not wait_gone(child.pid, 0.3), "alive: not gone"
    os.kill(child.pid, signal.SIGTERM)
    started = time.monotonic()
    assert wait_gone(child.pid, 10)
    assert time.monotonic() - started < 5


# ------------------------------------------------------------ what the RDB is held to


def memo_for(records: list[batch.Record]) -> list[dict]:
    """The delivered memo as if each partition's records landed at offsets 0, 1, 2..."""
    next_offset: dict[tuple, int] = {}
    memo = []
    for r in records:
        key = (r.topic, r.partition)
        off = next_offset.get(key, 0)
        next_offset[key] = off + 1
        memo.append(
            {
                "topic": r.topic, "partition": r.partition, "offset": off, "key": r.key,
                "expect": r.expect, "trade": r.trade,
                "value": r.value.decode() if r.value is not None else None,
            }
        )  # fmt: skip
    return memo


def rdb_row(exp: dict) -> dict:
    return {**exp, "broker_time": datetime(2026, 10, 20), "time": datetime(2026, 10, 20, 9)}


def test_expectations_are_exact_per_coordinate() -> None:
    memo = memo_for(batch.initial_batch())
    rows = verify.expected_trades(memo)
    assert len(rows) == 24 and len(verify.expected_dead(memo)) == 5
    assert rows == sorted(rows, key=lambda r: (r["topic"], r["partition"], r["offset"]))
    assert {d[3] for d in verify.expected_dead(memo)} == {"malformed", "empty_payload", "mapping"}
    first = rows[0]
    assert first["event_time"] == datetime(2026, 10, 20, 9, 0, 0)
    assert verify.compare_trades([rdb_row(r) for r in rows], memo).startswith("24 rows match")


def test_a_wrong_value_is_named_by_its_coordinate_and_column() -> None:
    memo = memo_for(batch.initial_batch())
    rows = [rdb_row(r) for r in verify.expected_trades(memo)]
    rows[3] = {**rows[3], "price": rows[3]["price"] + 1}
    with pytest.raises(Fail, match=r"\[\d\]@\d+: price is"):
        verify.compare_trades(rows, memo)
    with pytest.raises(Fail, match="23 rows, expected 24"):
        verify.compare_trades(rows[:-1], memo)


# ------------------------------------------------------------------ against a real broker


@pytest.mark.skipif(
    os.environ.get("UQF_KAFKA_EXAMPLE_REAL") != "1",
    reason="UQF_KAFKA_EXAMPLE_REAL=1 starts a real disposable Kafka broker (needs Java)",
)
def test_a_real_broker_delivers_the_batch_to_two_independent_groups(tmp_path: Path) -> None:
    from uqs.external.kafka_example import kclient, phases
    from uqs.external.kafka_example.feeds import Feed
    from uqs.external.kafka_example.publisher import publish

    cache = Path(os.environ.get("UQF_KAFKA_CACHE", Path.home() / ".cache" / "uqs" / "kafka"))
    b = broker.start(cache=cache, base=tmp_path)
    try:
        kclient.create_topics(b)
        done = publish(b, batch.initial_batch())
        assert len(done) == 29
        assert {(d.record.topic, d.partition, d.offset) for d in done if d.record.expect == "row"}
        feed = Feed("python", b, tmp_path, 0, batch.TOPICS, kclient.GROUP_1)
        ctx = verify.Context(b, 0, feed, timeout=60)
        ctx.remember(done)
        out = io.StringIO()
        rep = Report(out)
        phases.run_groups(ctx, rep)
        assert rep.ok, out.getvalue()
    finally:
        broker.stop(b)
    assert not b.directory.exists()
