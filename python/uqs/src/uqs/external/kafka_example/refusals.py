"""The phases that look at refusals: a denied group or topic, and an unresolvable advertised host.

Neither needs the stack, only a broker (`broker start --secure` for the first,
`--decoy` for the second). Both read what librdkafka reports through its error
callback and classify it as the q feed does (diagnose.py mirrors
src/etl/core/kafka_diagnostics.q), once per cause.
"""

from __future__ import annotations

import time
from typing import Any

from uqs.external.kafka_example.batch import TOPIC_A
from uqs.external.kafka_example.diagnose import Diagnosis, classify
from uqs.external.kafka_example.kclient import GROUP_1, client_config
from uqs.external.kafka_example.report import Fail, Report
from uqs.external.kafka_example.verify import Context, group_state

# ---------------------------------------------------------------------------- denial


def _first_error(ctx: Context, identity: str, window: float) -> tuple[Diagnosis | None, int, int]:
    """Subscribe as `identity`: the first error's diagnosis, the errors seen, the records read."""
    from confluent_kafka import Consumer  # ty: ignore[unresolved-import]

    errors: list[tuple[Any, str]] = []
    cfg = client_config(
        ctx.broker, identity,
        **{"group.id": GROUP_1, "enable.auto.commit": False, "auto.offset.reset": "earliest",
           "error_cb": lambda e: errors.append((e.name(), str(e.str())))},
    )  # fmt: skip
    consumer = Consumer(cfg)
    consumer.subscribe([TOPIC_A])
    records, deadline = 0, time.monotonic() + window
    try:
        while time.monotonic() < deadline and not errors:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                errors.append((msg.error().name(), str(msg.error().str())))
            else:
                records += 1
    finally:
        consumer.close()
    first = classify(*errors[0], bootstrap=(ctx.broker.bootstrap,)) if errors else None
    return first, len(errors), records


def run_denied(ctx: Context, rep: Report) -> None:
    """A denied group and a denied topic each get a clear, different, once-reported diagnosis."""
    wanted = (("nogroup", "group_auth", "group"), ("notopic", "topic_auth", "consume"))
    for identity, kind, stage in wanted:

        def denied(identity: str = identity, kind: str = kind, stage: str = stage) -> str:
            d, n, records = _first_error(ctx, identity, window=min(ctx.timeout, 30))
            if records:
                raise Fail(f"{identity} received {records} record(s): it was not denied")
            if d is None:
                raise Fail(
                    f"{identity} got no error within the window: no authorisation diagnostic"
                )
            if (d.kind, d.stage) != (kind, stage):
                raise Fail(
                    f"{identity}: expected {kind} at the {stage} stage, "
                    f"diagnosed {d.kind} at {d.stage}: {d.detail}"
                )
            return f"{identity} refused at the {d.stage} stage - {d.detail.strip()}"

        rep.expect(f"denied_{identity}", denied)

    def describe_is_not_consume() -> str:
        state, members = group_state(ctx_as(ctx, "notopic"), GROUP_1)
        d, _, records = _first_error(ctx, "notopic", window=min(ctx.timeout, 30))
        if records or d is None or d.kind != "topic_auth":
            raise Fail("notopic was not refused the topic")
        return (
            f"notopic can DESCRIBE group {GROUP_1} (state {state}) yet cannot read the topic "
            f"({d.kind}): a group-description permission is not proof of consume permission"
        )

    rep.expect("describe_is_not_consume", describe_is_not_consume)


def ctx_as(ctx: Context, identity: str) -> Context:
    """The same context, acting as another identity."""
    return Context(ctx.broker, ctx.rdb_port, ctx.feed, ctx.timeout, identity, ctx.delivered)


# ------------------------------------------------------------------------- advertised


def run_advertised(ctx: Context, rep: Report, window: float = 12.0) -> None:
    """Bootstrap through the decoy listener: metadata advertises a name that cannot resolve.

    Every client error in the window is classified and de-duplicated by cause, as the
    q feed does (src/etl/core/kafka_diagnostics.q): one report per cause, however many
    times librdkafka repeats it.
    """
    from confluent_kafka import Consumer  # ty: ignore[unresolved-import]

    def diagnosed() -> str:
        if not ctx.broker.decoy_bootstrap:
            raise Fail("this broker has no decoy listener: start it with `broker start --decoy`")
        events: list[tuple[Any, str]] = []
        cfg = client_config(
            ctx.broker, ctx.identity,
            **{"bootstrap.servers": ctx.broker.decoy_bootstrap, "group.id": f"{GROUP_1}-decoy",
               "enable.auto.commit": False,
               "error_cb": lambda e: events.append((e.name(), str(e.str())))},
        )  # fmt: skip
        consumer = Consumer(cfg)
        consumer.subscribe([TOPIC_A])
        deadline = time.monotonic() + window
        try:
            while time.monotonic() < deadline:
                consumer.poll(0.5)
        finally:
            consumer.close()
        causes: dict[tuple, Diagnosis] = {}
        for name, text in events:
            d = classify(name, text, bootstrap=(ctx.broker.decoy_bootstrap,))
            if d.kind == "all_down" and causes:
                continue  # the consequence of a cause already held, not a second cause
            causes.setdefault(d.cause, d)
        adv = [d for d in causes.values() if d.stage == "advertised"]
        if len(adv) != 1:
            raise Fail(
                f"expected one advertised-endpoint cause, found "
                f"{[c.cause for c in causes.values()]} from {len(events)} client errors"
            )
        d = adv[0]
        why = d.detail.split(": ", 1)[-1].split(" (")[0]
        return (
            f"advertised endpoint {d.endpoint} is unreachable ({why}); "
            f"reported ONCE for {len(events)} client errors in {window:.0f}s; bootstrap "
            f"{ctx.broker.decoy_bootstrap} itself answered"
        )

    rep.expect("advertised_unresolvable", diagnosed)
