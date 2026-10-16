"""`uqs job attach apply | verify | start | status` as an operator runs them (#1424).

The stack is a scripted one that keeps state (attach_support.FakeStack); what is
under test is what the commands ask, print, refuse and exit with.
"""

from __future__ import annotations

import json

import pytest
from attach_support import FakeStack, make_bundle
from typer.testing import CliRunner

from uqs import cli
from uqs.attach import config, facts, publisher, service
from uqs.cli import shared

TABLE = "piggybank_general_ledger"


class _ErrorLog:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def error(self, message: str, *args) -> None:
        self.messages.append(str(message).format(*args))

    def __getattr__(self, _name: str):
        return lambda *a, **kw: None


@pytest.fixture
def world(monkeypatch, tmp_path):
    fake = FakeStack(6000)
    monkeypatch.setenv(config.ATTACH_DIR_ENV, str(tmp_path / "attachments"))
    monkeypatch.setattr(service, "ipc_for", lambda att: fake)
    monkeypatch.setattr(facts, "_tcp_open", lambda host, port: False)
    errors = _ErrorLog()
    monkeypatch.setattr(shared, "log", errors)
    fake.errors = errors.messages
    monkeypatch.setattr(
        publisher.UqsPublisher,
        "start",
        lambda self, att: fake.started.append(att.publisher.procname),
    )
    bundle = make_bundle(tmp_path / "piggybank")
    plan = CliRunner().invoke(
        cli.app,
        ["job", "attach", "plan", "pb-uat", "--job", "pb_general_ledger", "--bundle", str(bundle)]
        + ["--stack-id", "6000", "--runtime-only", "--json"],
        catch_exceptions=False,
    )
    assert plan.exit_code == 0, plan.output
    return fake


def invoke(*argv: str, **kw):
    return CliRunner().invoke(cli.app, ["job", "attach", *argv], catch_exceptions=False, **kw)


def test_apply_asks_first_and_changes_nothing_on_no(world):
    result = invoke("apply", "pb-uat", input="n\n")
    assert result.exit_code != 0
    assert "Change stack 6000 as above? (no process is restarted)" in result.output
    assert not [op for _, op, _ in world.calls if op != "probe"]
    assert "Changes apply would make" in result.output, "it shows the plan before it asks"


def test_apply_yes_changes_the_stack_and_says_the_publisher_is_still_stopped(world):
    result = invoke("apply", "pb-uat", "--yes")
    assert result.exit_code == 0, result.output
    out = " ".join(result.output.split())
    assert "stp_register" in out and "consumer_attach" in out and "applied" in out
    assert "The publisher (pb_ledger1) is still stopped" in out
    assert world.named("stp1")["subscribers"][TABLE] == 2 and world.started == []


def test_apply_on_a_blocked_stack_is_refused_with_the_reason(world):
    world.named("stp1")["multilog"] = "singular"
    result = invoke("apply", "pb-uat", "--yes")
    assert result.exit_code == 1
    assert any("LOGGING_MODE" in m for m in world.errors)
    assert not [op for _, op, _ in world.calls if op != "probe"]


def test_verify_passes_exit_zero_and_a_broken_stack_exits_one(world):
    invoke("apply", "pb-uat", "--yes")
    ok = invoke("verify", "pb-uat")
    assert ok.exit_code == 0 and "verified" in ok.output
    world.named("rdb1")["subscribed"].remove(TABLE)
    bad = invoke("verify", "pb-uat", "--json")
    assert bad.exit_code == 1
    data = json.loads(bad.output)
    assert data["ok"] is False and data["state"] == "applied"
    assert any(f"subscribed to {TABLE}" in c["name"] and not c["ok"] for c in data["checks"])


def test_verify_before_apply_says_so(world):
    result = invoke("verify", "pb-uat")
    assert result.exit_code == 1 and any("has not been applied" in m for m in world.errors)


def test_start_verifies_first_and_starts_nothing_on_a_stack_that_no_longer_matches(world):
    invoke("apply", "pb-uat", "--yes")
    world.named("wdb1")["subscribed"].remove(TABLE)
    refused = invoke("start", "pb-uat")
    assert refused.exit_code == 1 and world.started == []
    assert any("no longer verifies" in m for m in world.errors)
    world.named("wdb1")["subscribed"].append(TABLE)
    ok = invoke("start", "pb-uat")
    assert ok.exit_code == 0 and world.started == ["pb_ledger1"]
    assert (
        json.loads(invoke("status", "pb-uat", "--json").output)["manifest"]["state"] == "verified"
    )


def test_status_shows_the_manifest_and_a_registration_the_stack_lost(world):
    invoke("apply", "pb-uat", "--yes")
    status = json.loads(invoke("status", "pb-uat", "--json").output)
    assert status["manifest"]["state"] == "applied" and status["health"] == []
    assert [o["status"] for o in status["manifest"]["operations"]] == ["done"] * 3
    # a tickerplant restart without a persisted schema forgets the table
    stp = world.named("stp1")
    stp["schemas"].pop(TABLE)
    stp["owned"].pop(TABLE)
    lost = json.loads(invoke("status", "pb-uat", "--json").output)
    assert any(
        line.startswith("registration-lost: stp1 no longer holds") for line in lost["health"]
    )
    human = invoke("status", "pb-uat")
    assert "registration-lost" in human.output and "Manifest: applied" in human.output


def test_the_real_publisher_is_started_under_the_attachments_stack_id_never_the_default(
    monkeypatch, tmp_path
):
    """What `uqs job attach start` hands the launcher: the attachment's base port,
    explicitly - the one thing the incident got wrong."""
    from types import SimpleNamespace

    from uqs.attach import service as svc
    from uqs.paths import default_paths
    from uqs.stack import runtime

    bundle = make_bundle(tmp_path / "pb")
    att = svc.create("pb-uat", "pb_general_ledger", bundle, 6000, default_paths().repo_root)
    seen: list[tuple] = []

    def fake(paths, names, base_port=None, **kw):
        seen.append((names, base_port, kw))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(runtime, "start", fake)
    monkeypatch.setattr(runtime, "stop", fake)
    pub = publisher.UqsPublisher(default_paths())
    pub.start(att)
    pub.stop(att)
    assert [(n, p) for n, p, _ in seen] == [("pb_ledger1", 6000), ("pb_ledger1", 6000)]

    monkeypatch.setattr(
        runtime, "start", lambda *a, **k: SimpleNamespace(returncode=3, stdout="x", stderr="boom")
    )
    with pytest.raises(config.AttachmentError, match=r"did not start \(exit 3\): xboom"):
        pub.start(att)
