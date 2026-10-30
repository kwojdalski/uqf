"""The phases after the basic path that need the stack or whole groups: restart, redelivery, groups.

Each is a few named stages (report.py) over the same Context (verify.py). Restart and
redelivery need a plaintext broker with the example's stack running; groups needs only a
broker. The refusals - a denied group or topic, an unresolvable advertised host - are in
refusals.py.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from uqs.external import connection, kafka_streamer
from uqs.external.kafka_example import rdb
from uqs.external.kafka_example.batch import TOPICS, extra_batch
from uqs.external.kafka_example.kclient import GROUP_1, GROUP_2, GROUP_3, client_config
from uqs.external.kafka_example.report import Fail, Report, wait_until
from uqs.external.kafka_example.verify import (
    Context,
    committed,
    compare_trades,
    end_offsets,
    stage_committed,
    stage_identity,
    stage_publish,
)


def _counts(ctx: Context) -> tuple[int, int, int]:
    p = ctx.rdb_port
    return (
        rdb.count("crypto_trade", p),
        rdb.count("kafka_message", p),
        rdb.count("kafka_dead_letter", p),
    )


# --------------------------------------------------------------------------- restart


def run_restart(ctx: Context, rep: Report) -> None:
    """Stop the feed, publish more, start it again: it resumes from the committed offsets.

    The group has committed past the first batch, so the restarted feed must read
    ONLY the new records even though auto.offset.reset is `earliest`: a committed
    offset takes precedence. kafka_message growing by exactly the new records -
    not by the whole topic - is the proof.
    """
    before = _counts(ctx)
    rep.run("restart_stop", lambda: (ctx.feed.stop(), "feed stopped; its group is left")[1])
    new = rep.run("restart_publish", lambda: stage_publish(ctx, extra_batch("R")))
    rep.run("restart_start", lambda: (ctx.feed.start(), "feed started again in the same group")[1])

    def resumed() -> str:
        want = before[1] + len(new)
        wait_until(
            lambda: (
                rdb.count("kafka_message", ctx.rdb_port) >= want,
                rdb.count("kafka_message", ctx.rdb_port),
            ),
            ctx.timeout,
            what=f"kafka_message reaching {want}",
        )
        time.sleep(3)  # a feed that re-read from `earliest` would keep publishing past `want`
        got = rdb.count("kafka_message", ctx.rdb_port)
        if got != want:
            raise Fail(
                f"kafka_message has {got} rows, expected exactly {want}: "
                "the feed did not resume from its committed offsets"
            )
        return (
            f"kafka_message grew by exactly the {len(new)} new records "
            "(earliest ignored: the group had committed)"
        )

    rep.run("resumed_from_committed", resumed)
    rep.run(
        "restart_rows", lambda: compare_trades(rdb.rows("crypto_trade", ctx.rdb_port), ctx.recall())
    )
    rep.run("restart_committed", lambda: stage_committed(ctx, ctx.feed.group))
    rep.run("restart_identity", lambda: stage_identity(ctx))


# ------------------------------------------------------------------------ redelivery


def doomed_consume(ctx: Context, stp_port: int, n: int) -> int:
    """Consume `n` records in the feed's group, publish them, and die uncommitted.

    This is the publish-before-commit failure made explicit: it uses the real
    envelope and publish path (kafka_streamer.publish_message), then
    closes without committing, as a process killed between the two would.
    """
    import kola
    from confluent_kafka import Consumer  # ty: ignore[unresolved-import]

    fields = kafka_streamer.contract_fields(str(ctx.feed.repo_root))
    user, _, password = connection.tp_credential().partition(":")
    q = kola.Q(connection.TP_HOST, stp_port, user=user, passwd=password)
    q.connect()
    consumer = Consumer(
        ctx.cfg(
            **{"group.id": GROUP_1, "enable.auto.commit": False, "auto.offset.reset": "earliest"}
        )
    )
    consumer.subscribe(list(TOPICS))
    got, deadline = 0, time.monotonic() + ctx.timeout
    try:
        while got < n and time.monotonic() < deadline:
            msg = consumer.poll(1.0)
            if msg is None or msg.error():
                continue
            kafka_streamer.publish_message(q, kafka_streamer.KAFKA_RAW_TABLE, msg, fields)
            got += 1
    finally:
        consumer.close()  # auto-commit is off: closing commits nothing
        q.disconnect()
    return got


def run_redelivery(ctx: Context, rep: Report, stp_port: int) -> None:
    """A consumer publishes and dies before committing: its successor is redelivered.

    The decoded output is not doubled.
    """
    before = _counts(ctx)
    rep.run("redelivery_stop", lambda: (ctx.feed.stop(), "feed stopped")[1])
    new = rep.run("redelivery_publish", lambda: stage_publish(ctx, extra_batch("D")))
    n = len(new) if new else 0

    def doomed() -> str:
        got = doomed_consume(ctx, stp_port, n)
        if got != n:
            raise Fail(f"the doomed consumer received {got} of {n} records")
        pending = {
            k: v for k, v in end_offsets(ctx).items() if committed(ctx, GROUP_1).get(k, -1) < v
        }
        if not pending:
            raise Fail("the offsets were committed: this was not a publish-before-commit failure")
        return (
            f"published {n} records to the tickerplant and exited without committing "
            f"(uncommitted: {sorted(pending)})"
        )

    rep.run("doomed_consumer", doomed)
    rep.run(
        "redelivery_start", lambda: (ctx.feed.start(), "a successor started in the same group")[1]
    )

    def redelivered() -> str:
        want = before[1] + 2 * n
        wait_until(
            lambda: (
                rdb.count("kafka_message", ctx.rdb_port) >= want,
                rdb.count("kafka_message", ctx.rdb_port),
            ),
            ctx.timeout,
            what=f"kafka_message reaching {want} (each new record twice)",
        )
        rows = rdb.rows("kafka_message", ctx.rdb_port)
        mine = {(m["topic"], m["partition"], m["offset"]) for m in ctx.recall()[-n:]}
        times = {
            c: sum(1 for r in rows if (r["topic"], r["partition"], r["offset"]) == c) for c in mine
        }
        if set(times.values()) != {2}:
            raise Fail(
                f"deliveries per redelivered record: {sorted(times.values())}, expected all 2"
            )
        return f"each of the {n} records is on kafka_message twice: the audit trail shows it"

    rep.run("redelivered", redelivered)

    def deduped() -> str:
        time.sleep(2)
        now = _counts(ctx)
        if now[0] != before[0] + n:
            raise Fail(
                f"crypto_trade grew by {now[0] - before[0]}, expected {n}: "
                "the redelivery reached decoded output"
            )
        compare_trades(rdb.rows("crypto_trade", ctx.rdb_port), ctx.recall())
        return (
            f"crypto_trade grew by exactly {n}: "
            "the second delivery was dropped by (topic;partition;offset)"
        )

    rep.run("decoded_dedupe", deduped)
    rep.run("redelivery_committed", lambda: stage_committed(ctx, ctx.feed.group))


# ---------------------------------------------------------------------------- groups


def consume_all(
    ctx: Context, group: str, n: int, identity: str | None
) -> set[tuple[str, int, int]]:
    """Read `n` records in `group` from the start, commit, and return their coordinates."""
    from confluent_kafka import Consumer, TopicPartition  # ty: ignore[unresolved-import]

    consumer = Consumer(
        client_config(
            ctx.broker,
            identity,
            **{"group.id": group, "enable.auto.commit": False, "auto.offset.reset": "earliest"},
        )
    )
    consumer.subscribe(list(TOPICS))
    seen: set[tuple[str, int, int]] = set()
    top: dict[tuple[str, int], int] = {}
    deadline = time.monotonic() + ctx.timeout
    try:
        while len(seen) < n and time.monotonic() < deadline:
            msg = consumer.poll(1.0)
            if msg is not None and not msg.error():
                seen.add((msg.topic(), msg.partition(), msg.offset()))
                key = (msg.topic(), msg.partition())
                top[key] = max(top.get(key, -1), msg.offset())
        if top:
            # Explicit offsets: a bare commit() with nothing stored is an error, not a no-op.
            consumer.commit(
                offsets=[TopicPartition(t, p, o + 1) for (t, p), o in top.items()],
                asynchronous=False,
            )
    finally:
        consumer.close()
    return seen


def run_groups(ctx: Context, rep: Report, identity: str | None = None) -> None:
    """Two fresh groups each read every record, concurrently, and move nobody's offsets.

    The feed's own group (GROUP_1) may be running and is only observed: its
    committed offsets must be the same after as before.
    """
    memo = ctx.recall()
    want = {(m["topic"], m["partition"], m["offset"]) for m in memo}
    mine = (GROUP_2, GROUP_3)
    results: dict[str, Any] = {}
    before = committed(ctx, GROUP_1)

    def reader(group: str) -> None:
        try:
            results[group] = consume_all(ctx, group, len(want), identity)
        except Exception as exc:  # noqa: BLE001
            results[group] = exc

    def both() -> str:
        threads = [threading.Thread(target=reader, args=(g,)) for g in mine]
        [t.start() for t in threads]
        [t.join(ctx.timeout + 15) for t in threads]
        for g in mine:
            got = results.get(g)
            if not isinstance(got, set):
                raise Fail(f"group {g}: {got!r}")
            if got != want:
                missing = sorted(want - got)[:3]
                raise Fail(
                    f"group {g} received {len(got)} of {len(want)} records (missing {missing})"
                )
        return f"{GROUP_2} and {GROUP_3} each received all {len(want)} records, concurrently"

    rep.run("two_groups_complete", both)

    def independent() -> str:
        ends = end_offsets(ctx)
        for g in mine:
            got = committed(ctx, g)
            bad = {k: (got.get(k), v) for k, v in ends.items() if v and got.get(k) != v}
            if bad:
                raise Fail(f"group {g}'s committed offsets differ from the end: {bad}")
        after = committed(ctx, GROUP_1)
        if after != before:
            raise Fail(f"{GROUP_1}'s committed offsets moved from {before} to {after}")
        return f"both groups committed every partition's end; {GROUP_1}'s offsets are unchanged"

    rep.run("groups_independent", independent)
