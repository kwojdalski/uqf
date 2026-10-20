"""The staged verification: broker, feed, plant, RDB - with real queries and real deadlines.

`run_basic` is the whole path in the order the issue lists its stages: library,
bootstrap metadata, advertised endpoints, the group, records polled and
published and committed, and rows visible in the RDB. Every comparison is
against the coordinates Kafka reported at publish time (`publisher.Delivered`),
so the expectation is exact, and every wait is bounded.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from uqs.external.kafka_example import rdb
from uqs.external.kafka_example.batch import PARTITIONS, TOPICS, Record, initial_batch
from uqs.external.kafka_example.broker import Broker
from uqs.external.kafka_example.diagnose import probe_endpoint
from uqs.external.kafka_example.feeds import Feed
from uqs.external.kafka_example.kclient import GROUP_1, client_config
from uqs.external.kafka_example.publisher import Delivered, publish
from uqs.external.kafka_example.report import Fail, Report, wait_until


@dataclass
class Context:
    """What a phase needs to know about the stack it is verifying."""

    broker: Broker
    rdb_port: int
    feed: Feed
    timeout: float = 60.0
    identity: str | None = None
    delivered: list[Delivered] = field(default_factory=list)

    def cfg(self, **extra: Any) -> dict[str, Any]:
        return client_config(self.broker, self.identity, **extra)

    @property
    def store(self) -> Path:
        return self.broker.directory / "delivered.json"

    def remember(self, more: list[Delivered]) -> None:
        self.delivered.extend(more)
        self.store.write_text(
            json.dumps(
                [
                    {
                        "topic": d.record.topic,
                        "partition": d.partition,
                        "offset": d.offset,
                        "key": d.record.key,
                        "expect": d.record.expect,
                        "trade": d.record.trade,
                        "value": d.record.value.decode() if d.record.value is not None else None,
                    }
                    for d in self.delivered
                ]
            )
        )

    def recall(self) -> list[dict]:
        return json.loads(self.store.read_text()) if self.store.is_file() else []


# ------------------------------------------------------------------ expectations


def _utc(text: str) -> datetime:
    """A payload's `ts` as the naive UTC datetime the RDB hands back."""
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC).replace(tzinfo=None)


def expected_trades(memo: list[dict]) -> list[dict]:
    """The crypto_trade rows the memo of delivered records must produce, ordered by coordinates."""
    out = [
        {
            "topic": m["topic"], "partition": m["partition"], "offset": m["offset"],
            "sym": m["trade"]["sym"], "trade_id": m["trade"]["trade_id"],
            "price": m["trade"]["price"], "qty": m["trade"]["qty"], "side": m["trade"]["side"],
            "event_time": _utc(m["trade"]["ts"]),
        }
        for m in memo
        if m["expect"] == "row"
    ]  # fmt: skip
    return sorted(out, key=lambda r: (r["topic"], r["partition"], r["offset"]))


def expected_dead(memo: list[dict]) -> list[tuple]:
    """(topic, partition, offset, reason) of every record that must be dead-lettered."""
    return sorted(
        (m["topic"], m["partition"], m["offset"], m["expect"]) for m in memo if m["expect"] != "row"
    )


def compare_trades(actual: list[dict], memo: list[dict]) -> str:
    """Raise `Fail` naming the first difference between the RDB's rows and the expected ones."""
    want = expected_trades(memo)
    if len(actual) != len(want):
        raise Fail(f"crypto_trade has {len(actual)} rows, expected {len(want)}")
    for got, exp in zip(actual, want, strict=True):
        for col, value in exp.items():
            seen = got[col]
            if col == "event_time":
                seen = seen.replace(tzinfo=None)
            if seen != value:
                raise Fail(
                    f"{exp['topic']}[{exp['partition']}]@{exp['offset']}: "
                    f"{col} is {seen!r}, expected {value!r}"
                )
        if got["broker_time"] is None or got["time"] is None:
            raise Fail(
                f"{exp['topic']}[{exp['partition']}]@{exp['offset']}: broker_time or time is null"
            )
    return f"{len(want)} rows match the delivered coordinates, values and event times"


