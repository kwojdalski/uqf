"""The record of a waived end-of-day fence (#1829, part of #1824).

A dry run or run of `uqs job backfill --attachment NAME --skip-eod-fence --reason TEXT` appends an
`override` entry to backfill_overrides.jsonl in the destination's state directory before the
runner can start, refuses to start if it cannot, never rewrites it, holds no credential, and
says whether the waiver was needed. The waiver itself is not saved: the next invocation is
fenced again. No q: the admission's q evaluation and the launch's process are doubles.
"""

from __future__ import annotations

import dataclasses
import getpass
import json
import sys
from pathlib import Path

import attach_backfill_support as support
import pytest
from attach_backfill_support import WORKER, facts_of, make_world
from test_attach_backfill_eod_override import IN_FENCE, REASON, WAIVED
from typer.testing import CliRunner

from uqs import cli
from uqs.attach import backfill_audit, backfill_owner, backfill_policy, backfill_run, config
from uqs.attach import backfill_plan as planning
from uqs.paths import UqsError

SENTINEL = "s3ntinel-Pa55word-1829"
PASSWORD_ENV = "UQF_TEST_1829_PASSWORD"


class _Started:
    pid = 999_999_999

    def poll(self):
        return 0


@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.setattr(support, "q_interpreter", lambda env=None: Path("/bin/false"))
    w = make_world(monkeypatch, tmp_path)
    # the stack's password comes from a variable: a sentinel no record may hold
    att = config.load("pb-uat")
    stack = dataclasses.replace(att.stack, user="uqfop", password_env=PASSWORD_ENV)
    config.save(dataclasses.replace(att, stack=stack))
    monkeypatch.setenv(PASSWORD_ENV, SENTINEL)
    monkeypatch.setattr(backfill_run, "q_interpreter", lambda env=None: Path("/bin/false"))
    monkeypatch.setattr(planning, "evaluate", lambda *a, **k: facts_of())
    yield w
    backfill_owner.release("pb-uat", WORKER)


def _in_fence(world):
    world.stack.state_over.setdefault("wdb1", {})["now"] = IN_FENCE


def _popen(world, record: list, *, fail: bool = False):
    """A Popen double that notes what was on disk the moment it was called."""

    def popen(argv, **kw):
        files = list(world.status.rglob(backfill_audit.OVERRIDES_LOG))
        record.append({"argv": argv, "env": kw["env"], "audit": [f.read_text() for f in files]})
        if fail:
            raise FileNotFoundError(2, "No such file or directory", argv[0])
        return _Started()

    support.fake_popen(world.monkeypatch, popen)


def _launch(world, mode="run", **kw):
    return backfill_run.launch(
        world.paths, "pb-uat", WORKER, "v1", support.FROM, support.TO,
        ipc=world.stack, mode=mode, **kw,
    )  # fmt: skip


def _record_file(world) -> Path:
    [path] = world.status.rglob(backfill_audit.OVERRIDES_LOG)
    return path


# ------------------------------------------------------------------- when it is written


def test_the_record_is_on_disk_before_the_runner_is_started(world):
    calls: list = []
    _popen(world, calls)
    _in_fence(world)
    run, _plan = _launch(world, waived=WAIVED)
    [call] = calls
    [text] = call["audit"]
    [entry] = [json.loads(x) for x in text.splitlines()]
    assert entry["event"] == "override" and entry["run"] == run.run_id
    assert run.audit == _record_file(world) == run.state_dir / backfill_audit.OVERRIDES_LOG
    assert run.as_dict()["override_record"] == str(run.audit)


def test_a_runner_that_cannot_start_keeps_its_record_and_says_not_started(world):
    calls: list = []
    _popen(world, calls, fail=True)
    _in_fence(world)
    with pytest.raises(UqsError, match="could not start the backfill runner"):
        _launch(world, waived=WAIVED)
    override, outcome = backfill_audit.read(_record_file(world))
    assert override["event"] == "override" and override["needed"] is True
    assert outcome["event"] == "outcome" and outcome["outcome"] == "not-started"
    assert outcome["run"] == override["run"]
    assert backfill_owner.active("pb-uat") == [], "nothing started, nothing owned"


