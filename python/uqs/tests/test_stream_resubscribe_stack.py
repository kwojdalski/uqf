"""A streaming job resubscribes after a tickerplant restart (#2201).

Opt-in (`-m live_stack`): a real stack on the DEFAULT uqf runtime, with
discovery, stp1 and the executions normalizer. stp1 is stopped and started
again under the running job. Before the fix `.sub.AUTORECONNECT` was 0b, so
the job's subscription stayed inactive for good while the process stayed up and
beat as healthy. Now TorQ's own `.sub.autoreconnect` resubscribes it, replaying
what the plant logged between coming back and the resubscription, once.

The scratch stack's base port is `UQS_STACK_BASE_PORT` when set.
"""

from __future__ import annotations

import os
import time

import pytest
from attach_stack_support import IsolatedStack, pick_base_port, requires_stack

pytestmark = requires_stack()

#: One order, filled: executions1 turns each into one fill.
ORDER = (
    "{[id] .u.upd[`orders;(enlist id;enlist `EURUSD;enlist `london;enlist `spot;"
    "enlist 1;enlist 1e6;enlist 1.085;enlist `filled)]}"
)


def _until(check, seconds: float, what: str):
    deadline = time.monotonic() + seconds
    while True:
        value = check()
        if value:
            return value
        if time.monotonic() > deadline:
            pytest.fail(f"{what} did not happen in {seconds}s")
        time.sleep(1)


def test_a_restarted_tickerplant_is_resubscribed_and_nothing_is_lost_or_doubled(tmp_path):
    base = int(os.environ.get("UQS_STACK_BASE_PORT") or pick_base_port())
    st = IsolatedStack(tmp_path, base_port=base)
    # the default uqf runtime, not the attach tests' starter pack as it ships
    st.env.pop("UQS_RUNTIME", None)
    st.env.pop("TORQAPPHOME", None)
    try:
        st.start("discovery1", "stp1")
        run = st.uqs("start", "executions1", "--base-port", str(base))
        assert run.returncode == 0, run.stdout + run.stderr
        executions = base + 36

        def q(expr, *args):
            import kola

            conn = kola.Q("127.0.0.1", executions, user="admin", passwd="admin", timeout=15000)
            try:
                return conn.sync(expr, *args)
            finally:
                conn.disconnect()

        def received():
            try:
                return int(q("0^.qtorq.received`orders"))
            except Exception:
                return 0

        def published():
            try:
                return int(q("0^.qtorq.published`executions"))
            except Exception:
                return 0

        _until(lambda: q("1b") is not None, 90, "executions1 answering")
        _until(
            lambda: q("0<count select from .sub.SUBSCRIPTIONS where active"),
            60,
            "executions1 subscribed",
        )
        st.q("stp1", ORDER, 1)
        _until(lambda: received() == 1, 30, "order 1 received")

        st.stop("stp1")
        # the job is up and beating, but its subscription is inactive, and it says so
        _until(
            lambda: not q("all exec active from .sub.SUBSCRIPTIONS"), 30, "subscription inactive"
        )
        st.start("stp1")
        # the outage's message: logged by the new plant before the job resubscribes
        st.q("stp1", ORDER, 2)
        _until(lambda: q("all exec active from .sub.SUBSCRIPTIONS"), 90, "resubscription")
        _until(lambda: received() == 2, 30, "order 2 replayed")
        assert q("`boolean$.sub.AUTORECONNECT")
        st.q("stp1", ORDER, 3)
        _until(lambda: received() == 3, 30, "order 3 received live")
        time.sleep(5)  # a duplicate would have arrived by now
        assert received() == 3
        assert published() == 3, "each order produced exactly one fill"
        assert int(q("count .qetl.job.stream.quarantined")) == 0
    finally:
        st.teardown()


