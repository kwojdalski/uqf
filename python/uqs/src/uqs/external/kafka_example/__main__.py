"""Command line: `python -m uqs.external.kafka_example <command>`.

  broker start [--secure] [--decoy] [--advertised-host H]   start a disposable broker
  broker stop --dir DIR                                    stop it and remove its directory
  publish --dir DIR                                        publish the known batch
  verify PHASE --dir DIR --rdb-port N --stp-port N         verify one phase

PHASE is basic, restart, redelivery, groups, denied or advertised, or `all` for
the first four in order. Exit status is 0 only when every stage printed PASS.
docs/guides/kafka-crypto-example.md is the walkthrough.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from uqs.external.kafka_example import broker as bk
from uqs.external.kafka_example import kclient, phases, refusals, verify
from uqs.external.kafka_example.batch import TOPICS, initial_batch
from uqs.external.kafka_example.feeds import Feed
from uqs.external.kafka_example.kclient import GROUP_1
from uqs.external.kafka_example.report import Report
from uqs.paths import UqsError
from uqs.stack import kafka_extra

DEFAULT_CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "uqs" / "kafka"


def _broker_start(a: argparse.Namespace) -> int:
    b = bk.start(
        cache=Path(a.cache), advertised_host=a.advertised_host, secure=a.secure, decoy=a.decoy,
        base=Path(a.base) if a.base else None,
    )  # fmt: skip
    try:
        kclient.create_topics(b)
        roles = kclient.grant(b) if a.secure else {}
    except Exception:
        bk.stop(b)
        raise
    print(f"directory   {b.directory}")
    print(f"bootstrap   {b.bootstrap}")
    print(f"advertised  {b.advertised}   (what the broker tells clients to connect to next)")
    if b.decoy_bootstrap:
        print(f"decoy       {b.decoy_bootstrap}   (its metadata advertises {bk.UNRESOLVABLE_HOST})")
    print(f"topics      {', '.join(TOPICS)}")
    for who, may in roles.items():
        print(f"identity    {who:<8} {may}")
    if a.secure:
        print(f"credentials {kclient.credentials_file(b)}   (0600; passwords are never printed)")
    return 0


def _broker_stop(a: argparse.Namespace) -> int:
    bk.stop(bk.Broker.load(Path(a.dir)))
    print(f"stopped and removed {a.dir}")
    return 0


def _context(a: argparse.Namespace, group: str = GROUP_1) -> verify.Context:
    b = bk.Broker.load(Path(a.dir))
    feed = Feed(
        a.feed, b, Path(a.repo_root), a.stp_port, TOPICS, group,
        uqs=a.uqs.split() if a.uqs else [], kfk_home=os.environ.get("UQF_KFK_HOME", ""),
    )  # fmt: skip
    return verify.Context(b, a.rdb_port, feed, a.timeout, a.identity)


def _publish(a: argparse.Namespace) -> int:
    ctx = _context(a)
    ctx.remember(verify.publish(ctx.broker, initial_batch(), identity=ctx.identity))
    print(f"published {len(ctx.delivered)} records; coordinates saved in {ctx.store}")
    return 0


def _verify(a: argparse.Namespace) -> int:
    ctx, rep = _context(a), Report()
    ctx.delivered = []
    order = ["basic", "restart", "redelivery", "groups"] if a.phase == "all" else [a.phase]
    try:
        for phase in order:
            print(f"== {phase}")
            if phase == "basic":
                verify.run_basic(ctx, rep)
            else:
                ctx.delivered = [] if phase != "basic" else ctx.delivered
                _replay_memo(ctx)
                if phase == "restart":
                    phases.run_restart(ctx, rep)
                elif phase == "redelivery":
                    phases.run_redelivery(ctx, rep, a.stp_port)
                elif phase == "groups":
                    phases.run_groups(ctx, rep, ctx.identity if ctx.broker.secure else None)
                elif phase == "denied":
                    refusals.run_denied(ctx, rep)
                elif phase == "advertised":
                    refusals.run_advertised(ctx, rep)
    finally:
        if a.stop_feed and ctx.feed.kind == "python":
            ctx.feed.stop()
    print(rep.summary())
    return 0 if rep.ok else 1


def _replay_memo(ctx: verify.Context) -> None:
    """Re-read the delivered coordinates a previous run saved, so phases run one by one."""
    ctx.delivered = []
    memo = ctx.recall()
    from uqs.external.kafka_example.batch import Record
    from uqs.external.kafka_example.publisher import Delivered

    for m in memo:
        rec = Record(
            m["topic"],
            m["partition"],
            m["key"],
            m["value"].encode() if m["value"] is not None else None,
            m["expect"],
            m["trade"],
        )
        ctx.delivered.append(Delivered(rec, m["partition"], m["offset"]))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="kafka_example",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("broker").add_subparsers(dest="action", required=True)
    s = b.add_parser("start")
    s.add_argument(
        "--cache", default=str(DEFAULT_CACHE), help="where the Kafka distribution is unpacked"
    )
    s.add_argument("--base", help="parent of the scratch directory (default: the system temp dir)")
    s.add_argument(
        "--advertised-host",
        default="127.0.0.1",
        help="the host the broker tells clients to connect to",
    )
    s.add_argument(
        "--secure",
        action="store_true",
        help="SASL/PLAIN with a deny-by-default authorizer and four disposable identities",
    )
    s.add_argument(
        "--decoy", action="store_true", help="add a listener advertising an unresolvable name"
    )
    s.set_defaults(fn=_broker_start)
    t = b.add_parser("stop")
    t.add_argument("--dir", required=True)
    t.set_defaults(fn=_broker_stop)
    for name, fn in (("publish", _publish), ("verify", _verify)):
        c = sub.add_parser(name)
        if name == "verify":
            c.add_argument(
                "phase",
                choices=["all", "basic", "restart", "redelivery", "groups", "denied", "advertised"],
            )
            c.add_argument(
                "--stop-feed", action="store_true", help="stop the python feed when done"
            )
        c.add_argument("--dir", required=True, help="the broker directory `broker start` printed")
        c.add_argument("--rdb-port", type=int, default=6552)
        c.add_argument("--stp-port", type=int, default=6550)
        c.add_argument("--feed", choices=["python", "native"], default="python")
        c.add_argument("--uqs", default="", help="the uqs command prefix, for --feed native")
        c.add_argument("--repo-root", default=os.getcwd())
        c.add_argument("--timeout", type=float, default=60.0, help="seconds each wait may take")
        c.add_argument("--identity", default=None, help="the SASL identity, for a --secure broker")
        c.set_defaults(fn=fn)
    a = p.parse_args(argv)
    try:
        if a.fn is not _broker_stop:  # every other command talks to the broker
            kafka_extra.require("python -m uqs.external.kafka_example")
        return a.fn(a)
    except UqsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