def test_a_record_that_cannot_be_written_starts_nothing(world):
    calls: list = []
    _popen(world, calls)
    _in_fence(world)
    plan = planning.plan_attached(
        world.paths, "pb-uat", WORKER, "v1", support.FROM, support.TO, ipc=world.stack,
        waived=WAIVED,
    )  # fmt: skip
    blocker = Path(plan["destination"]["state_dir"]) / backfill_audit.OVERRIDES_LOG
    blocker.mkdir(parents=True)  # a directory where the log goes: the append fails
    with pytest.raises(UqsError, match="could not record the override .* nothing was started"):
        _launch(world, waived=WAIVED)
    assert calls == []
    assert backfill_owner.active("pb-uat") == []


def test_a_run_without_a_waiver_writes_no_record(world):
    calls: list = []
    _popen(world, calls)
    run, _ = _launch(world)
    assert run.audit is None and calls[0]["audit"] == []
    assert list(world.status.rglob(backfill_audit.OVERRIDES_LOG)) == []


def test_plan_records_nothing(world):
    _in_fence(world)
    planning.plan_attached(
        world.paths, "pb-uat", WORKER, "v1", support.FROM, support.TO, ipc=world.stack,
        waived=WAIVED,
    )  # fmt: skip
    assert list(world.status.rglob(backfill_audit.OVERRIDES_LOG)) == []


# ------------------------------------------------------------------- what it holds


@pytest.mark.parametrize("mode", ["run", "dry-run"])
def test_the_record_names_the_override_who_when_where_and_what_it_accepted(world, mode):
    calls: list = []
    _popen(world, calls)
    _in_fence(world)
    run, plan = _launch(world, mode, waived=WAIVED)
    [e] = backfill_audit.read(run.audit)
    assert e["override"] == "eod-fence" and e["reason"] == REASON and e["mode"] == mode
    assert (e["requested"], e["needed"]) == (True, True)
    assert e["effect"] == backfill_policy.EOD_BYPASSED
    assert e["operator"] == getpass.getuser() and e["host"] and e["at"]
    how = "--dry-run" if mode == "dry-run" else f"--mode {mode}"
    assert e["command"] == (
        f"uqs job backfill {WORKER} --attachment pb-uat {how} --from "
        f"{plan['range_from']} --to {plan['range_to']} --source-version v1 "
        "--override eod-fence"
    )
    d = e["destination"]
    assert d["attachment"] == "pb-uat" and d["hdb_dir"] == plan["destination"]["hdb_dir"]
    assert Path(d["hdb_dir"]).resolve() == world.hdb.resolve()
    assert d["identity"] == run.destination.identity
    assert e["bounds"] == {
        "last_writable_date": "2026-10-15",
        "writes_stop_at": None,
        "eod_fence_bypassed": True,
    }
    [accepted] = e["accepted"]
    assert accepted["level"] == "accepted" and accepted["code"] == "eod-fence"
    assert accepted["level"] != "pass" and accepted["reason"] == REASON
    [observed] = e["observed"]
    assert observed == {"component": "wdb1", **accepted["facts"]} and "window" in observed
    assert e["job"] == WORKER and e["log"] == str(run.log) and e["run"] == run.log.stem


def test_a_waiver_nobody_needed_is_recorded_as_not_needed(world):
    calls: list = []
    _popen(world, calls)
    run, _ = _launch(world, waived=WAIVED)
    [e] = backfill_audit.read(run.audit)
    assert (e["requested"], e["needed"]) == (True, False)
    assert e["accepted"] == [] and e["observed"] == []
    assert e["bounds"]["writes_stop_at"] is None, "it still bypassed the write deadline"


def test_the_record_holds_no_credential(world):
    calls: list = []
    _popen(world, calls)
    _in_fence(world)
    att = config.load("pb-uat")
    assert att.stack.password() == SENTINEL
    run, _ = _launch(world, waived=WAIVED)
    backfill_audit.outcome(run.audit, run.run_id, outcome="failed", result={"error": "boom"})
    [call] = calls
    assert call["env"][backfill_run.REACH_PASSWORD_ENV] == SENTINEL, "it was in play"
    text = run.audit.read_text()
    assert SENTINEL not in text
    assert f"{att.stack.user}:{SENTINEL}" not in text and f":{SENTINEL}" not in text
    assert backfill_run.REACH_PASSWORD_ENV not in text


