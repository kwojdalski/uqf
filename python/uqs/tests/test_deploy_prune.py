"""uqs deploy prune (#866): old releases removed, under the lock, and never
the release current names, the one a rollback returns to, or one whose push is
still running - whatever --keep is.

The remote here is no fake: each script runs with bash against a destination
under tmp_path, so the listing program and the removals are the server's own.
"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from uqs import cli
from uqs.deploy import prune
from uqs.deploy.config import DeployError, make_config

R1, R2, R3, R4, R5 = (f"2026100{d}T120000Z-0123456789a{d}" for d in range(1, 6))


class LocalRemote:
    """Runs each script with bash on this machine; records them in order."""

    def __init__(self) -> None:
        self.scripts: list[tuple[str, str]] = []

    def run(self, script: str, timeout: int, stage: str, as_login: bool = False):
        self.scripts.append((stage, script))
        return subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, timeout=timeout, check=False
        )

    def put(self, local: Path, remote_path: str, timeout: int, stage: str) -> None:
        raise AssertionError("pruning copies nothing to the server")


def _release(dest: Path, rid: str, size: int, **report) -> None:
    root = dest / "releases" / rid
    (root / ".venv").mkdir(parents=True)
    (root / ".venv" / "wheel").write_bytes(b"x" * size)
    if report:
        (root / "deploy-report.json").write_text(json.dumps(report))


@pytest.fixture
def dest(tmp_path: Path) -> Path:
    """R4 is current and replaced R2; R1's push never finished; R3 is plain
    history; R5 is a newer push that failed and never became current."""
    d = tmp_path / "uqf"
    _release(d, R1, 100, status="running")
    _release(d, R2, 200, status="deployed")
    _release(d, R3, 300, status="deployed", previous_release=R1)
    _release(d, R4, 400, status="deployed", previous_release=R2)
    _release(d, R5, 500, status="failed", previous_release=R4)
    (d / "current").symlink_to(f"releases/{R4}")
    return d


def _prune(dest: Path, keep: int, dry_run: bool = False):
    remote, out = LocalRemote(), io.StringIO()
    cfg = make_config(artifact="", host="uqf-server", dest=str(dest), profile="prune")
    code = prune.prune(cfg, remote, keep=keep, dry_run=dry_run, out=out)
    return code, json.loads(out.getvalue()), remote


def _left(dest: Path) -> list[str]:
    return sorted(p.name for p in (dest / "releases").iterdir())


@pytest.mark.parametrize("keep", [0, 1, 2])
def test_current_its_rollback_target_and_a_running_push_survive_any_keep(dest, keep):
    code, result, _ = _prune(dest, keep)
    assert code == 0 and result["prune"] == "done"
    assert {R1, R2, R4} <= set(_left(dest)), "running, the rollback target and current"
    kept = {k["release"]: k["why"] for k in result["kept"]}
    assert kept[R4] == "current" and kept[R2] == "a rollback would return to it"
    assert kept[R1] == "its report says a push is still running"


def test_the_oldest_beyond_keep_go_and_the_space_they_held_is_counted(dest):
    _, result, _ = _prune(dest, 1)
    assert result["removed"] == [R3] and _left(dest) == [R1, R2, R4, R5]
    assert result["freed_bytes"] == 300 + len(json.dumps({"status": "deployed",
                                                          "previous_release": R1}))  # fmt: skip
    _, result, _ = _prune(dest, 0)
    assert result["removed"] == [R5] and _left(dest) == [R1, R2, R4]


def test_a_dry_run_lists_the_removals_and_removes_nothing(dest):
    before = _left(dest)
    _, result, remote = _prune(dest, 0, dry_run=True)
    assert result["prune"] == "planned" and result["removed"] == [R3, R5]
    assert _left(dest) == before
    assert not any("rm -rf" in s and "/releases/" in s for _, s in remote.scripts)


def test_the_lock_is_held_from_before_the_listing_until_after_the_removal(dest):
    _, _, remote = _prune(dest, 0)
    stages = [stage for stage, _ in remote.scripts]
    assert stages[0] == "lock" and stages[-1] == "lock"
    assert all(stage == "prune" for stage in stages[1:-1])
    assert not (dest / "deploy.lock").exists(), "and released"


def test_the_lock_names_prune_as_its_holder(dest):
    _, _, remote = _prune(dest, 0)
    assert "command=prune" in remote.scripts[0][1]


def test_a_held_lock_refuses_and_removes_nothing(dest):
    (dest / "deploy.lock").mkdir()
    before = _left(dest)
    with pytest.raises(DeployError, match="another deployment holds"):
        _prune(dest, 0)
    assert _left(dest) == before and (dest / "deploy.lock").is_dir()


def test_only_a_name_shaped_like_a_release_is_ever_removed(dest):
    (dest / "releases" / "notes").mkdir()
    (dest / "releases" / "keep-me").symlink_to("..")
    _, result, _ = _prune(dest, 0)
    assert (dest / "releases" / "notes").is_dir() and (dest / "releases" / "keep-me").is_symlink()
    kept = {k["release"]: k["why"] for k in result["kept"]}
    assert kept["notes"] == "not named like a release, so never removed"
    assert "keep-me" not in kept, "a link is not a release, and is not even listed"


def test_a_server_with_no_releases_has_nothing_to_prune(tmp_path):
    _, result, _ = _prune(tmp_path / "empty", 0)
    assert result == {"prune": "done", "kept": [], "removed": [], "freed_bytes": 0}


def test_decide_keeps_the_newest_by_name_which_is_by_time():
    listing = {
        "current": "",
        "releases": [{"release": r, "bytes": 1, "status": "deployed"} for r in (R1, R2, R3)],
    }
    assert prune.decide(listing, 2)["removed"] == [R1]


def test_the_command_refuses_a_negative_keep():
    r = CliRunner().invoke(
        cli.app, ["deploy", "prune", "--host", "h", "--dest", "/d", "--keep", "-1"]
    )
    assert r.exit_code != 0 and "--keep" in r.output
