"""Tests for scripts/test.py's smoke lane options, and the q-docs-peachq lane.

The lane runs tests/q/smoke_external_metadata.q, which reads its sources off
its own command line. These check that --targets, --tables and --timeout-ms
reach it as those flags, and are refused for every other lane.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "test.py"

# Loaded by path: scripts/ is not a package on any search root, the same
# approach test_contract_surface.py takes.
_spec = importlib.util.spec_from_file_location("uqf_test_runner", SCRIPT)
assert _spec and _spec.loader
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)


@pytest.fixture
def ran(monkeypatch) -> list[tuple[str, str, tuple[str, ...]]]:
    calls: list[tuple[str, str, tuple[str, ...]]] = []
    # The interpreter check starts q; these tests are about the lanes' flags.
    monkeypatch.setattr(runner, "check_interpreter", lambda env=None: None)
    monkeypatch.setattr(
        runner, "_q", lambda lane, script, *args, **_kw: calls.append((lane, script, args))
    )
    return calls


def test_the_smoke_options_reach_the_script_as_its_own_flags(ran):
    argv = ["smoke", "--targets", "h1:5010", "h2:5011", "--tables", "quote:sym,bid", "trade:sym"]
    assert runner.main([*argv, "--timeout-ms", "3000"]) == 0
    assert ran == [
        (
            "smoke",
            "tests/q/smoke_external_metadata.q",
            (
                "-targets",
                "h1:5010",
                "h2:5011",
                "-tables",
                "quote:sym,bid",
                "trade:sym",
                "-timeout_ms",
                "3000",
            ),
        )
    ]


def test_smoke_with_no_options_passes_no_flags_so_the_script_can_skip(ran):
    """An unconfigured checkout is SKIP, not a failure."""
    assert runner.main(["smoke"]) == 0
    assert ran == [("smoke", "tests/q/smoke_external_metadata.q", ())]


def test_the_smoke_options_are_refused_for_another_lane(ran):
    with pytest.raises(SystemExit) as exc:
        runner.main(["python", "--targets", "h1:5010"])
    assert exc.value.code == 2
    assert ran == []


def test_q_docs_peachq_fails_rather_than_skips_when_peachq_cannot_be_had(ran, monkeypatch):
    """PeachQ is required beside KDB-X, so a resolver failure fails the lane."""

    def unavailable():
        raise runner.peachq.PeachQError("offline, and the cache is cold")

    monkeypatch.setattr(runner.peachq, "resolve", unavailable)
    with pytest.raises(SystemExit, match="q-docs-peachq: offline"):
        runner.main(["q-docs-peachq"])


def test_q_docs_peachq_reruns_q_docs_declared_as_peachq(monkeypatch):
    """The child is told it is PeachQ, so the interpreter check refuses a
    binary that is not; the resolved path reaches that lane and no other."""
    calls = []
    monkeypatch.setattr(runner, "check_interpreter", lambda env=None: None)
    monkeypatch.setattr(runner, "_run", lambda lane, argv, *, env=None: calls.append((argv, env)))
    monkeypatch.setattr(runner.peachq, "resolve", lambda: Path("/cache/peachq/q"))
    assert runner.main(["q-docs-peachq"]) == 0
    [(argv, env)] = calls
    assert argv[1:] == [str(SCRIPT), "q-docs"]
    assert env == {runner.Q_IMPL_ENV: "peachq", "QCMD": "/cache/peachq/q"}
    assert runner.Q_CMD != "/cache/peachq/q", "the global QCMD is left alone"
