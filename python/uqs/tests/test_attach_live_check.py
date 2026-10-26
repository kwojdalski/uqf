"""A start re-asks the stack whether an attached table is still registered (#1651).

The start gate (stack/attach_state.py) used to read only the saved state, so
after a remote tickerplant restarted - dropping a runtime-only registration -
a plain `uqs start` still published into a plant that no longer knew the
table. uqs.attach.live asks the tickerplant at every start of such a
publisher; these pin what it allows, what it refuses, and when it asks nothing.

Nothing here starts anything: every refusal but the attach gate is stubbed and
the gate is called directly, as runtime.run_torq_sh calls it.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from attach_support import FakeStack

from uqs.attach import config, live, service
from uqs.attach.facts import Host
from uqs.attach.manifest import Manifest
from uqs.paths import UqsError, default_paths, repo_root
from uqs.stack import attach_state, occupancy, qtree, start_policy

ROOT = repo_root()
PUBLISHER = "executions1"
STACK = 6000
#: This machine, where only its own names are local: uat-box.example is not.
HERE = Host(lambda h, p: False, ("localhost",))


@pytest.fixture(autouse=True)
def only_the_attach_gate(tmp_path: Path, monkeypatch):
    monkeypatch.setenv(config.ATTACH_DIR_ENV, str(tmp_path / "attachments"))
    monkeypatch.setattr(qtree, "refuse_unloadable", lambda *_a: None)
    monkeypatch.setattr(occupancy, "refuse_if_taken", lambda *_a: None)
    monkeypatch.setattr(live, "Host", lambda: HERE)


def verified(host: str, runtime_only: bool) -> config.Attachment:
    att = service.create(
        "ex-uat", "executions", None, STACK, ROOT, host=host, runtime_only=runtime_only
    )
    config.save(att)
    Manifest.new(att, Manifest.path_for(att.name)).set_state("verified", "set by the test")
    return att


def stack_holding(att: config.Attachment, monkeypatch) -> FakeStack:
    """A stack whose tickerplant registers every table `att` publishes."""
    fake = FakeStack(STACK)
    tp = fake.named("stp1")
    for name in (t.name for t in att.tables):
        tp["tables"].append(name)
        tp["published"].append(name)
    monkeypatch.setattr(live, "ipc_for", lambda _att: fake)
    return fake


def start() -> None:
    start_policy.refuse_start(default_paths(), ["start", PUBLISHER], STACK)


@pytest.mark.parametrize(
    ("host", "runtime_only"),
    [("uat-box.example", True), ("localhost", True)],
    ids=["remote stack", "runtime-only here"],
)
def test_a_registered_table_lets_the_publisher_start(monkeypatch, host, runtime_only):
    fake = stack_holding(verified(host, runtime_only), monkeypatch)
    start()
    assert fake.ops() == {"probe"}  # read-only
    assert {port for port, _, _ in fake.calls} == {STACK + 1, STACK}  # discovery, then stp1


@pytest.mark.parametrize("lost", ["published", "tables"])
def test_a_table_the_tickerplant_lost_refuses_the_start(monkeypatch, lost):
    att = verified("uat-box.example", True)
    fake = stack_holding(att, monkeypatch)
    gone = att.tables[0].name
    fake.named("stp1")[lost].remove(gone)
    with pytest.raises(UqsError) as caught:
        start()
    text = str(caught.value)
    assert f"{gone} is no longer registered on stp1" in text
    assert f"stack {STACK} at uat-box.example" in text
    assert "Re-apply with `uqs job attach ex-uat`" in text and "Nothing was started" in text


@pytest.mark.parametrize("down", ["discovery", "tickerplant"])
def test_a_stack_that_does_not_answer_refuses_the_start(monkeypatch, down):
    fake = stack_holding(verified("uat-box.example", True), monkeypatch)
    fake.down.add(STACK + 1 if down == "discovery" else STACK)
    with pytest.raises(UqsError) as caught:
        start()
    text = str(caught.value)
    assert f"still registered on stack {STACK} at uat-box.example cannot be told" in text
    assert "Connection refused" in text and "`uqs job attach ex-uat`" in text


def test_an_unset_password_variable_refuses_rather_than_passes(monkeypatch):
    att = verified("uat-box.example", True)
    config.save(replace(att, stack=replace(att.stack, password_env="UQS_TEST_NO_SUCH_PASSWORD")))
    monkeypatch.delenv("UQS_TEST_NO_SUCH_PASSWORD", raising=False)
    with pytest.raises(UqsError, match="cannot be told: the password variable"):
        start()


def test_a_schema_file_attachment_here_makes_no_call(monkeypatch):
    verified("localhost", False)

    def no_ipc(_att):
        raise AssertionError("a local schema-file attachment asked the stack")

    monkeypatch.setattr(live, "ipc_for", no_ipc)
    start()


def test_without_the_check_loaded_a_start_is_refused(monkeypatch):
    verified("localhost", False)
    monkeypatch.setattr(attach_state, "live_check", None)
    with pytest.raises(UqsError, match="uqs.attach.live is not loaded"):
        start()