# ---------------------------------------------------------------------- broker facts


def end_offsets(ctx: Context) -> dict[tuple[str, int], int]:
    """(topic, partition) -> the offset after the last record, from the broker."""
    from confluent_kafka import Consumer, TopicPartition  # ty: ignore[unresolved-import]

    consumer = Consumer(ctx.cfg(**{"group.id": f"{GROUP_1}-probe-{id(ctx)}"}))
    try:
        return {
            (t, p): consumer.get_watermark_offsets(TopicPartition(t, p), timeout=15)[1]
            for t in TOPICS
            for p in range(PARTITIONS)
        }
    finally:
        consumer.close()


def committed(ctx: Context, group: str) -> dict[tuple[str, int], int]:
    """(topic, partition) -> the group's committed offset; -1001 where it has none.

    Read with a consumer that never subscribes and never commits, so looking
    does not move anything.
    """
    from confluent_kafka import Consumer, TopicPartition  # ty: ignore[unresolved-import]

    consumer = Consumer(ctx.cfg(**{"group.id": group, "enable.auto.commit": False}))
    try:
        tps = [TopicPartition(t, p) for t in TOPICS for p in range(PARTITIONS)]
        return {(tp.topic, tp.partition): tp.offset for tp in consumer.committed(tps, timeout=15)}
    finally:
        consumer.close()


def group_state(ctx: Context, group: str) -> tuple[str, int]:
    """(state, members) of a consumer group, from a group description.

    That is what the BROKER says about membership. It is not proof the members
    may read the topic - a principal can describe a group it cannot consume
    with - so no stage passes on it alone.
    """
    from confluent_kafka.admin import AdminClient  # ty: ignore[unresolved-import]

    admin = AdminClient(ctx.cfg())
    desc = admin.describe_consumer_groups([group])[group].result(15)
    return desc.state.name, len(desc.members)


# ------------------------------------------------------------------------- stages


def stage_library(ctx: Context) -> str:
    if ctx.feed.kind == "native":
        home = Path(ctx.feed.kfk_home)
        missing = [f for f in ("kfk.q", "libkfk.so") if not (home / f).is_file()]
        if missing:
            raise Fail(
                f"{home} lacks {', '.join(missing)} - "
                "build KxSystems/kafka v1.6.6 (docs/services/kafka.md)"
            )
        return f"kfk.q and libkfk.so in {home}"
    import confluent_kafka  # ty: ignore[unresolved-import]

    py, lib = confluent_kafka.version()[0], confluent_kafka.libversion()[0]
    return f"confluent-kafka {py} on librdkafka {lib}"


def stage_bootstrap(ctx: Context) -> tuple[str, Any]:
    from confluent_kafka.admin import AdminClient  # ty: ignore[unresolved-import]

    try:
        meta = AdminClient(ctx.cfg()).list_topics(timeout=15)
    except Exception as exc:  # noqa: BLE001
        raise Fail(f"no metadata from bootstrap {ctx.broker.bootstrap}: {exc}") from exc
    missing = [t for t in TOPICS if t not in meta.topics]
    if missing:
        raise Fail(
            f"bootstrap {ctx.broker.bootstrap} answered but lacks topic(s) {', '.join(missing)}"
        )
    return (
        f"{ctx.broker.bootstrap} answered: {len(meta.brokers)} broker(s), "
        f"topics {', '.join(TOPICS)}",
        meta,
    )


