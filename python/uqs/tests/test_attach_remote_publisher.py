"""A publisher on this machine attached to a stack on another (#1651).

The publisher runs where `uqs` starts it, so its running state is this
machine's; the stack's processes are asked where discovery lists them; and
the stack must be able to resolve the name the publisher registers under.
"""

from __future__ import annotations

import socket
from dataclasses import replace
from pathlib import Path

import pytest
from attach_support import make_bundle

from uqs.attach import preflight, publisher, remote, service
from uqs.attach.config import Attachment
from uqs.attach.facts import Facts, Host
from uqs.attach.remote import Endpoint
from uqs.attach.topology import Observation, Proc
from uqs.paths import repo_root


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    return make_bundle(tmp_path / "piggybank")


def _remote(bundle: Path, advertised: str = "") -> Attachment:
    att = service.create(
        "pb-uat", "pb_general_ledger", bundle, 6000, repo_root(),
        host="uat-box.example", runtime_only=True,
    )  # fmt: skip
    if advertised:
        att = replace(att, publisher=replace(att.publisher, advertised_host=advertised))
    return att


def _observed(resolved: str | None) -> Observation:
    probe: dict = {"services": []}
    if resolved is not None:
        probe["resolved"] = resolved
    return Observation(Proc("discovery", "discovery", Endpoint("discovery", "uat", 6001), probe))


#: A machine for which only its own names are local: uat-box.example is not.
HERE = Facts(host=Host(lambda h, p: False, ("localhost",)))


def test_the_publisher_registers_under_this_machines_name(bundle):
    assert _remote(bundle).publisher.advertised_host == socket.gethostname().lower()


def test_the_running_check_is_this_machines_not_the_stacks(bundle, monkeypatch):
    asked: list[tuple[str, int]] = []
    monkeypatch.setattr(
        publisher.planning, "publisher_port", lambda att, root: preflight.PublisherPort(6020)
    )
    pub = publisher.UqsPublisher(
        paths=None,  # ty: ignore[invalid-argument-type]
        host=Host(lambda h, p: asked.append((h, p)) or False),
    )
    assert not pub.running(_remote(bundle))
    assert asked == [("localhost", 6020)], "never the stack's host"


def test_a_loopback_name_is_refused_for_a_remote_stack(bundle):
    found = preflight.advertised(_remote(bundle, "localhost"), _observed("10.0.0.5"), HERE)
    assert [f.code for f in found] == ["ADVERTISED_LOOPBACK"]


def test_a_name_the_stack_cannot_resolve_is_refused(bundle):
    found = preflight.advertised(_remote(bundle, "laptop.local"), _observed(""), HERE)
    assert [f.code for f in found] == ["ADVERTISED_UNRESOLVABLE"]
    assert "cannot resolve laptop.local" in found[0].message


def test_a_name_the_stack_resolves_passes(bundle):
    assert preflight.advertised(_remote(bundle, "laptop.corp"), _observed("10.0.0.9"), HERE) == []


def test_a_stack_on_this_machine_needs_no_resolution(bundle):
    att = _remote(bundle, "localhost")
    att = replace(att, stack=replace(att.stack, host="localhost"))
    assert preflight.advertised(att, _observed(""), HERE) == []


def test_a_name_that_does_not_resolve_here_is_said_as_that():
    ipc = remote.KolaIpc("u", "p")
    with pytest.raises(remote.IpcError, match="no-such-host.invalid does not resolve"):
        ipc.call(Endpoint("stp1", "no-such-host.invalid", 6002), "probe", {"tables": []})
