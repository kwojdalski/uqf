"""uqs deploy rollback (#865): a server put back on an earlier release, on
purpose - in the order a push restarts, verified before `current` moves, and
left where it began when the target will not verify. ssh is a fake that
records every script, as in test_deploy.py.
"""

from __future__ import annotations

import io
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
    return make_config(artifact="", host="uqf-server", dest="/opt/uqf", profile="rollback")


def _roll(remote, **kw):
    out = io.StringIO()
    code = rollback.rollback(_cfg(), remote, out=out, **kw)
    return code, json.loads(out.getvalue())


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
    assert remote.ran(f"cd /opt/uqf/releases/{OLD}\n", ".venv/bin/uqs stop all")
    restart = remote.index(
        f"cd /opt/uqf/releases/{NEW}\n", ".venv/bin/uqs start --profile essential"
    )
    assert restart > remote.index("uqs deploy verify --profile fx")
    assert remote.ran("uqs deploy verify --profile essential"), "and the restore is verified"


def test_a_dry_run_stops_and_starts_nothing():
    remote = _server()
    code, result = _roll(remote, dry_run=True)
    assert code == 0 and result["rollback"] == "planned"
    assert result["stop"] == ["rdb1", "hdb1"]
    assert result["start"] == {"profile": "fx", "extra_processes": ["piggy1"]}
    assert not remote.ran(".venv/bin/uqs stop") and not remote.ran(".venv/bin/uqs start")
    assert not remote.ran(".current.new")


def test_without_a_recorded_predecessor_it_asks_for_to():
    remote = _server(previous=None)
    with pytest.raises(DeployError, match="pass --to RELEASE"):
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
        ["deploy", "rollback", "--host", "h", "--dest", "/opt/uqf", "--to", "../etc;rm -rf /"],
    )
    assert result.exit_code == 1