def stage_advertised(meta: Any, bootstrap: str) -> str:
    """Probe every broker address the METADATA gives - these, not the bootstrap, carry the data."""
    notes, bad = [], []
    for b in meta.brokers.values():
        why = probe_endpoint(b.host, b.port)
        (bad if why else notes).append(f"{b.host}:{b.port}" + (f" ({why})" if why else ""))
    if bad:
        raise Fail(
            f"advertised endpoint(s) unreachable from here: {'; '.join(bad)} - "
            f"bootstrap {bootstrap} worked, "
            "but the broker tells clients to use these; fix its advertised.listeners "
            "(or the tunnel/hosts entry)"
        )
    return (
        f"advertised {', '.join(notes)} resolve and accept connections (bootstrap was {bootstrap})"
    )


def stage_rdb(ctx: Context) -> str:
    n = rdb.count("crypto_trade", ctx.rdb_port)
    got = rdb.types("crypto_trade", ctx.rdb_port)
    if got != rdb.CRYPTO_TRADE_TYPES:
        raise Fail(f"crypto_trade's types are {got}, expected {rdb.CRYPTO_TRADE_TYPES}")
    return f"RDB :{ctx.rdb_port} answers; crypto_trade has {n} rows and the declared types"


def stage_fresh(ctx: Context) -> str:
    ends = end_offsets(ctx)
    used = {k: v for k, v in ends.items() if v}
    if used:
        raise Fail(
            f"topics already hold records {used}: the example needs a fresh disposable broker"
        )
    for t in ("crypto_trade", "kafka_message", "kafka_dead_letter"):
        if n := rdb.count(t, ctx.rdb_port):
            raise Fail(f"RDB {t} already has {n} rows: restart the stack for a clean run")
    return "topics are empty and the RDB tables hold nothing"


def stage_publish(ctx: Context, records: list[Record]) -> tuple[str, list[Delivered]]:
    done = publish(ctx.broker, records, identity=ctx.identity)
    ctx.remember(done)
    ok = sum(1 for d in done if d.record.expect == "row")
    where = sorted({(d.record.topic, d.partition) for d in done})
    return (
        f"{len(done)} records acknowledged by the broker ({ok} valid, {len(done) - ok} invalid) "
        f"across {len(where)} topic-partitions; an acknowledgement is not an fsync",
        done,
    )


def stage_group(ctx: Context, group: str) -> str:
    def joined():
        try:
            state, members = group_state(ctx, group)
        except Exception as exc:  # noqa: BLE001 - a group that does not exist yet is not an error yet
            return False, f"describe failed: {exc}"
        return (
            state == "STABLE" and members >= 1
        ), f"{state} with {members} member(s); feed log: {ctx.feed.log_tail(200)!r}"

    wait_until(joined, ctx.timeout, what=f"group {group} joined (state STABLE, a member)")
    state, members = group_state(ctx, group)
    return (
        f"group {group} is {state} with {members} member (membership only: reading is proved below)"
    )


def stage_committed(ctx: Context, group: str) -> str:
    def caught_up():
        ends, got = end_offsets(ctx), committed(ctx, group)
        behind = {k: (got.get(k), v) for k, v in ends.items() if v and got.get(k, -1) < v}
        return (not behind), f"behind (committed, end): {behind}"

    wait_until(caught_up, ctx.timeout, what=f"group {group}'s committed offsets reaching the end")
    ends = end_offsets(ctx)
    return "committed == end on " + ", ".join(
        f"{t}[{p}]={o}" for (t, p), o in sorted(ends.items()) if o
    )


def stage_rows(ctx: Context) -> str:
    memo = ctx.recall()
    want = len(expected_trades(memo))
    wait_until(
        lambda: (
            rdb.count("crypto_trade", ctx.rdb_port) >= want,
            rdb.count("crypto_trade", ctx.rdb_port),
        ),
        ctx.timeout,
        what=f"crypto_trade reaching {want} rows",
    )
    return compare_trades(rdb.rows("crypto_trade", ctx.rdb_port), memo)


