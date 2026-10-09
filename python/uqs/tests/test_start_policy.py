"""Every front end refuses what `uqs start` refuses (#887).

The refusals used to live in the CLI, so `uqs-mcp`'s `uqs_start` and the
browser's `POST /control/process/start` started what the CLI refused: torq.sh
exits 0 on a name that is not a process, so a typo "succeeded" and the HTTP
route answered `ok: true`. Here the real `runtime.start`/`stop`/`restart` are
called through each front end against this checkout's own process table, with
torq.sh replaced by a stub that fails the test if it is ever reached.
"""

from __future__ import annotations

import dataclasses
import subprocess
from datetime import UTC, datetime

import pytest

from uqs import interpreter, mcp
from uqs import paths as stack_paths
from uqs.paths import UqsError
from uqs.stack import backfill, occupancy, runtime, start_policy


@pytest.fixture
def no_torq(monkeypatch):
    """torq.sh must not run: a refusal happens before it."""

    def ran(*_a, **_k):
        pytest.fail("torq.sh was run for a start that should have been refused")

    # The launcher, not run_torq_sh: the refusals live inside run_torq_sh (#964).
    monkeypatch.setattr(runtime, "torq_launcher", ran)
    monkeypatch.setattr(occupancy, "refuse_if_taken", lambda *_a: None)


@pytest.mark.parametrize("verb", [runtime.start, runtime.stop, runtime.restart])
def test_the_stack_itself_refuses_an_unknown_name(no_torq, verb):
    with pytest.raises(UqsError, match="nosuchproc1"):
        verb(stack_paths.default_paths(), "rdb1 nosuchproc1")


def test_all_is_still_torq_shs_own_selector(monkeypatch):
    seen = []
    monkeypatch.setattr(runtime, "run_torq_sh", lambda _p, args, **_k: seen.append(args))
    monkeypatch.setattr(occupancy, "refuse_if_taken", lambda *_a: None)
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
    monkeypatch.setattr(occupancy, "refuse_if_taken", lambda *_a: None)
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


# ------------------------------------------------- one choke point (#964)

FROM, TO = datetime(2026, 9, 13, tzinfo=UTC), datetime(2026, 9, 15, tzinfo=UTC)

#: Every way into torq.sh that starts q processes: `uqs start`, `uqs restart`,
#: `uqs backfill`, and `uqs raw -- start ...` (run_torq_sh as given).
STARTERS = {
    "start": lambda p: runtime.start(p, "rdb1"),
    "restart": lambda p: runtime.restart(p, "rdb1"),
    "backfill": lambda p: backfill.start(p, "demo_deals_backfill", "v1", FROM, TO),
    "raw": lambda p: runtime.run_torq_sh(p, ["start", "rdb1"]),
}


@pytest.fixture
def old_q(monkeypatch, tmp_path):
    """A source-tree KDB-X runtime whose q answers 4.1 - too old for nested `\\d`."""
    paths = stack_paths.default_paths()
    decl = dataclasses.replace(paths.runtime_declaration, q_tree="source")
    monkeypatch.setattr(type(paths), "runtime_declaration", property(lambda _s: decl))
    q = tmp_path / "q"
    q.write_text("#!/bin/sh\necho '4.1'\n")
    q.chmod(0o755)
    monkeypatch.setenv("QCMD", str(q))
    monkeypatch.delenv("UQF_Q_IMPL", raising=False)
    interpreter.q_version.cache_clear()
    yield paths
    interpreter.q_version.cache_clear()


@pytest.mark.parametrize("how", sorted(STARTERS))
def test_every_starter_refuses_a_q_too_old_for_the_tree(no_torq, old_q, how):
    """`uqs backfill` used to call torq.sh past the version check, and its
    worker died at the first nested `\\d` with the failure only in its log."""
    with pytest.raises(UqsError, match=r"kdb\+ 4\.1, which has no nested contexts"):
        STARTERS[how](old_q)


def test_backfill_is_checked_as_the_process_it_starts(monkeypatch):
    """The processes are the words before torq.sh's first flag: a backfill's
    `-extras` are not process names, and its port check is its own process's."""
    seen = []
    monkeypatch.setattr(start_policy.qtree, "refuse_unloadable", lambda *_a: None)
    monkeypatch.setattr(occupancy, "refuse_if_taken", lambda _p, _b, procs: seen.append(procs))
    paths = stack_paths.default_paths()
    start_policy.refuse_start(paths, ["start", "deals_backfill1", "-extras", "-worker", "x"], 7000)
    start_policy.refuse_start(paths, ["restart"], 7000)
    assert seen == ["deals_backfill1", "all"]
    with pytest.raises(UqsError, match="nosuchproc1"):
        start_policy.refuse_start(paths, ["start", "nosuchproc1", "-extras", "-worker", "x"], None)


def test_a_verb_that_starts_nothing_is_not_refused(monkeypatch):
    monkeypatch.setattr(
        occupancy, "refuse_if_taken", lambda *_a: pytest.fail("a stop checked the ports")
    )
    start_policy.refuse_start(stack_paths.default_paths(), ["summary", "nosuchproc1"], None)
