"""Credentials checked in both directions, each refusal said as itself (#1651).

Three credentials meet when a publisher here publishes to a stack elsewhere:

  * the attachment's (`--user`, `--password-env`), which uqs asks the stack
    with: refused -> STACK_REFUSES_ATTACH_CREDENTIALS (kola's authentication
    failure), not DISCOVERY_UNREACHABLE or UNREACHABLE;
  * the publisher's own TorQ credential, which it publishes with: refused by
    discovery or the tickerplant -> STACK_REFUSES_PUBLISHER_CREDENTIALS;
  * the stack's, which discovery connects back with: refused by the publisher
    -> PUBLISHER_REFUSES_STACK_CREDENTIALS.

q's word for a refused credential is `access`; anything else is still an
unreachable address.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from attach_support import FakeStack, make_bundle

from uqs.attach import config, facts, overrides, plan, reach, service, verify, workflow
from uqs.attach.config import Attachment
from uqs.attach.facts import Host
from uqs.attach.manifest import Manifest
from uqs.attach.remote import Endpoint
from uqs.paths import repo_root

ROOT = repo_root()
HERE = Host(lambda h, p: False, ("localhost",))
LISTENING = Host(lambda h, p: True, ("localhost",))
CODES = (
    "STACK_REFUSES_ATTACH_CREDENTIALS",
    "STACK_REFUSES_PUBLISHER_CREDENTIALS",
    "STACK_UNREACHABLE_FROM_PUBLISHER",
    "PUBLISHER_REFUSES_STACK_CREDENTIALS",
)


class Publisher:
    def __init__(self) -> None:
        self.on = False

    def running(self, att) -> bool:
        return self.on

    def start(self, att) -> None:
        self.on = True

    def stop(self, att) -> None:
        self.on = False


@pytest.fixture(autouse=True)
def no_real_ports(monkeypatch):
    monkeypatch.setattr(facts, "_tcp_open", lambda host, port: False)
    monkeypatch.setattr(reach, "START_WAIT_S", 0.0)


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    return make_bundle(tmp_path / "piggybank")


def _att(bundle: Path) -> Attachment:
    att = service.create(
        "pb-uat", "pb_general_ledger", bundle, 6000, ROOT, host="uat-box.example",
        runtime_only=True, user="feeds", password_env="UAT_TORQ_PASSWORD",
    )  # fmt: skip
    return replace(att, publisher=replace(att.publisher, advertised_host="laptop.corp"))


def _port(att: Attachment) -> int:
    port = plan.publisher_port(att, ROOT).port
    assert port is not None
    return port


def _run(att: Attachment, fake: FakeStack, state: Path, pub: Publisher):
    resolved = workflow.Resolved(att, "new", config.directory(att.name, state))
    return workflow.execute(
        resolved, plan.build(att, fake, ROOT), fake, ROOT, pub, state_dir=state, host=HERE
    )


def test_every_credential_code_is_a_refusal_no_acceptance_lifts():
    for code in CODES:
        assert not overrides.POLICY[code].overridable, code


# ------------------------------------------------- the attachment's credential


def test_discovery_refusing_the_attachments_credential_is_not_unreachable(bundle):
    fake, att = FakeStack(6000), _att(bundle)
    fake.refuse.add(6001)
    shown = plan.build(att, fake, ROOT, facts.Facts(host=HERE))
    (found,) = [f for f in shown.findings if f.level == "blocker"]
    assert found.code == "STACK_REFUSES_ATTACH_CREDENTIALS" and found.component == "discovery"
    assert "refuses the attachment's credential (user feeds, password from " in found.message
    assert "$UAT_TORQ_PASSWORD" in found.message and "the process is up" in found.action


def test_a_tickerplant_refusing_it_is_named_and_not_called_down(bundle):
    fake, att = FakeStack(6000), _att(bundle)
    fake.refuse.add(6000)
    shown = plan.build(att, fake, ROOT, facts.Facts(host=HERE))
    codes = {(f.code, f.component) for f in shown.findings if f.level == "blocker"}
    assert ("STACK_REFUSES_ATTACH_CREDENTIALS", "stp1") in codes
    assert not any(code == "UNREACHABLE" for code, _ in codes)


def test_a_process_that_is_down_is_still_unreachable(bundle):
    fake, att = FakeStack(6000), _att(bundle)
    fake.down.add(6000)
    shown = plan.build(att, fake, ROOT, facts.Facts(host=HERE))
    assert ("UNREACHABLE", "stp1") in {(f.code, f.component) for f in shown.findings}


# ---------------------------------------------- the publisher's, to the stack


def test_the_publisher_is_asked_with_uqs_local_credential_to_reach_both(bundle):
    fake, att = FakeStack(6000), _att(bundle)
    tp = Endpoint("stp1", "uat-box.example", 6000)
    assert reach.outbound(att, fake, _port(att), tp) is None
    asked = [(e, r["host"], r["port"]) for e, _, r in fake.publisher_calls]
    me = Endpoint("pb_ledger1", "localhost", _port(att), local=True)
    assert asked == [(me, "uat-box.example", 6001), (me, "uat-box.example", 6000)]


def test_the_stack_refusing_the_publishers_credential_is_said_as_that(bundle):
    fake, att = FakeStack(6000), _att(bundle)
    fake.publisher_cannot["uat-box.example:6000"] = "access"
    tp = Endpoint("stp1", "uat-box.example", 6000)
    found = reach.outbound(att, fake, _port(att), tp)
    assert found is not None and found.code == "STACK_REFUSES_PUBLISHER_CREDENTIALS"
    assert "stp1 at uat-box.example:6000 refuses the credential pb_ledger1 connects with" in (
        found.message
    )
    assert found.facts == {"address": "uat-box.example:6000", "process": "stp1"}


def test_a_stack_the_publisher_cannot_open_is_unreachable_from_it(bundle):
    fake, att = FakeStack(6000), _att(bundle)
    fake.publisher_cannot["uat-box.example:6001"] = "timeout"
    found = reach.outbound(att, fake, _port(att), None)
    assert found is not None and found.code == "STACK_UNREACHABLE_FROM_PUBLISHER"
    assert found.message.endswith("cannot connect to discovery at uat-box.example:6001: timeout")


def test_a_publisher_that_refuses_uqs_cannot_vouch_for_the_path(bundle):
    fake, att = FakeStack(6000), _att(bundle)
    fake.publisher_error = "the credential of user 'admin' was refused"
    found = reach.outbound(att, fake, _port(att), None)
    assert found is not None and found.code == "STACK_UNREACHABLE_FROM_PUBLISHER"
    assert "pb_ledger1 could not be asked: the credential of user 'admin'" in found.message


# --------------------------------------------- the stack's, to the publisher


def test_the_publisher_refusing_the_stacks_credential_is_said_as_that(bundle):
    fake, att = FakeStack(6000), _att(bundle)
    fake.unreachable[f"laptop.corp:{_port(att)}"] = "access"
    found = reach.check(att, fake, _port(att))
    assert found is not None and found.code == "PUBLISHER_REFUSES_STACK_CREDENTIALS"
    assert "refuses the credential the stack on uat-box.example connects with" in found.message
    assert "access list" in found.action


# ------------------------------------------------------------ in the workflow


@pytest.mark.parametrize(
    ("direction", "code"),
    [
        ("out", "STACK_REFUSES_PUBLISHER_CREDENTIALS"),
        ("back", "PUBLISHER_REFUSES_STACK_CREDENTIALS"),
    ],
)
def test_a_refused_credential_after_the_start_stops_the_publisher(
    bundle, tmp_path, direction, code
):
    fake, att, pub = FakeStack(6000), _att(bundle), Publisher()
    if direction == "out":
        fake.publisher_cannot["uat-box.example:6001"] = "access"
    else:
        fake.unreachable[f"laptop.corp:{_port(att)}"] = "access"
    out = _run(att, fake, tmp_path, pub)
    last = out.stages[-1]
    assert (last.name, last.status) == ("reach", "failed") and last.detail.startswith(code)
    assert not pub.on and Manifest.load(att.name, tmp_path).state == "applied"


def test_verify_reports_each_direction_on_its_own(bundle, tmp_path):
    fake, att, pub = FakeStack(6000), _att(bundle), Publisher()
    assert _run(att, fake, tmp_path, pub).ok
    # the tickerplant at the address discovery lists for it (FakeStack: testhost)
    fake.publisher_cannot["testhost:6000"] = "access"
    r = verify.verify(att, fake, tmp_path, ROOT, LISTENING)
    by = {c.name: c for c in r.checks}
    out = by["pb_ledger1 reaches the stack with its own credential"]
    back = by[f"the stack reaches pb_ledger1 at laptop.corp:{_port(att)}"]
    assert not out.ok and out.detail.startswith("STACK_REFUSES_PUBLISHER_CREDENTIALS: ")
    assert back.ok
    assert r.state == "applied"