# --------------------------------------------------------------- failure and partial runs


def test_a_failed_run_keeps_its_record_and_only_appends_the_outcome(world):
    calls: list = []
    _popen(world, calls)
    _in_fence(world)
    run, _ = _launch(world, waived=WAIVED)
    before = run.audit.read_bytes()
    backfill_audit.outcome(run.audit, run.run_id, outcome="partial", exit_code=1)
    after = run.audit.read_bytes()
    assert after.startswith(before), "the override entry is never rewritten"
    override, outcome = backfill_audit.read(run.audit)
    assert override["event"] == "override" and outcome["outcome"] == "partial"


# ------------------------------------------------------------- scoped to one invocation


def test_the_waiver_is_not_saved_and_the_next_invocation_is_fenced_again(world):
    calls: list = []
    _popen(world, calls)
    _in_fence(world)
    cfg = config.directory("pb-uat") / config.CONFIG_FILE
    saved = cfg.read_bytes()
    run, _ = _launch(world, waived=WAIVED)
    assert cfg.read_bytes() == saved, "nothing is written into the attachment's configuration"
    assert b"eod" not in saved.lower() and REASON.encode() not in cfg.read_bytes()
    backfill_owner.release("pb-uat", WORKER)
    recorded = run.audit.read_bytes()
    with pytest.raises(backfill_policy.CoordinationRefused, match="end of day"):
        _launch(world)
    assert len(calls) == 1, "the second invocation started nothing"
    assert run.audit.read_bytes() == recorded


# ------------------------------------------------------------------- the command


ARGV = [
    "job", "backfill", WORKER, "--attachment", "pb-uat", "--from", "2026-09-11",
    "--to", "2026-09-13", "--source-version", "v1",
]  # fmt: skip


def _cli(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["uqs", *ARGV, *argv])
    return CliRunner().invoke(cli.app, [*ARGV, *argv], env={"COLUMNS": "300"})


def test_cli_run_reports_the_record_keeps_the_risk_accepted_and_appends_the_outcome(
    world, monkeypatch
):
    calls: list = []
    _popen(world, calls)
    _in_fence(world)
    argv = ("--wait", "--skip-eod-fence", "--reason", REASON)
    text = _cli(monkeypatch, *argv)
    assert text.exit_code == 0, text.output
    flat = " ".join(text.output.split())
    assert "recorded in" in flat and "accepted wdb1" in flat
    backfill_owner.release("pb-uat", WORKER)
    result = _cli(monkeypatch, *argv, "--json")
    assert result.exit_code == 0, result.output
    out = json.loads(result.stdout)
    [accepted] = out["plan"]["coordination"]["accepted"]
    assert accepted["level"] == "accepted"
    entries = backfill_audit.read(Path(out["override_record"]))
    assert [e["event"] for e in entries] == ["override", "outcome", "override", "outcome"]
    assert entries[-1]["run"] == Path(out["log"]).stem and entries[-1]["exit_code"] == 0


def test_attachment_show_lists_the_recorded_waivers(world, monkeypatch):
    calls: list = []
    _popen(world, calls)
    _in_fence(world)
    run, _ = _launch(world, waived=WAIVED)
    shown = CliRunner().invoke(cli.app, ["job", "attachment", "show", "pb-uat", "--json"])
    assert shown.exit_code == 0, shown.output
    doc = json.loads(shown.stdout)
    assert doc["backfill_overrides"]["record"] == str(run.audit)
    [e] = doc["backfill_overrides"]["entries"]
    assert e["override"] == "eod-fence" and e["needed"] is True
    human = CliRunner().invoke(cli.app, ["job", "attachment", "show", "pb-uat"])
    flat = " ".join(human.output.split())
    assert "OVERRIDE eod-fence (needed; run)" in flat and "accepted: wdb1 eod-fence" in flat
    assert SENTINEL not in human.output and SENTINEL not in shown.output
