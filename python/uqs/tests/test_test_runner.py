"""Tests for scripts/test.py's smoke lane options.

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
