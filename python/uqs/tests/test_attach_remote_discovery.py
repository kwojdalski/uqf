"""A publisher attached to a stack on another machine is told that stack's discovery (#1651).

TorQ finds discovery through its process.csv, whose discovery row is this
machine's. Started like any other process, such a publisher looked for discovery
here, found none, and never reached the stack's tickerplant, while its start
reported success. The remote-stack lane (tests/remote/) found it: every start of
it now carries `-extras -.servers.DISCOVERY :host:port`, whichever route started
it, and nothing else does.
"""

from __future__ import annotations

import contextlib
import subprocess
from pathlib import Path

import pytest

from uqs.attach import config, publisher, service
from uqs.paths import UqsError, default_paths, repo_root
from uqs.stack import generated, runtime, start_policy

ROOT = repo_root()
PUBLISHER = "executions1"


@pytest.fixture(autouse=True)
def state(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "attachments"
    monkeypatch.setenv(config.ATTACH_DIR_ENV, str(root))
    return root


def attach(host: str, **kw) -> config.Attachment:
    att = service.create("ex-uat", "executions", None, 6000, ROOT, host=host, **kw)
    config.save(att)
    return att


def extras(*args: str) -> list[str]:
    return start_policy.with_publisher_extras(default_paths(), list(args))


def test_a_publisher_of_a_remote_stack_is_pointed_at_its_discovery():
    attach("uat-stack", runtime_only=True)
    assert extras("start", PUBLISHER) == [
        "start", PUBLISHER, "-extras", "-.servers.DISCOVERY", ":uat-stack:6001",
    ]  # fmt: skip
    assert extras("restart", PUBLISHER)[-2:] == ["-.servers.DISCOVERY", ":uat-stack:6001"]


def test_the_discovery_named_by_the_attachment_is_the_one_passed():
    attach("uat-stack", discovery="uat-stack:6101", runtime_only=True)
    assert extras("start", PUBLISHER)[-1] == ":uat-stack:6101"


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1"])
def test_a_stack_on_this_machine_changes_nothing(host):
    attach(host)
    assert extras("start", PUBLISHER) == ["start", PUBLISHER]


def test_other_processes_and_other_verbs_are_left_alone():
    attach("uat-stack", runtime_only=True)
    assert extras("start", "rdb1") == ["start", "rdb1"]
    assert extras("stop", PUBLISHER) == ["stop", PUBLISHER]


def test_existing_extras_are_kept_and_extended():
    attach("uat-stack", runtime_only=True)
    assert extras("start", PUBLISHER, "-extras", "-x", "1") == [
        "start", PUBLISHER, "-extras", "-x", "1", "-.servers.DISCOVERY", ":uat-stack:6001",
    ]  # fmt: skip


def test_a_remote_publisher_started_with_others_is_refused():
    """-extras reaches every process of one torq.sh call: rdb1 must not be
    pointed at another machine's discovery."""
    attach("uat-stack", runtime_only=True)
    with pytest.raises(UqsError, match="start it on its own"):
        extras("start", f"{PUBLISHER} rdb1")


@pytest.mark.parametrize(
    ("host", "after"),
    [("localhost", []), ("uat-stack", ["-.servers.DISCOVERY", ":uat-stack:6001"])],
)
def test_a_traced_publisher_start_carries_trace_on_its_start_line(monkeypatch, host, after):
    """`job attachment add --trace` (#2687) starts the publisher as `uqs start
    --trace` does: `-trace` through torq.sh's -extras, which a remote stack's
    discovery then extends - one -extras, both on the publisher's start line."""
    att = attach(host, runtime_only=True)
    seen: list[list[str]] = []

    def launched(paths, args, *rest):
        seen.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(start_policy, "refuse_start", lambda *a, **kw: None)
    monkeypatch.setattr(runtime, "torq_launcher", lambda paths: Path("torq.sh"))
    monkeypatch.setattr(generated, "lifecycle_lock", lambda *a, **kw: contextlib.nullcontext())
    monkeypatch.setattr(runtime, "_bootstrap_and_run", launched)
    publisher.UqsPublisher(default_paths(), trace=True).start(att)
    publisher.UqsPublisher(default_paths()).start(att)
    assert seen == [
        ["start", PUBLISHER, "-extras", "-trace", *after],
        ["start", PUBLISHER, *(["-extras", *after] if after else [])],
    ]
