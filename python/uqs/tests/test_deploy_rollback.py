"""uqs deploy rollback (#865): a server put back on an earlier release, on
purpose - in the order a push restarts, verified before `current` moves, and
left where it began when the target will not verify. ssh is a fake that
records every script, as in test_deploy.py.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from uqs import cli
from uqs.deploy import rollback, verify
from uqs.deploy.config import DeployError, make_config

NEW = "20261008T120000Z-0123456789ab"
OLD = "20261007T120000Z-ba9876543210"


def _done(stdout: str = "", rc: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], rc, stdout=stdout, stderr="")


def _report(profile: str, processes: list[str], previous: str | None = None, extra=()) -> str:
    return json.dumps(
        {
            "profile": profile,
            "processes": [{"process": p} for p in processes],
            "extra_processes": list(extra),
            "previous_release": previous,
            "status": "deployed",
        }
    )


def _verified(passed: bool) -> subprocess.CompletedProcess:
    body = {"passed": passed, "reason": "" if passed else "no answer from rdb1", "processes": []}
    marker = verify.OK_MARKER if passed else verify.FAILED_MARKER
    return _done(json.dumps(body) + "\n" + marker + "\n", rc=0 if passed else 1)


class FakeRemote:
    def __init__(self, rules: dict[str, subprocess.CompletedProcess]):
        self.rules = rules
        self.scripts: list[tuple[str, str]] = []

    def run(self, script: str, timeout: int, stage: str, as_login: bool = False):
        self.scripts.append((stage, script))
        for marker, result in self.rules.items():
            if marker in script:
                return result
        return _done()

    def put(self, local: Path, remote_path: str, timeout: int, stage: str) -> None:
        raise AssertionError("a rollback copies nothing to the server")

    def index(self, *parts: str) -> int:
        """The first script holding every one of `parts`."""
        return next(i for i, (_, s) in enumerate(self.scripts) if all(p in s for p in parts))

    def ran(self, *parts: str) -> bool:
        return any(all(p in s for p in parts) for _, s in self.scripts)


def _server(target_verifies: bool = True, current: str = NEW, previous: str | None = OLD):
    return FakeRemote(
        {
            "readlink": _done(f"{current}\n" if current else ""),
            f"releases/{NEW}/deploy-report.json": _done(
                _report("essential", ["rdb1", "hdb1"], previous)
            ),
            f"releases/{OLD}/deploy-report.json": _done(
                _report("fx", ["rdb1"], None, extra=["piggy1"])
            ),
            "uqs deploy verify --profile fx": _verified(target_verifies),
            "uqs deploy verify --profile essential": _verified(True),
        }
    )


def _cfg():
    return make_config(artifact="", ssh="uqf-server", dest="/opt/uqf", profile="rollback")


def _roll(remote, **kw):
    return rollback.rollback(_cfg(), remote, **kw)


def test_it_returns_to_the_release_current_replaced_in_the_order_a_push_restarts():
    remote = _server()
    code, result = _roll(remote)
    assert code == 0 and result["rollback"] == "done" and result["current"] == OLD
    stop = remote.index(f"cd /opt/uqf/releases/{NEW}\n", ".venv/bin/uqs stop rdb1 hdb1")
    start = remote.index(f"cd /opt/uqf/releases/{OLD}\n", ".venv/bin/uqs start --profile fx piggy1")
    check = remote.index("uqs deploy verify --profile fx")
    move = remote.index(f"ln -sfn releases/{OLD}")
    assert stop < start < check < move, "stop current, start the target, verify it, then move"
    assert remote.ran("rm -rf /opt/uqf/deploy.lock"), "the lock is released"


def test_to_names_another_release():
    remote = _server(previous=None)
    code, result = _roll(remote, to=OLD)
    assert code == 0 and result["to"] == OLD


def test_a_target_that_does_not_verify_leaves_the_server_where_it_began():
    remote = _server(target_verifies=False)
    code, result = _roll(remote)
    assert code == 1 and result["rollback"] == "failed" and result["current"] == NEW
    assert result["restore"] == "restored"
    assert not remote.ran(".current.new"), "current never moves"
    restart = remote.index(
        f"cd /opt/uqf/releases/{NEW}\n", ".venv/bin/uqs start --profile essential"
    )
    assert restart > remote.index("uqs deploy verify --profile fx")
    assert remote.ran("uqs deploy verify --profile essential"), "and the restore is verified"


def test_a_target_that_does_not_start_leaves_the_server_where_it_began():
    """A failed `uqs start` used to escape before the restore: current's
    processes stopped, the target's half-started, nothing restarted (#1541)."""
    remote = _server()
    remote.rules[".venv/bin/uqs start --profile fx"] = _done(rc=1)
    code, result = _roll(remote)
    assert code == 1 and result["rollback"] == "failed" and result["current"] == NEW
    assert result["restore"] == "restored"
    assert not remote.ran(".current.new"), "current never moves"
    assert not remote.ran("uqs deploy verify --profile fx"), "a target that did not start"
    stop = remote.index(f"cd /opt/uqf/releases/{OLD}\n", ".venv/bin/uqs stop rdb1 piggy1")
    restart = remote.index(
        f"cd /opt/uqf/releases/{NEW}\n", ".venv/bin/uqs start --profile essential"
    )
    assert stop < restart, "what the target part-started is stopped, then current restarts"
    assert remote.ran("uqs deploy verify --profile essential"), "and the restore is verified"


