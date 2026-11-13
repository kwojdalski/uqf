"""Waiving the end-of-day fence of an attached backfill (#1828, part of #1824).

`uqs job backfill JOB --attachment NAME --from A --to B --skip-eod-fence --reason TEXT`: the plan,
the dry run and the run are admitted within the native writers' end-of-day fence, and the run
gets no end-of-day write deadline (`-deadline 0Wp`). Only `eod-fence` findings are lifted -
each kept, at level `accepted`, never shown as passing - and every other blocker still refuses.

Admission is Python and the plan's q evaluation is replaced (`plan_with`), and the launch's
process is a double: these run without q. test_attach_backfill_run.py drives the real runner
past the waived boundary.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import attach_backfill_support as support
import pytest
from attach_backfill_support import WORKER, facts_of, make_world, plan_with
from plain_cli import PlainRunner
from test_attach_backfill_findings import CASES, EOD, _expect

from uqs import cli
from uqs.attach import backfill_owner, backfill_policy, backfill_run
from uqs.attach import backfill_plan as planning
from uqs.attach.backfill import Destination
from uqs.cli import safety, skip_options
from uqs.paths import UqsError

BYPASSED = "EOD fence bypassed; no EOD write deadline"
REASON = "Approved maintenance window"
WAIVED = {"eod-fence": REASON}
#: 30 minutes before wdb1's roll: an eod-fence blocker and nothing else
IN_FENCE = "2026.10.16D23:30:00.000000000"


@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.setattr(support, "q_interpreter", lambda env=None: Path("/bin/false"))
    return make_world(monkeypatch, tmp_path)


def _in_fence(world, who="wdb1", now=IN_FENCE):
    world.stack.state_over.setdefault(who, {})["now"] = now


def _waived_plan(world, facts=None):
    world.monkeypatch.setattr(planning, "evaluate", lambda *a, **k: facts or facts_of())
    return planning.plan_attached(
        world.paths, "pb-uat", WORKER, "v1", support.FROM, support.TO,
        ipc=world.stack, on_conflict="replace", waived=WAIVED,
    )  # fmt: skip


# ------------------------------------------------------------------ the declaration


def test_the_guardrail_is_declared_once_from_the_coordination_policy():
    by_id = {g.id: g for g in safety.registry()}
    g = by_id["eod-fence"]
    policy = backfill_policy.POLICY["eod-fence"]
    assert g.commands == ("job backfill",)
    assert (g.risk, g.action, g.overridable) == (policy.risk, policy.action, True)
    assert g.skip_flag and not g.component_scoped and g.name == "eod-fence"
    # the one generated skip `job backfill` takes (#1918 added others' elsewhere)
    assert [x.id for x in safety.registry() if x.skip_flag and "job backfill" in x.commands] == [
        "eod-fence"
    ]
    assert skip_options.declared("job backfill") == (g,)


def test_backfill_help_shows_the_generated_skip_and_receive_does_not():
    run = PlainRunner().invoke(cli.app, ["job", "backfill", "--help"], env={"COLUMNS": "300"})
    flat = " ".join(run.output.split())
    assert "--skip-eod-fence" in flat and "The same as --override eod-fence" in flat
    assert backfill_policy.POLICY["eod-fence"].risk[:60] in flat
    receive = PlainRunner().invoke(
        cli.app, ["job", "receiver", "start", "--help"], env={"COLUMNS": "300"}
    )
    assert "--skip-eod-fence" not in receive.output, "the receiver path takes no waiver"


# ------------------------------------------------------------------- what it lifts


@pytest.mark.parametrize("window", sorted(EOD))
def test_the_waiver_admits_a_plan_past_the_fence_from_either_side(world, window):
    now, _facts, message = EOD[window]
    _in_fence(world, now=now)
    with pytest.raises(backfill_policy.CoordinationRefused, match="end of day"):
        plan_with(world, facts_of())
    plan = _waived_plan(world)
    co = plan["coordination"]
    assert co["eod_fence_bypassed"] is True and co["writes_stop_at"] is None
    assert co["last_writable_date"] == "2026-10-15", "completed history is still the bound"
    [accepted] = co["accepted"]
    assert accepted["level"] == "accepted" and accepted["code"] == "eod-fence"
    assert accepted["reason"] == REASON and accepted["facts"]["window"] == window
    assert message in accepted["message"]
    [override] = plan["overrides"]
    assert override == {
        "override": "eod-fence",
        "reason": REASON,
        "requested": True,
        "needed": True,
        "accepted": [accepted],
        "effect": BYPASSED,
    }


def test_every_writer_in_the_fence_is_lifted_and_each_is_kept_visible(world):
    _in_fence(world, "wdb1")
    _in_fence(world, "rdb1")
    plan = _waived_plan(world)
    assert sorted(f["component"] for f in plan["coordination"]["accepted"]) == ["rdb1", "wdb1"]
    assert all(f["level"] == "accepted" for f in plan["coordination"]["accepted"])


@pytest.mark.parametrize("code", sorted(CASES))
def test_the_waiver_lifts_nothing_else(world, monkeypatch, code):
    """Each other blocker - the active date, a reload in progress, an unknown clock, missing
    evidence, an unsupported destination - still refuses, beside a lifted fence."""
    provoke, dates, component, _facts, message = CASES[code]
    provoke(world, monkeypatch)
    _in_fence(world, "rdb1")
    with pytest.raises(backfill_policy.CoordinationRefused) as info:
        _waived_plan(world, facts_of(dates=dates) if dates else None)
    exc = info.value
    assert {f.code for f in exc.findings} == {code}
    assert all(f.level == "blocker" for f in exc.findings)
    assert any(f.component == _expect(component, world) for f in exc.findings)
    assert message in str(exc)
    assert [(f.code, f.component, f.level) for f in exc.accepted] == [
        ("eod-fence", "rdb1", "accepted")
    ]
    assert "accepted, and lifting nothing else (eod-fence waived)" in str(exc)
    details = exc.json_details()
    assert {f["code"] for f in details["findings"]} == {code}
    assert [f["code"] for f in details["accepted"]] == ["eod-fence"]


@pytest.mark.parametrize(
    ("over", "message"),
    [
        (
            {"partition": "2026.09.01"},
            "2026-09-12 is not completed history: wdb1's current write partition is",
        ),
        ({"reloadcomplete": False}, "wdb1 has an end-of-day reload in progress"),
        ({"next_roll": ""}, "wdb1 does not report its clock and next roll"),
    ],
)
def test_active_date_reload_and_clock_still_refuse_with_the_fence_waived(world, over, message):
    world.stack.state_over["wdb1"] = {"now": IN_FENCE, **over}
    with pytest.raises(backfill_policy.CoordinationRefused, match="cannot be coordinated") as info:
        _waived_plan(world)
    assert message in str(info.value)
    assert "eod-fence" not in {f.code for f in info.value.findings}


def test_a_waiver_nobody_needed_is_recorded_as_requested_not_needed(world):
    plain = plan_with(world, facts_of())
    assert plain["overrides"] == []
    assert plain["coordination"]["eod_fence_bypassed"] is False
    assert plain["coordination"]["writes_stop_at"] == "2026-10-16T23:00:00+00:00"
    plan = _waived_plan(world)
    [override] = plan["overrides"]
    assert override["requested"] is True and override["needed"] is False
    assert override["accepted"] == [] and plan["coordination"]["accepted"] == []
    # the fence is still waived for the run: no write deadline
    assert plan["coordination"]["writes_stop_at"] is None
    assert plan["coordination"]["eod_fence_bypassed"] is True
    text = planning.render(plan)
    assert "requested but not needed: no finding of it fired at admission" in text


def test_a_finding_that_is_never_waived_is_refused_as_a_waiver(world):
    for code, why in (("active-date", "is never waived"), ("no-such", "nothing to waive")):
        with pytest.raises(UqsError, match=why):
            backfill_policy.check_waivers({code: "because"})
    with pytest.raises(UqsError, match="needs a reason"):
        backfill_policy.check_waivers({"eod-fence": " "})


# ------------------------------------------------------------------- what it says


def test_the_plan_says_the_fence_is_bypassed_and_never_that_writes_stop(world):
    _in_fence(world)
    text = planning.render(_waived_plan(world))
    assert BYPASSED in text and "writes stop at" not in text
    assert "last writable date 2026-10-15" in text
    assert f"OVERRIDE      eod-fence (needed: it lifted the findings below); reason: {REASON}" in (
        text
    )
    assert "    accepted    wdb1: end of day is at 2026-10-17T00:00:00+00:00" in text
    world.stack.state_over.pop("wdb1")
    fenced = planning.render(plan_with(world, facts_of()))
    assert "writes stop at 2026-10-16T23:00:00+00:00, 60 min before end of day" in fenced
    assert BYPASSED not in fenced and "OVERRIDE" not in fenced


# ------------------------------------------------------------------- the runner


def _flags(plan, tmp):
    dest = Destination(**backfill_run._destination(plan))
    return backfill_run.runner_flags(
        plan, dest, tmp / "owner.json", host="testhost", user="u", worker=WORKER, version="v1",
        range_from=support.FROM, range_to=support.TO, mode="run", on_conflict=None,
        verbose=False, trace=False,
    )  # fmt: skip


def _deadline(flags):
    return flags[flags.index("-deadline") + 1]


def test_the_runner_gets_an_infinite_deadline_only_when_the_fence_is_waived(world):
    fenced = _flags(plan_with(world, facts_of()), world.tmp)
    assert _deadline(fenced) == "2026.10.16D23:00:00.000000000"
    waived = _flags(_waived_plan(world), world.tmp)
    assert _deadline(waived) == "0Wp" == backfill_run.NO_DEADLINE
    assert waived[waived.index("-max_date") + 1] == "2026.10.15", "the date bound is kept"


def test_an_admission_that_contradicts_itself_is_refused(world):
    plan = plan_with(world, facts_of())
    plan["coordination"]["writes_stop_at"] = None
    with pytest.raises(UqsError, match="inconsistent"):
        backfill_run.bounds_of(plan)


class _Started:
    """What Popen returns, for a launch that runs nothing."""

    pid = 999_999_999

    def poll(self):
        return 0


@pytest.fixture
def launcher(world, monkeypatch):
    """backfill_run.launch with no q: the command line it would start is recorded."""
    started: list[list[str]] = []
    monkeypatch.setattr(backfill_run, "q_interpreter", lambda env=None: Path("/bin/false"))
    support.fake_popen(monkeypatch, lambda argv, **kw: started.append(argv) or _Started())
    monkeypatch.setattr(planning, "evaluate", lambda *a, **k: facts_of())

    def launch(mode="run", **kw):
        return backfill_run.launch(
            world.paths, "pb-uat", WORKER, "v1", support.FROM, support.TO,
            ipc=world.stack, mode=mode, **kw,
        )  # fmt: skip

    return launch, started


@pytest.mark.parametrize("mode", ["run", "dry-run"])
def test_the_fresh_admission_at_launch_applies_the_waiver(world, launcher, mode):
    """The plan an operator read was outside the fence; by launch the stack is inside it.
    The admission made at launch finds the fence now, and the waiver lifts it then."""
    launch, started = launcher
    before = _waived_plan(world)
    assert before["overrides"][0]["needed"] is False
    _in_fence(world)
    with pytest.raises(backfill_policy.CoordinationRefused, match="30 minutes away"):
        launch()
    assert started == [], "refused: nothing started"
    _, plan = launch(mode, waived=WAIVED)
    assert plan["overrides"][0]["needed"] is True
    assert [f["code"] for f in plan["coordination"]["accepted"]] == ["eod-fence"]
    [argv] = started
    assert _deadline(argv) == "0Wp"
    assert argv[argv.index("-mode") + 1] == mode.replace("-", "_")
    backfill_owner.release("pb-uat", WORKER)


def test_the_waiver_at_launch_still_refuses_a_reload_in_progress(world, launcher):
    launch, started = launcher
    world.stack.state_over["wdb1"] = {"now": IN_FENCE, "reloadcomplete": False}
    with pytest.raises(backfill_policy.CoordinationRefused, match="end-of-day reload in progress"):
        launch(waived=WAIVED)
    assert started == []


# ---------------------------------------------------------------------- the command


ARGV = [
    "job", "backfill", WORKER, "--attachment", "pb-uat", "--from", "2026-09-11",
    "--to", "2026-09-13", "--source-version", "v1",
]  # fmt: skip


def _cli(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["uqs", *ARGV, *argv])
    return PlainRunner().invoke(cli.app, [*ARGV, *argv], env={"COLUMNS": "300"})


def test_cli_plan_with_the_skip_admits_and_says_so(world, monkeypatch):
    monkeypatch.setattr(planning, "evaluate", lambda *a, **k: facts_of())
    _in_fence(world)
    refused = _cli(monkeypatch, "--mode", "plan")
    assert refused.exit_code == 1
    ok = _cli(monkeypatch, "--mode", "plan", "--skip-eod-fence", "--reason", REASON)
    assert ok.exit_code == 0, ok.output
    assert BYPASSED in ok.output and "writes stop at" not in ok.output
    same = _cli(monkeypatch, "--mode", "plan", "--override", "eod-fence", "--reason", REASON)
    assert same.exit_code == 0 and same.output == ok.output, "two spellings of one waiver"


def test_cli_plan_json_carries_the_override_record(world, monkeypatch):
    monkeypatch.setattr(planning, "evaluate", lambda *a, **k: facts_of())
    _in_fence(world)
    result = _cli(monkeypatch, "--mode", "plan", "--json", "--skip-eod-fence", "--reason", REASON)
    assert result.exit_code == 0, result.output
    out = json.loads(result.stdout)
    assert out["coordination"]["writes_stop_at"] is None
    assert out["coordination"]["eod_fence_bypassed"] is True
    [override] = out["overrides"]
    assert override["needed"] is True and override["reason"] == REASON
    assert override["accepted"][0]["level"] == "accepted"


def test_cli_run_threads_the_waiver_to_the_launch(world, monkeypatch, launcher):
    _, started = launcher
    _in_fence(world)
    result = _cli(monkeypatch, "--dry-run", "--skip-eod-fence", "--reason", REASON)
    assert result.exit_code == 0, result.output
    assert BYPASSED in " ".join(result.output.split())
    [argv] = started
    assert _deadline(argv) == "0Wp" and argv[argv.index("-mode") + 1] == "dry_run"
    backfill_owner.release("pb-uat", WORKER)


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--skip-eod-fence"], "--skip-eod-fence needs --reason"),
        (
            ["--mode", "validate", "--skip-eod-fence", "--reason", REASON],
            "--skip-eod-fence means nothing with --mode validate",
        ),
        (
            ["--writer", "stackhost:7000", "--skip-eod-fence", "--reason", REASON],
            "--skip-eod-fence is not supported with --writer",
        ),
        (
            ["--writer", "stackhost:7000", "--override", "any-writer", "--override", "eod-fence",
             "--reason", REASON],
            "--skip-eod-fence is not supported with --writer",
        ),
    ],
)  # fmt: skip
def test_cli_refuses_the_waiver_where_it_cannot_be_honoured(world, monkeypatch, argv, message):
    result = _cli(monkeypatch, *argv)
    assert result.exit_code != 0
    assert any(message in m for m in world.errors), world.errors


def test_cli_refuses_the_waiver_on_the_fleet_path(world, monkeypatch):
    argv = ["job", "backfill", WORKER, "--source-version", "v1", "--from", "2026-09-11",
            "--to", "2026-09-13", "--skip-eod-fence", "--reason", REASON]  # fmt: skip
    monkeypatch.setattr(sys, "argv", ["uqs", *argv])
    result = PlainRunner().invoke(cli.app, argv, env={"COLUMNS": "300"})
    assert result.exit_code != 0
    assert any(
        "--skip-eod-fence is for --attachment: only a backfill into an attached native HDB" in m
        for m in world.errors
    ), world.errors
