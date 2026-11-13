"""`uqs job backfill --attachment ... --skip-eod-fence` through the real command (#1830, the last
part of #1824).

The per-part tests prove the waiver in the library under the command. These invoke the Typer
command itself: either spelling, repeated or not, admits a plan, dry run or run within the
fence as one waiver, with no --writer; every malformed waiver is refused and starts nothing;
the checks outside coordination still refuse; a backfill without the flag is what it was before
#1824; and nothing of a skip survives its invocation.

No q: the admission's q evaluation and the runner's process are doubles, as in
test_attach_backfill_eod_override.py, whose fixtures these mirror.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import attach_backfill_support as support
import pytest
from attach_backfill_support import WORKER, facts_of, make_world
from plain_cli import PlainRunner
from test_attach_backfill_eod_override import IN_FENCE, REASON

from uqs import cli
from uqs.attach import backfill_audit, backfill_owner, backfill_policy, backfill_run
from uqs.attach import backfill_plan as planning
from uqs.cli import skip_options

BYPASSED = backfill_policy.EOD_BYPASSED
FENCED_DEADLINE = "2026.10.16D23:00:00.000000000"


class _Started:
    pid = 999_999_999

    def poll(self):
        return 0


@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.setattr(support, "q_interpreter", lambda env=None: Path("/bin/false"))
    w = make_world(monkeypatch, tmp_path)
    monkeypatch.setattr(backfill_run, "q_interpreter", lambda env=None: Path("/bin/false"))
    monkeypatch.setattr(planning, "evaluate", lambda *a, **k: facts_of())
    w.started = []
    support.fake_popen(monkeypatch, lambda argv, **kw: w.started.append(argv) or _Started())
    yield w
    backfill_owner.release("pb-uat", WORKER)


def _in_fence(world):
    world.stack.state_over.setdefault("wdb1", {})["now"] = IN_FENCE


ARGV = [
    "job", "backfill", WORKER, "--attachment", "pb-uat", "--from", "2026-09-11",
    "--to", "2026-09-13", "--source-version", "v1",
]  # fmt: skip


def _cli(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["uqs", *ARGV, *argv])
    return PlainRunner().invoke(cli.app, [*ARGV, *argv], env={"COLUMNS": "300"})


def _records(world) -> list[dict]:
    return [
        e for f in world.status.rglob(backfill_audit.OVERRIDES_LOG) for e in backfill_audit.read(f)
    ]


def _flag(argv, name):
    return argv[argv.index(name) + 1]


SPELLINGS = {
    "skip": ["--skip-eod-fence"],
    "override": ["--override", "eod-fence"],
    "both": ["--skip-eod-fence", "--override", "eod-fence"],
    "override-twice": ["--override", "eod-fence", "--override", "eod-fence"],
    "skip-twice": ["--skip-eod-fence", "--skip-eod-fence"],
}


@pytest.mark.parametrize("mode", ["plan", "dry-run", "run"])
@pytest.mark.parametrize("spelling", sorted(SPELLINGS))
def test_each_spelling_admits_a_fenced_local_backfill_as_one_waiver(
    world, monkeypatch, spelling, mode
):
    """The ordinary attached path, without --writer: a plan, a dry run and a run within the
    fence are admitted by either spelling, repeated or not, as one waiver."""
    _in_fence(world)
    how = ["--dry-run"] if mode == "dry-run" else ["--mode", mode]
    result = _cli(monkeypatch, *how, *SPELLINGS[spelling], "--reason", REASON)
    assert result.exit_code == 0, (result.output, world.errors)
    assert BYPASSED in " ".join(result.output.split())
    if mode == "plan":
        assert world.started == [] and _records(world) == []
        return
    [argv] = world.started
    assert _flag(argv, "-deadline") == "0Wp" and _flag(argv, "-max_date") == "2026.10.15"
    assert _flag(argv, "-mode") == mode.replace("-", "_")
    [entry] = _records(world)
    assert (entry["override"], entry["reason"], entry["needed"]) == ("eod-fence", REASON, True)


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--override", "eod-fence"], "--override eod-fence needs --reason"),
        (["--skip-eod-fence", "--reason", "  "], "--skip-eod-fence needs --reason"),
        (["--reason", REASON], "--reason says why an --override is acceptable, and none"),
        (
            ["--override", "eod-fence:wdb1", "--reason", REASON],
            "eod-fence is waived for the whole command, not one component - give "
            "--override eod-fence",
        ),
        (
            ["--skip-eod-fence", "--override", "eod-fence:wdb1", "--reason", REASON],
            "conflicting requests for eod-fence: --skip-eod-fence waives it for the whole "
            "command, --override eod-fence:wdb1 for one component",
        ),
        (
            ["--override", "active-date", "--reason", REASON],
            "`uqs job backfill` has no override 'active-date' - it takes: any-writer, eod-fence",
        ),
        (
            ["--override", "q-version-check", "--reason", REASON],
            "has no override 'q-version-check' (it is `uqs start`, `uqs restart`'s)",
        ),
        (
            ["--override", "TORQ_VERSION:stp1", "--reason", REASON],
            "has no override 'TORQ_VERSION:stp1' (it is `uqs job attachment add`'s)",
        ),
    ],
)
def test_a_malformed_waiver_is_refused_and_starts_nothing(world, monkeypatch, argv, message):
    _in_fence(world)
    result = _cli(monkeypatch, *argv)
    assert result.exit_code != 0
    assert any(message in m for m in world.errors), world.errors
    assert world.started == [] and _records(world) == []


@pytest.mark.parametrize("name", ["--skip-active-date", "--skip-reload-in-progress", "--skip-eod"])
def test_no_other_coordination_finding_has_a_skip(world, monkeypatch, name):
    result = _cli(monkeypatch, name, "--reason", REASON)
    assert result.exit_code == 2 and f"No such option: {name}" in result.output
    assert world.started == []


def test_the_waiver_leaves_the_schema_and_ownership_checks_in_force(world, monkeypatch):
    """Outside coordination too: a table whose shape drifted, and a run already owning the
    attachment, refuse a waived backfill as they refuse any other."""
    _in_fence(world)
    table = support.tree_table()
    drifted = type(table)(table.name, (*table.columns[:-1], ("extra", "f")))
    support.save_attachment("pb-uat", world.hdb, table=drifted)
    waived = ("--skip-eod-fence", "--reason", REASON)
    assert _cli(monkeypatch, "--mode", "run", *waived).exit_code == 1
    assert any("differs between attachment pb-uat" in m for m in world.errors), world.errors
    support.save_attachment("pb-uat", world.hdb)
    backfill_owner.claim("pb-uat", WORKER, os.getpid(), "elsewhere")
    assert _cli(monkeypatch, "--mode", "run", *waived).exit_code == 1
    assert any("has an attached backfill running" in m for m in world.errors), world.errors
    assert world.started == [] and _records(world) == []


def test_without_a_waiver_a_backfill_is_what_it_was_before(world, monkeypatch):
    """Outside the fence: a deadline on the runner, no override, no record. Inside it: the
    same refusal as ever, and nothing started."""
    result = _cli(monkeypatch, "--mode", "run")
    assert result.exit_code == 0, (result.output, world.errors)
    [argv] = world.started
    assert _flag(argv, "-deadline") == FENCED_DEADLINE
    assert _flag(argv, "-max_date") == "2026.10.15"
    assert BYPASSED not in result.output and "OVERRIDE" not in result.output
    assert _records(world) == []
    plan = _cli(monkeypatch, "--mode", "plan", "--json")
    co = json.loads(plan.stdout)
    assert co["overrides"] == [] and co["coordination"]["writes_stop_at"] is not None
    _in_fence(world)
    refused = _cli(monkeypatch, "--mode", "run")
    assert refused.exit_code == 1
    assert any("cannot be coordinated" in m and "end of day" in m for m in world.errors)
    assert len(world.started) == 1 and _records(world) == []


def test_a_skip_does_not_outlive_its_invocation(world, monkeypatch):
    """Two invocations in one process: the second, without the flag, is fenced again - nothing
    from the first survives in skip_options' context variable or in the parsed options."""
    _in_fence(world)
    first = _cli(monkeypatch, "--mode", "run", "--skip-eod-fence", "--reason", REASON)
    assert first.exit_code == 0, (first.output, world.errors)
    assert skip_options._CURRENT.get() is None
    backfill_owner.release("pb-uat", WORKER)
    second = _cli(monkeypatch, "--mode", "run")
    assert second.exit_code == 1
    assert any("end of day" in m for m in world.errors), world.errors
    assert len(world.started) == 1, "the second invocation started nothing"
    # nor does a refused one leave anything behind
    world.errors.clear()
    assert _cli(monkeypatch, "--mode", "validate", "--skip-eod-fence", "--reason", "r").exit_code
    assert skip_options._CURRENT.get() is None
    plan = _cli(monkeypatch, "--mode", "plan")
    assert plan.exit_code == 1 and any("end of day" in m for m in world.errors)
    assert len(_records(world)) == 1