def stage_dead_letters(ctx: Context) -> str:
    memo = ctx.recall()
    want = expected_dead(memo)
    wait_until(
        lambda: (
            rdb.count("kafka_dead_letter", ctx.rdb_port) >= len(want),
            rdb.count("kafka_dead_letter", ctx.rdb_port),
        ),
        ctx.timeout,
        what=f"kafka_dead_letter reaching {len(want)} rows",
    )
    got = sorted(
        (r["topic"], r["partition"], r["offset"], r["reason"])
        for r in rdb.rows("kafka_dead_letter", ctx.rdb_port)
    )
    if got != want:
        raise Fail(f"dead letters are {got}, expected {want}")
    by = sorted({w[3] for w in want})
    return f"{len(want)} dead letters with the expected coordinates and reasons ({', '.join(by)})"


def stage_raw(ctx: Context, *, extra_copies: int = 0) -> str:
    """kafka_message holds every delivery - redeliveries included - with the payload as sent."""
    memo = ctx.recall()
    rows = rdb.rows("kafka_message", ctx.rdb_port)
    seen: dict[tuple, list[str]] = {}
    for r in rows:
        seen.setdefault((r["topic"], r["partition"], r["offset"]), []).append(r["payload"])
    for m in memo:
        got = seen.get((m["topic"], m["partition"], m["offset"]))
        if not got:
            raise Fail(f"kafka_message lacks {m['topic']}[{m['partition']}]@{m['offset']}")
        sent = m["value"] or ""
        if any(p != sent for p in got):
            raise Fail(
                f"{m['topic']}[{m['partition']}]@{m['offset']}: payload differs from what was sent"
            )
    return (
        f"kafka_message holds all {len(memo)} records ({len(rows)} deliveries) "
        "with their payloads as sent"
    )


def stage_identity(ctx: Context) -> str:
    """Offset identity is (topic, partition, offset): equal offsets elsewhere are other records."""
    rows = rdb.rows("crypto_trade", ctx.rdb_port)
    coords = {(r["topic"], r["partition"], r["offset"]) for r in rows}
    if len(coords) != len(rows):
        raise Fail(
            f"{len(rows) - len(coords)} duplicate (topic, partition, offset) in crypto_trade"
        )
    by_offset: dict[int, set] = {}
    for t, p, o in coords:
        by_offset.setdefault(o, set()).add((t, p))
    shared = {o: sorted(v) for o, v in by_offset.items() if len(v) > 1}
    if not shared:
        raise Fail(
            "no offset occurs on more than one topic-partition: "
            "the batch cannot show offset identity"
        )
    top = max(shared, key=lambda o: len(shared[o]))
    return (
        f"{len(coords)} rows, no duplicate coordinate; {len(shared)} offsets occur on several "
        f"topic-partitions (offset {top} on {len(shared[top])} of them) "
        "and none was dropped as a repeat"
    )


def run_basic(ctx: Context, rep: Report) -> None:
    """Publish the known batch and verify every stage, library to RDB."""
    rep.run("library", lambda: stage_library(ctx))
    meta = rep.run("bootstrap", lambda: stage_bootstrap(ctx))
    rep.run("advertised", lambda: stage_advertised(meta, ctx.broker.bootstrap))
    rep.run("rdb", lambda: stage_rdb(ctx))
    rep.run("fresh", lambda: stage_fresh(ctx))
    rep.run("publish", lambda: stage_publish(ctx, initial_batch()))
    rep.run(
        "feed_started",
        lambda: (ctx.feed.start(), f"{ctx.feed.kind} feed started in group {ctx.feed.group}")[1],
    )
    rep.run("group_joined", lambda: stage_group(ctx, ctx.feed.group))
    rep.run("rows_visible", lambda: stage_rows(ctx))
    rep.run("dead_letters", lambda: stage_dead_letters(ctx))
    rep.run("raw_envelopes", lambda: stage_raw(ctx))
    rep.run("committed", lambda: stage_committed(ctx, ctx.feed.group))
    rep.run("offset_identity", lambda: stage_identity(ctx))
