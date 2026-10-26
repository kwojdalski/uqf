"""The stack reaching a running publisher back, and what runtime-only costs it (#1651).

Preflight can check only the name the publisher registers under; whether the
stack can open its port is tried once it runs - in the workflow straight after
the start, and in verify when it is already up. Both ask discovery, with the
read-only `reach` operation, to open and close a handle to it.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from attach_support import FakeStack, make_bundle

from uqs.attach import config, facts, plan, reach, service, verify, workflow
from uqs.attach.config import Attachment
from uqs.attach.facts import Host
from uqs.attach.manifest import Manifest
from uqs.attach.remote import source
from uqs.paths import repo_root

ROOT = repo_root()
#: This machine, where only its own names are local: uat-box.example is not.
HERE = Host(lambda h, p: False, ("localhost",))
#: The same machine with the publisher listening on it.
LISTENING = Host(lambda h, p: True, ("localhost",))


class Publisher:
    def __init__(self, listens: bool = True) -> None:
        self.on, self.started, self.listens = False, 0, listens

    def running(self, att) -> bool:
        return self.on and self.listens

    def start(self, att) -> None:
        self.on, self.started = True, self.started + 1

    def stop(self, att) -> None:
        self.on = False


@pytest.fixture(autouse=True)
def no_real_ports(monkeypatch):
    monkeypatch.setattr(facts, "_tcp_open", lambda host, port: False)
    monkeypatch.setattr(reach, "START_WAIT_S", 0.0)


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    return make_bundle(tmp_path / "piggybank")


def _att(bundle: Path, host: str = "uat-box.example") -> Attachment:
    att = service.create(
        "pb-uat", "pb_general_ledger", bundle, 6000, ROOT, host=host, runtime_only=True
    )
    return replace(att, publisher=replace(att.publisher, advertised_host="laptop.corp"))


def _where(att: Attachment) -> str:
    return f"laptop.corp:{plan.publisher_port(att, ROOT).port}"


def _run(att: Attachment, fake: FakeStack, state: Path, publisher: Publisher, host=HERE):
    resolved = workflow.Resolved(att, "new", config.directory(att.name, state))
    return workflow.execute(
        resolved, plan.build(att, fake, ROOT), fake, ROOT, publisher, state_dir=state, host=host
    )


def _reach_calls(fake: FakeStack) -> list[tuple[int, dict]]:
    return [(port, req) for port, op, req in fake.calls if op == "reach"]


# ------------------------------------------------------------------- the check


def test_discovery_is_asked_to_reach_the_advertised_address(bundle):
    fake, att = FakeStack(6000), _att(bundle)
    assert reach.check(att, fake, 6020) is None
    assert _reach_calls(fake) == [
        (6001, {"host": "laptop.corp", "port": 6020, "timeout": reach.TIMEOUT_MS})
    ]


def test_an_address_the_stack_cannot_open_is_a_blocker_naming_it(bundle):
    fake, att = FakeStack(6000), _att(bundle)
    fake.unreachable["laptop.corp:6020"] = "timeout"
    found = reach.check(att, fake, 6020)
    assert found is not None and found.level == "blocker"
    assert found.code == "PUBLISHER_UNREACHABLE_FROM_STACK"
    assert "cannot connect back to pb_ledger1 at laptop.corp:6020: timeout" in found.message
    assert found.facts == {"address": "laptop.corp:6020"}


def test_a_discovery_that_cannot_be_asked_is_no_pass(bundle):
    fake, att = FakeStack(6000), _att(bundle)
    fake.down.add(6001)
    found = reach.check(att, fake, 6020)
    assert found is not None and "discovery could not be asked" in found.message


def test_a_just_started_publisher_is_retried_until_it_listens(bundle):
    fake, att, pub = FakeStack(6000), _att(bundle), Publisher()
    answers, now, slept = iter([False, False, True]), [0.0], []
    pub.running = lambda a: next(answers)  # ty: ignore[invalid-assignment]

    def sleep(s: float) -> None:
        slept.append(s)
        now[0] += s

    found = reach.after_start(att, fake, 6020, pub, wait_s=10, sleep=sleep, clock=lambda: now[0])
    assert found is None and slept == [1.0, 1.0] and len(_reach_calls(fake)) == 1


# ------------------------------------------------------------------ the workflow


def test_a_publisher_the_stack_reaches_is_attached(bundle, tmp_path):
    fake, att, pub = FakeStack(6000), _att(bundle), Publisher()
    out = _run(att, fake, tmp_path, pub)
    assert out.ok, out.stages
    assert [s.name for s in out.stages] == ["save", "apply", "verify", "start", "reach"]
    assert out.stages[-1].status == "done" and pub.on
    assert Manifest.load(att.name, tmp_path).state == "verified"
    assert [r["host"] for _, r in _reach_calls(fake)] == ["laptop.corp"]


def test_a_publisher_the_stack_cannot_reach_is_never_reported_attached(bundle, tmp_path):
    fake, att, pub = FakeStack(6000), _att(bundle), Publisher()
    fake.unreachable[_where(att)] = "hop. OS reports: Connection refused"
    out = _run(att, fake, tmp_path, pub)
    assert not out.ok
    last = out.stages[-1]
    assert (last.name, last.status) == ("reach", "failed")
    assert last.detail.startswith("PUBLISHER_UNREACHABLE_FROM_STACK: ")
    assert _where(att) in last.detail and "Connection refused" in last.detail
    assert not pub.on, "the publisher this run started is stopped again"
    assert out.recovery[0] == "pb_ledger1 was stopped"
    m = Manifest.load(att.name, tmp_path)
    assert m.state == "applied", "the start gate is closed again"


def test_a_publisher_that_never_listens_is_reported_as_that(bundle, tmp_path):
    fake, att = FakeStack(6000), _att(bundle)
    out = _run(att, fake, tmp_path, Publisher(listens=False))
    assert out.stages[-1].status == "failed"
    assert "is not listening on" in out.stages[-1].detail
    assert _reach_calls(fake) == [], "nothing to ask the stack to reach"


def test_a_stack_on_this_machine_is_not_asked(bundle, tmp_path):
    fake, att = FakeStack(6000), _att(bundle, host="localhost")
    out = _run(att, fake, tmp_path, Publisher())
    assert out.ok and [s.name for s in out.stages][-1] == "start"
    assert "reach" not in fake.ops()


# ------------------------------------------------------------------------ verify


def _applied(att: Attachment, fake: FakeStack, state: Path) -> None:
    out = _run(att, fake, state, Publisher(), host=HERE)
    assert out.ok, out.stages


def test_verify_asks_the_stack_to_reach_a_running_publisher(bundle, tmp_path):
    fake, att = FakeStack(6000), _att(bundle)
    _applied(att, fake, tmp_path)
    r = verify.verify(att, fake, tmp_path, ROOT, LISTENING)
    assert r.ok and f"the stack reaches pb_ledger1 at {_where(att)}" in [c.name for c in r.checks]

    fake.unreachable[_where(att)] = "timeout"
    r = verify.verify(att, fake, tmp_path, ROOT, LISTENING)
    failed = [c for c in r.checks if not c.ok]
    assert [c.name for c in failed] == [f"the stack reaches pb_ledger1 at {_where(att)}"]
    assert failed[0].detail.startswith("PUBLISHER_UNREACHABLE_FROM_STACK: ")
    assert r.state == "applied"


def test_verify_does_not_try_a_publisher_that_is_not_running(bundle, tmp_path):
    fake, att = FakeStack(6000), _att(bundle)
    _applied(att, fake, tmp_path)
    before = len(_reach_calls(fake))
    assert verify.verify(att, fake, tmp_path, ROOT, HERE).ok
    assert len(_reach_calls(fake)) == before


# ------------------------------------------------------------------ runtime-only


def test_runtime_only_on_a_remote_stack_says_the_re_apply_is_the_operators(bundle, tmp_path):
    fake, att = FakeStack(6000), _att(bundle)
    shown = plan.build(att, fake, ROOT, facts.Facts(host=HERE))
    (warned,) = [f.message for f in shown.findings if f.code == "RUNTIME_ONLY"]
    assert "restarting the tickerplant DROPS the registration" in warned
    assert "must be re-applied (`uqs job attach pb-uat`)" in warned
    assert "The stack is on uat-box.example" in warned

    out = _run(att, fake, tmp_path, Publisher())
    assert out.verified is not None and out.verified.warnings == [warned]
    verified = next(s for s in out.stages if s.name == "verify")
    assert warned in verified.detail


def test_runtime_only_on_this_machine_says_the_restart_without_the_remote_part(bundle):
    fake, att = FakeStack(6000), _att(bundle, host="localhost")
    shown = plan.build(att, fake, ROOT, facts.Facts(host=HERE))
    (warned,) = [f.message for f in shown.findings if f.code == "RUNTIME_ONLY"]
    assert "must be re-applied" in warned and "The stack is on" not in warned


# ---------------------------------------------------------------- reach.q itself

_REACH_Q = """\\p 0W
f:value "\\n" sv read0 hsym `$getenv`UQS_REACH_Q;
ask:{[port] -1 "OUT ",f .j.j `host`port`timeout!("localhost";port;2000)};
ask system"p";
ask "J"$getenv`UQS_FREE_PORT;
.servers.USERPASS:`admin:hunter2;
ask system"p";
exit 0
"""


def test_reach_q_opens_and_closes_a_handle_and_never_answers_the_credential(
    q_binary, tmp_path, unused_port
):
    q, env = q_binary
    (tmp_path / "reach.q").write_text(source("reach") + "\n")
    (tmp_path / "run.q").write_text(_REACH_Q)
    run = subprocess.run(
        [q, str(tmp_path / "run.q"), "-q"],
        env={**env, "UQS_REACH_Q": str(tmp_path / "reach.q"), "UQS_FREE_PORT": str(unused_port)},
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60, check=False,
    )  # fmt: skip
    lines = [line[4:] for line in run.stdout.splitlines() if line.startswith("OUT ")]
    assert len(lines) == 3, f"{run.stdout}{run.stderr}"
    itself, closed, credentialed = (json.loads(line) for line in lines)
    assert itself["reached"] is True and itself["error"] == ""
    assert closed["reached"] is False and closed["address"] == f"localhost:{unused_port}"
    assert closed["error"], "q's own reason is passed on"
    assert credentialed["reached"] is True and "hunter2" not in lines[2]
