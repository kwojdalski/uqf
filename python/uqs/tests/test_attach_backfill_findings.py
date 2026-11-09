"""Attached-backfill coordination blockers as structured findings (#1827).

Each blocker `backfill_coordination` and `backfill_fence` raise is a `Finding` with a stable
id, the component it is about and the facts observed, and every id has a declared policy.
Without a waiver these tests also pin that admission refuses and admits exactly as it did;
waiving eod-fence (#1828) is test_attach_backfill_eod_override.py.

Admission and coordination are Python; only the plan's evaluation runs q, and `plan_with`
replaces it. So these tests run without a q interpreter.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import attach_backfill_support as support
import pytest
from attach_backfill_support import DATASET, WORKER, facts_of, make_world, plan_with
from attach_support import probe
from typer.testing import CliRunner

from uqs import cli
from uqs.attach import backfill_coordination as coordination
from uqs.attach import backfill_fence, backfill_policy
from uqs.attach import backfill_plan as planning
from uqs.attach.facts import Host
from uqs.attach.remote import IpcError
from uqs.paths import UqsError


@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.setattr(support, "q_interpreter", lambda env=None: Path("/bin/false"))
    return make_world(monkeypatch, tmp_path)


def refusal(world, facts=None) -> backfill_policy.CoordinationRefused:
    with pytest.raises(backfill_policy.CoordinationRefused) as info:
        plan_with(world, facts or facts_of())
    return info.value


def _unreachable(world, monkeypatch):
    original = world.stack.call

    def call(endpoint, op, request):
        if op == "writer_state" and endpoint.procname == "wdb1":
            raise IpcError(endpoint, op, "cannot connect: Connection refused")
        return original(endpoint, op, request)

    monkeypatch.setattr(world.stack, "call", call)


def _sort(world, monkeypatch):
    world.stack.named("wdb1")["sortrows"] = [
        {"tabname": "default", "att": "g", "col": "time", "sort": True},
    ]


def _compression(world, monkeypatch):
    world.stack.add(probe("compress1", "compression", 6010, "6000"), listed_only=True)
    world.stack.refresh_discovery()


def _state(**over):
    def apply(world, monkeypatch):
        world.stack.state_over["wdb1"] = over

    return apply


#: (id, how to provoke it, dates the plan touches, component, facts it must carry, message)
CASES = {
    "writer-unreachable": (
        _unreachable,
        None,
        "wdb1",
        {"reason": "cannot connect: Connection refused"},
        "wdb1 does not answer writer_state (cannot connect: Connection refused)",
    ),
    "remote-host": (
        lambda w, m: m.setattr(coordination, "HOST", Host(local_names=("elsewhere",))),
        None,
        "<hdb>",
        {"host": "testhost"},
        "the stack runs on testhost, not this machine",
    ),
    "network-filesystem": (
        lambda w, m: m.setattr(coordination, "filesystem_of", lambda path: "nfs4"),
        None,
        "<hdb>",
        {"filesystem": "nfs4"},
        "is on a nfs4 filesystem: kdb+'s serialisation of the sym file relies on local file",
    ),
    "segmented-hdb": (
        lambda w, m: (w.hdb / "par.txt").write_text("/elsewhere\n"),
        None,
        "<hdb>",
        {"par_txt": "<hdb>/par.txt"},
        "is segmented (par.txt): partitions live outside it",
    ),
    "sort-policy": (
        _sort,
        None,
        "wdb1",
        {
            "table": DATASET,
            "sort_row": "default",
            "sorted_by": "time",
            "attributes": "time#g",
            "expected_sorted_by": "sym,time",
            "expected_attributes": "sym#p",
        },
        f"wdb1's sort.csv sorts {DATASET} by ['time'] with attributes {{'time': 'g'}}",
    ),
    "in-place-maintenance": (
        _compression,
        None,
        "compress1",
        {"proctype": "compression"},
        "compress1 (compression) works on HDB partitions in place",
    ),
    "native-calendar": (
        _state(datatz_adj=str(5 * 3600 * 10**9)),
        None,
        "wdb1",
        {"datatz_adj": "18000000000000"},
        "wdb1 stamps `time` in a data zone other than UTC",
    ),
    "active-date-unknown": (
        _state(partition=""),
        None,
        "wdb1",
        {"proctype": "wdb", "partition": ""},
        "wdb1 does not report which date it is writing",
    ),
    "active-date": (
        lambda w, m: None,
        ["2026.09.12", "2026.10.16", "2026.10.17"],
        "wdb1",
        {"active_date": "2026-10-16", "touched_dates": "2026-10-16,2026-10-17"},
        "2026-10-16, 2026-10-17 is not completed history: "
        "wdb1's current write partition is 2026-10-16",
    ),
    "clock-unknown": (
        _state(next_roll=""),
        None,
        "wdb1",
        {"next_roll": ""},
        "wdb1 does not report its clock and next roll",
    ),
    "reload-in-progress": (
        _state(reloadcomplete=False),
        None,
        "wdb1",
        {"reloadcomplete": "False"},
        "wdb1 has an end-of-day reload in progress",
    ),
}

#: the end-of-day timing id, from each side of the roll and with the roll overdue
EOD = {
    "before-roll": (
        "2026.10.16D23:30:00.000000000",
        {"minutes_to_roll": "30", "guard_minutes": "60", "now": "2026-10-16T23:30:00+00:00"},
        "end of day is at 2026-10-17T00:00:00+00:00, 30 minutes away on wdb1's clock",
    ),
    "after-roll": (
        "2026.10.16D00:30:00.000000000",
        {"minutes_since_roll": "30", "guard_minutes": "60"},
        "wdb1's last end of day was 30 minutes ago: its sort, merge and reload may still",
    ),
    "overdue": (
        "2026.10.17D00:10:00.000000000",
        {"minutes_to_roll": "-10", "next_roll": "2026-10-17T00:00:00+00:00"},
        "end of day is at 2026-10-17T00:00:00+00:00, -10 minutes away",
    ),
}


def _expect(text: str, world) -> str:
    return text.replace("<hdb>", str(world.hdb))


@pytest.mark.parametrize("code", sorted(CASES))
def test_each_blocker_is_a_finding_with_its_component_and_facts(world, monkeypatch, code):
    provoke, dates, component, facts, message = CASES[code]
    provoke(world, monkeypatch)
    exc = refusal(world, facts_of(dates=dates) if dates else None)
    assert isinstance(exc, UqsError)
    [found] = [
        f for f in exc.findings if f.code == code and f.component == _expect(component, world)
    ]
    assert found.level == "blocker"
    for key, value in facts.items():
        assert found.facts[key] == _expect(value, world), key
    assert message in found.message and found.message in str(exc)
    # nothing else is wrong with the stack: this one check is the whole refusal (the rdb is
    # writing the active date too, so that one is found on both writers)
    assert {f.code for f in exc.findings} == {code}


@pytest.mark.parametrize("window", sorted(EOD))
def test_end_of_day_timing_is_one_finding_from_either_side(world, window):
    now, facts, message = EOD[window]
    world.stack.state_over["wdb1"] = {"now": now}
    exc = refusal(world)
    [found] = exc.findings
    assert found.code == "eod-fence" and found.component == "wdb1"
    assert found.facts["window"] == window
    assert found.facts["next_roll"] == "2026-10-17T00:00:00+00:00"
    for key, value in facts.items():
        assert found.facts[key] == value, key
    assert message in found.message
    assert found.action == backfill_policy.POLICY["eod-fence"].action
    # the facts beside it are still established: what a later override would admit with
    assert exc.coordination is not None and exc.coordination.wdb == "wdb1"


# ---------------------------------------------------------------------- the policy table


#: Every module that makes a coordination blocker: the scan reads them all.
EMITTERS = (coordination, backfill_fence)


def _blocker_calls() -> list[ast.Call]:
    return [
        n
        for module in EMITTERS
        for n in ast.walk(ast.parse(Path(module.__file__).read_text()))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "blocker"
    ]


def _emitted_codes() -> set[str]:
    return {
        c.args[0].value
        for c in _blocker_calls()
        if c.args and isinstance(c.args[0], ast.Constant) and isinstance(c.args[0].value, str)
    }


def test_every_finding_coordination_can_emit_has_a_policy():
    """A finding added without a decision about accepting it would be unreviewed."""
    emitted = _emitted_codes()
    assert emitted == set(backfill_policy.POLICY), (
        f"no policy: {sorted(emitted - set(backfill_policy.POLICY))}; "
        f"never emitted: {sorted(set(backfill_policy.POLICY) - emitted)}"
    )
    # and the cases above provoke every one of them
    assert emitted == set(CASES) | {"eod-fence"}
    # a blocker raised with a computed id would escape the scan: every call names it
    assert all(isinstance(c.args[0], ast.Constant) for c in _blocker_calls())


def test_only_end_of_day_timing_could_be_accepted():
    allowed = {code for code, policy in backfill_policy.POLICY.items() if policy.overridable}
    assert allowed == {"eod-fence"}
    for code in ("clock-unknown", "active-date-unknown", "writer-unreachable"):
        assert not backfill_policy.POLICY[code].overridable, "missing evidence is never accepted"
    for code in ("remote-host", "network-filesystem", "segmented-hdb"):
        assert not backfill_policy.POLICY[code].overridable, "unsupported is never accepted"
    assert all(p.risk for p in backfill_policy.POLICY.values())
    assert backfill_policy.POLICY["eod-fence"].action


def test_ids_are_kebab_case():
    for code in backfill_policy.POLICY:
        assert code == code.lower() and " " not in code and "_" not in code, code


def test_a_finding_without_a_policy_cannot_be_made():
    with pytest.raises(KeyError):
        backfill_policy.blocker("no-such-check", "wdb1", "message")


# ------------------------------------------------------------- admission does not change


def test_a_healthy_stack_is_admitted_with_the_same_coordination(world):
    co = plan_with(world, facts_of())["coordination"]
    assert set(co) == {
        "mechanism",
        "wdb",
        "filesystem",
        "active_date",
        "last_writable_date",
        "writes_stop_at",
        "now",
        "next_roll",
        "eod_guard_minutes",
        "writers",
        "calendar",
        "warnings",
        # #1828: whether the end-of-day fence was waived, and the findings a waiver lifted
        "eod_fence_bypassed",
        "accepted",
    }
    assert co["wdb"] == "wdb1" and co["active_date"] == "2026-10-16"
    assert co["writes_stop_at"] == "2026-10-16T23:00:00+00:00"
    assert co["eod_fence_bypassed"] is False and co["accepted"] == []


def test_a_refusal_without_the_evidence_carries_no_coordination(world, monkeypatch):
    plan_with(world, facts_of())  # admitted
    _unreachable(world, monkeypatch)
    world.stack.state_over["rdb1"] = {"now": "2026.10.16D23:30:00.000000000"}
    exc = refusal(world)
    assert [(f.code, f.component) for f in exc.findings] == [
        ("writer-unreachable", "wdb1"),
        ("eod-fence", "rdb1"),
    ]
    assert exc.coordination is None, "no wdb answered: nothing to admit with"


def test_every_blocker_is_still_one_refusal_in_the_order_found(world, monkeypatch):
    monkeypatch.setattr(coordination, "filesystem_of", lambda path: "nfs4")
    world.stack.state_over["wdb1"] = {
        "partition": "2026.09.01",
        "now": "2026.10.16D23:50:00.000000000",
        "reloadcomplete": False,
    }
    world.stack.state_over["rdb1"] = {"partitions": []}
    exc = refusal(world)
    text = str(exc)
    assert text.startswith(
        f"a backfill into {world.hdb} (attachment pb-uat) cannot be coordinated with its "
        "native writers:\n  - "
    )
    assert text.split("\n  - ")[1:] == [f.message for f in exc.findings]
    codes = [(f.code, f.component) for f in exc.findings]
    assert codes[0] == ("network-filesystem", str(world.hdb))
    assert {
        ("active-date-unknown", "rdb1"),
        ("active-date", "wdb1"),
        ("eod-fence", "wdb1"),
        ("reload-in-progress", "wdb1"),
    } <= set(codes)


def test_the_assessment_helper_is_the_refusal_without_raising(world, monkeypatch):
    """`examine` is what `assess` refuses on: same findings, no exception."""
    seen = {}
    real = coordination.examine

    def spy(*args, **kwargs):
        seen["found"] = real(*args, **kwargs)
        return seen["found"]

    monkeypatch.setattr(coordination, "examine", spy)
    world.stack.state_over["wdb1"] = {"reloadcomplete": False}
    exc = refusal(world)
    found = seen["found"]
    assert isinstance(found, backfill_policy.Assessment)
    assert found.blockers == exc.findings and found.coordination == exc.coordination
    assert found.coordination is not None and found.warnings == []


# ------------------------------------------------------------------------------- JSON


def test_a_json_refusal_carries_ids_components_and_facts(world, monkeypatch):
    monkeypatch.setattr(planning, "evaluate", lambda *a, **k: facts_of())
    world.stack.state_over["wdb1"] = {"now": "2026.10.16D23:30:00.000000000"}
    argv = [
        "job", "backfill", WORKER, "--attachment", "pb-uat", "--from", "2026-09-11",
        "--to", "2026-09-13", "--source-version", "v1", "--mode", "plan", "--json",
    ]  # fmt: skip
    # --json is read from the command line itself (uqs.logger.json_requested)
    monkeypatch.setattr(sys, "argv", ["uqs", *argv])
    result = CliRunner().invoke(cli.app, argv, env={"COLUMNS": "300"})
    assert result.exit_code == 1
    out = json.loads(result.stdout)
    assert out["ok"] is False and "cannot be coordinated" in out["error"]
    [found] = out["findings"]
    assert found["code"] == "eod-fence" and found["component"] == "wdb1"
    assert found["level"] == "blocker" and found["overridable"] is True
    assert found["facts"]["minutes_to_roll"] == "30"
    assert found["facts"]["guard_minutes"] == "60"
    assert found["message"] in out["error"]
