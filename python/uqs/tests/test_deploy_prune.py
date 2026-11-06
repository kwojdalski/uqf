"""uqs deploy prune (#866): old releases removed, under the lock, and never
the release current names, the one a rollback returns to, or one whose push is
still running - whatever --keep is.

The remote here is no fake: each script runs with bash against a destination
under tmp_path, so the listing program and the removals are the server's own.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from rich.text import Text
from typer.testing import CliRunner

from uqs import cli
from uqs.cli.safety import dry_run_note
from uqs.deploy import prune
from uqs.deploy.config import DeployError, make_config

NOTE = Text.from_markup(dry_run_note()).plain
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
    remote = LocalRemote()
    cfg = make_config(artifact="", ssh="uqf-server", dest=str(dest), profile="prune")
    code, result = prune.prune(cfg, remote, keep=keep, dry_run=dry_run)
    return code, result, remote


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


def test_a_dry_run_takes_no_lock_and_writes_nothing_on_the_server(dest):
    """#1902: taking the lock is a write - the lock, its owner and heartbeat -
    and a dry run promises none. It reads the lock instead."""
    _, _, remote = _prune(dest, 0, dry_run=True)
    assert not any("mkdir" in s or "> " in s for _, s in remote.scripts)
    assert not (dest / "deploy.lock").exists()


def test_a_dry_run_under_a_held_lock_plans_and_says_the_lock_is_held(dest, log_text):
    (dest / "deploy.lock").mkdir()
    (dest / "deploy.lock" / "owner").write_text("head\ncommand=push\nuser=ops\nhost=h\npid=1\n")
    _, result, _ = _prune(dest, 0, dry_run=True)
    assert result["prune"] == "planned" and result["removed"] == [R3, R5]
    assert (dest / "deploy.lock" / "owner").is_file(), "left as it was"
    assert "another deployment holds" in log_text() and "push" in log_text()


def test_the_command_ends_a_dry_run_with_the_note_on_stderr_after_the_json(dest, monkeypatch):
    """#1902, #2125: under --json stdout is the one document a program parses,
    and the note goes to stderr; without it, the text and then the note."""
    from uqs.cli import deploy_server

    monkeypatch.setattr(deploy_server, "Remote", lambda *a, **k: LocalRemote())
    argv = ["deploy", "prune", "--ssh", "h", "--dest", str(dest), "--keep", "0", "--dry-run"]
    r = CliRunner().invoke(cli.app, [*argv, "--json"])
    assert r.exit_code == 0, r.output
    doc = json.loads(r.stdout)
    assert doc["ok"] is True and doc["prune"] == "planned" and doc["removed"] == [R3, R5]
    assert r.stderr.strip().splitlines()[-1] == NOTE
    text = CliRunner().invoke(cli.app, argv)
    assert text.exit_code == 0, text.output
    assert text.stdout.startswith("would remove 2 release(s)")
    assert text.stdout.strip().splitlines()[-1] == NOTE


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
    assert result == {"ok": True, "prune": "done", "kept": [], "removed": [], "freed_bytes": 0}


def test_decide_keeps_the_newest_by_name_which_is_by_time():
    listing = {
        "current": "",
        "releases": [{"release": r, "bytes": 1, "status": "deployed"} for r in (R1, R2, R3)],
    }
    assert prune.decide(listing, 2)["removed"] == [R1]


def test_the_command_refuses_a_negative_keep():
    r = CliRunner().invoke(
        cli.app, ["deploy", "prune", "--ssh", "h", "--dest", "/d", "--keep", "-1"]
    )
    plain = re.sub(r"\x1b\[[0-9;]*m", "", r.output)  # CI renders usage errors in colour
    assert r.exit_code != 0 and "--keep" in plain


@pytest.mark.parametrize("answer", [False, True])
def test_prune_asks_with_what_would_go_and_removes_nothing_unless_told_yes(dest, answer):
    """#1854: a removed release cannot be brought back, so `deploy prune` asks
    (`--yes` does not): the question is decided under the same lock as the removal."""
    asked: list[dict] = []
    remote = LocalRemote()
    cfg = make_config(artifact="", ssh="uqf-server", dest=str(dest), profile="prune")
    before = _left(dest)
    code, result = prune.prune(
        cfg, remote, keep=0, confirm=lambda planned: asked.append(planned) or answer
    )
    assert [a["removed"] for a in asked] == [[R3, R5]]
    assert result["ok"] is bool(answer)
    if answer:
        assert code == 0 and result["prune"] == "done" and _left(dest) == [R1, R2, R4]
    else:
        assert code == 1 and result["prune"] == "declined" and _left(dest) == before


def test_json_without_yes_is_refused_before_the_server_is_reached(dest, monkeypatch, terminal):
    """#2229: `deploy prune --json` asked on the terminal, in the middle of a
    --json run; it is refused, exit 1, as every --json command without --yes."""
    from uqs.cli import deploy_server

    def never(*_a, **_k):
        raise AssertionError("the server was reached")

    monkeypatch.setattr(deploy_server, "Remote", lambda *a, **k: LocalRemote())
    monkeypatch.setattr(prune, "prune", never)
    argv = ["deploy", "prune", "--ssh", "h", "--dest", str(dest), "--keep", "0", "--json"]
    before = _left(dest)
    monkeypatch.setattr("sys.argv", ["uqs", *argv])  # json_requested reads it
    r = CliRunner().invoke(cli.app, argv, input="y\n")
    assert r.exit_code == 1, r.output
    doc = json.loads(r.stdout)
    assert doc["ok"] is False
    assert "--json cannot ask for confirmation: add --yes to remove releases" in doc["error"]
    assert _left(dest) == before