def test_a_job_with_a_dead_subscription_is_reported_failing_at_its_next_beat(tmp_path):
    base = int(os.environ.get("UQS_STACK_BASE_PORT") or pick_base_port())
    st = IsolatedStack(tmp_path, base_port=base)
    st.env.pop("UQS_RUNTIME", None)
    st.env.pop("TORQAPPHOME", None)
    try:
        st.start("discovery1", "stp1")
        run = st.uqs("start", "executions1", "--base-port", str(base))
        assert run.returncode == 0, run.stdout + run.stderr
        executions = base + 36
        import kola

        def q(expr):
            conn = kola.Q("127.0.0.1", executions, user="admin", passwd="admin", timeout=15000)
            try:
                return conn.sync(expr)
            finally:
                conn.disconnect()

        _until(lambda: q("0<count select from .sub.SUBSCRIPTIONS where active"), 90, "subscribed")
        before = int(q(".qetl.stream_health.of[`executions]`failed"))
        st.stop("stp1")
        # uptime beats every minute; the first beat after the loss counts it
        _until(
            lambda: int(q(".qetl.stream_health.of[`executions]`failed")) > before,
            150,
            "a failed beat for the dead subscription",
        )
        assert "not subscribed" in str(q("string .qetl.stream_health.of[`executions]`last_error"))
        st.start("stp1")
        _until(lambda: q("all exec active from .sub.SUBSCRIPTIONS"), 90, "resubscription")
        # the outage is a gap in the job's uptime, which `uqs job gaps` reports
        assert int(q("count .qetl.uptime.gaps[`executions;.z.p-0D00:30;.z.p]")), (
            "the outage is a gap"
        )
    finally:
        st.teardown()


def test_a_resubscription_that_fails_once_is_retried_and_delivers_once(tmp_path):
    """The first resubscription throws; the retry tick runs the second (#2214)."""
    base = int(os.environ.get("UQS_STACK_BASE_PORT") or pick_base_port())
    st = IsolatedStack(tmp_path, base_port=base)
    st.env.pop("UQS_RUNTIME", None)
    st.env.pop("TORQAPPHOME", None)
    try:
        st.start("discovery1", "stp1")
        run = st.uqs("start", "executions1", "--base-port", str(base))
        assert run.returncode == 0, run.stdout + run.stderr
        executions = base + 36
        import kola

        def q(expr):
            conn = kola.Q("127.0.0.1", executions, user="admin", passwd="admin", timeout=15000)
            try:
                return conn.sync(expr)
            finally:
                conn.disconnect()

        def received():
            try:
                return int(q("0^.qtorq.received`orders"))
            except Exception:
                return 0

        _until(lambda: q("0<count select from .sub.SUBSCRIPTIONS where active"), 90, "subscribed")
        # the plant is "not ready" for the first resubscription only
        q(
            "{.sub.orig:.sub.subscribe; .sub.calls:0; .sub.subscribe:{[t;i;s;r;p] "
            '.sub.calls+:1; if[.sub.calls=1; \'"plant not ready"]; .sub.orig[t;i;s;r;p]}; 1b}[]'
        )
        st.q("stp1", ORDER, 1)
        _until(lambda: received() == 1, 30, "order 1 received")
        st.stop("stp1")
        _until(
            lambda: not q("all exec active from .sub.SUBSCRIPTIONS"), 30, "subscription inactive"
        )
        st.start("stp1")
        st.q("stp1", ORDER, 2)  # the outage's message
        # the beat says failing once the first resubscription has thrown
        _until(lambda: int(q(".sub.calls")) >= 1, 90, "first resubscription attempted")
        _until(lambda: q("all exec active from .sub.SUBSCRIPTIONS"), 150, "retried resubscription")
        assert int(q(".sub.calls")) >= 2, "failed once, succeeded on a retry"
        _until(lambda: received() == 2, 30, "order 2 replayed")
        time.sleep(5)  # a duplicate would have arrived by now
        assert received() == 2
        assert int(q("count .qetl.job.stream.quarantined")) == 0
    finally:
        st.teardown()