def test_a_target_that_will_not_stop_still_restores_current():
    remote = _server(target_verifies=False)
    remote.rules[".venv/bin/uqs stop rdb1 piggy1"] = _done(rc=1)
    code, result = _roll(remote)
    assert code == 1 and result["restore"] == "restored"
    assert remote.ran(f"cd /opt/uqf/releases/{NEW}\n", ".venv/bin/uqs start --profile essential")


def test_a_current_that_will_not_stop_is_restarted_and_nothing_else_runs():
    remote = _server()
    remote.rules[".venv/bin/uqs stop rdb1 hdb1"] = _done(rc=1)
    with pytest.raises(DeployError):
        rollback.rollback(_cfg(), remote)
    stop = remote.index(".venv/bin/uqs stop rdb1 hdb1")
    restart = remote.index(
        f"cd /opt/uqf/releases/{NEW}\n", ".venv/bin/uqs start --profile essential"
    )
    assert stop < restart and remote.ran("uqs deploy verify --profile essential")
    assert not remote.ran(".venv/bin/uqs start --profile fx"), "the target is never started"
    assert not remote.ran(".current.new"), "current never moves"
    assert remote.ran("rm -rf /opt/uqf/deploy.lock"), "the lock is released"


def test_a_target_that_does_not_verify_is_stopped_by_name_never_by_all():
    """`stop all` would leave the target's startwithall=0 processes and its
    extra ones running beside the restored release (#1040)."""
    remote = _server(target_verifies=False)
    _roll(remote)
    assert not remote.ran(".venv/bin/uqs stop all")
    assert remote.ran(f"cd /opt/uqf/releases/{OLD}\n", ".venv/bin/uqs stop rdb1 piggy1")


def test_a_report_naming_no_processes_stops_its_profile_and_extras():
    remote = _server()
    remote.rules[f"releases/{NEW}/deploy-report.json"] = _done(
        _report("arbitrage", [], OLD, extra=["piggy1"])
    )
    code, _ = _roll(remote)
    assert code == 0 and not remote.ran(".venv/bin/uqs stop all")
    stop = next(s for _, s in remote.scripts if ".venv/bin/uqs stop" in s)
    named = stop.split("uqs stop", 1)[1].split()
    assert {"arbitrage1", "crossarb1", "superbook1", "piggy1"} <= set(named)


def test_a_dry_run_stops_and_starts_nothing():
    remote = _server()
    code, result = _roll(remote, dry_run=True)
    assert code == 0 and result["rollback"] == "planned"
    assert result["stop"] == ["rdb1", "hdb1"]
    assert result["start"] == {"profile": "fx", "extra_processes": ["piggy1"]}
    assert not remote.ran(".venv/bin/uqs stop") and not remote.ran(".venv/bin/uqs start")
    assert not remote.ran(".current.new")


def test_a_dry_run_reads_the_lock_and_takes_nothing():
    """#1902: taking the lock writes on the server, and a dry run promises not to."""
    remote = _server()
    _roll(remote, dry_run=True)
    assert [stage for stage, _ in remote.scripts].count("lock") == 1
    assert not remote.ran("mkdir") and not remote.ran("rm -rf")


def test_a_dry_run_under_a_held_lock_plans_and_warns(log_text):
    remote = _server()
    remote.rules["deploy.lock/owner"] = _done("command=push\nuser=ops\nhost=h\npid=1\nage=5\n")
    code, result = _roll(remote, dry_run=True)
    assert code == 0 and result["rollback"] == "planned"
    assert "another deployment holds" in log_text() and "push" in log_text()


def test_the_command_ends_a_dry_run_with_the_note_on_stderr_after_the_json(monkeypatch):
    from rich.text import Text

    from uqs.cli import deploy_server
    from uqs.cli.safety import dry_run_note

    monkeypatch.setattr(deploy_server, "Remote", lambda *a, **k: _server())
    argv = ["deploy", "rollback", "--ssh", "h", "--dest", "/opt/uqf", "--dry-run"]
    note = Text.from_markup(dry_run_note()).plain
    r = CliRunner().invoke(cli.app, [*argv, "--json"])
    assert r.exit_code == 0, r.output
    doc = json.loads(r.stdout)  # #2125: the one document, the note beside it on stderr
    assert (doc["ok"], doc["rollback"], doc["from"], doc["to"]) == (True, "planned", NEW, OLD)
    assert r.stderr.strip().splitlines()[-1] == note
    text = CliRunner().invoke(cli.app, argv)
    assert text.stdout.startswith(f"would roll back from {NEW} to {OLD}: stop ")
    assert text.stdout.strip().splitlines()[-1] == note


def test_without_a_recorded_predecessor_it_asks_for_to():
    remote = _server(previous=None)
    with pytest.raises(DeployError, match="pass --release RELEASE"):
        _roll(remote)
    assert remote.ran("rm -rf /opt/uqf/deploy.lock"), "a refusal releases the lock"


def test_with_no_current_release_there_is_nothing_to_roll_back():
    with pytest.raises(DeployError, match="no current release"):
        _roll(_server(current=""))


def test_rolling_back_to_the_current_release_is_refused():
    with pytest.raises(DeployError, match="already current"):
        _roll(_server(), to=NEW)


def test_the_cli_refuses_a_to_that_is_not_a_release_id():
    result = CliRunner().invoke(
        cli.app,
        ["deploy", "rollback", "--ssh", "h", "--dest", "/opt/uqf", "--release", "../etc;rm -rf /"],
    )
    assert result.exit_code == 2
