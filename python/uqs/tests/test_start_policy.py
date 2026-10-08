"""Every front end refuses what `uqs start` refuses (#887).

The refusals used to live in the CLI, so `uqs-mcp`'s `uqs_start` and the
browser's `POST /control/process/start` started what the CLI refused: torq.sh
exits 0 on a name that is not a process, so a typo "succeeded" and the HTTP
route answered `ok: true`. Here the real `runtime.start`/`stop`/`restart` are
called through each front end against this checkout's own process table, with
torq.sh replaced by a stub that fails the test if it is ever reached.
"""

from __future__ import annotations

import subprocess

import pytest

from uqs import mcp
from uqs import paths as stack_paths
from uqs.paths import UqsError
from uqs.stack import runtime, start_policy


@pytest.fixture
def no_torq(monkeypatch):
    """torq.sh must not run: a refusal happens before it."""

    def ran(*_a, **_k):
        pytest.fail("torq.sh was run for a start that should have been refused")

    monkeypatch.setattr(runtime, "run_torq_sh", ran)
    monkeypatch.setattr(runtime.occupancy, "refuse_if_taken", lambda *_a: None)


@pytest.mark.parametrize("verb", [runtime.start, runtime.stop, runtime.restart])
def test_the_stack_itself_refuses_an_unknown_name(no_torq, verb):
    with pytest.raises(UqsError, match="nosuchproc1"):
        verb(stack_paths.default_paths(), "rdb1 nosuchproc1")


def test_all_is_still_torq_shs_own_selector(monkeypatch):
    seen = []
    monkeypatch.setattr(runtime, "run_torq_sh", lambda _p, args, **_k: seen.append(args))
    monkeypatch.setattr(runtime.occupancy, "refuse_if_taken", lambda *_a: None)
    runtime.start(stack_paths.default_paths(), "all")
    assert seen == [["start", "all"]]


def test_the_mcp_server_refuses_an_unknown_name(no_torq):
    said = mcp.uqs_start("nosuchproc1")
    assert said.startswith("ERROR:") and "nosuchproc1" in said


def test_the_mcp_server_refuses_an_unknown_profile(no_torq):
    said = mcp.uqs_start(profile="nosuchprofile")
    assert said.startswith("ERROR:") and "nosuchprofile" in said


def test_the_mcp_server_starts_a_profile_as_the_cli_resolves_it(monkeypatch):
    seen = []

    def torq(_paths, args, **_k):
        seen.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(runtime, "run_torq_sh", torq)
    monkeypatch.setattr(runtime.occupancy, "refuse_if_taken", lambda *_a: None)
    mcp.uqs_start(profile="essential")
    members, _ = start_policy.resolve_profiles(stack_paths.default_paths(), "essential")
    assert seen == [["start", " ".join(members)]]


def test_the_control_api_refuses_an_unknown_name(no_torq):
    control = pytest.importorskip("uqf_frontend.control")
    from uqf_frontend.config import Settings
    from uqf_frontend.errors import ValidationFailed

    with pytest.raises(ValidationFailed, match="nosuchproc1"):
        control.lifecycle(Settings(enable_writes=True), "start", "nosuchproc1")


@pytest.mark.parametrize(
    ("names", "extra", "says"),
    [
        ("", (), "--profile needs at least one name"),
        ("essential", ("all",), "cannot be combined with `all`"),
        ("essential", ("nosuchproc1",), "nosuchproc1"),
    ],
)
def test_profile_resolution_refuses_with_a_reason(names, extra, says):
    with pytest.raises(UqsError, match=says):
        start_policy.resolve_profiles(stack_paths.default_paths(), names, extra)
