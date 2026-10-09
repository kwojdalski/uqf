"""The deploy lock (#867): who holds it, a heartbeat between stages, and
--break-lock, which removes only a lock whose holder has stopped beating.

The remote here runs each script with bash against a destination under
tmp_path, so the lock is the server's own directory and clock.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
from pathlib import Path

import pytest

from uqs.deploy import lock
from uqs.deploy.config import DeployError, make_config
from uqs.deploy.server import Server

HERE = socket.gethostname()


class LocalRemote:
    def __init__(self) -> None:
        self.scripts: list[tuple[str, str]] = []

    def run(self, script: str, timeout: int, stage: str, as_login: bool = False):
        self.scripts.append((stage, script))
        return subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, timeout=timeout, check=False
        )

    def put(self, local: Path, remote_path: str, timeout: int, stage: str) -> None:
        raise AssertionError("locking copies nothing")


def _server(dest: Path, break_lock: bool = False) -> Server:
    cfg = make_config(
        artifact="", host="uqf-server", dest=str(dest), profile="essential", break_lock=break_lock
    )
    return Server(cfg, LocalRemote(), release="20261008T120000Z-0123456789ab")


def _held(dest: Path, *, age: int, host: str = "elsewhere", pid: int = 1, limit: int = 300):
    """A lock another deployment left, last beating `age` seconds ago."""
    d = dest / "deploy.lock"
    d.mkdir(parents=True)
    fields = f"command=push\nrelease=OLD\nuser=ops\nhost={host}\npid={pid}\nstale_after={limit}\n"
    (d / "owner").write_text("2026-10-08T00:00:00+00:00 push OLD by ops@elsewhere pid 1\n" + fields)
    (d / "heartbeat").write_text(f"{int(time.time()) - age}\n")


# ------------------------------------------------------------------ verdict

DEAD, ALIVE = (lambda pid: False), (lambda pid: True)


@pytest.mark.parametrize(
    ("info", "alive", "ok", "why"),
    [
        ({"age": "30", "stale_after": "300", "host": "x", "pid": "9"}, DEAD, False, "within 300s"),
        ({"age": "900", "stale_after": "300", "host": "x", "pid": "9"}, DEAD, True, "past 300s"),
        ({"age": "900", "stale_after": "300", "host": HERE, "pid": "9"}, ALIVE, False,
         "still running on this machine"),
        ({"age": "900", "stale_after": "300", "host": HERE, "pid": "9"}, DEAD, True,
         "its process 9 on"),
        ({"age": "100"}, DEAD, False, "within 1020s"),
        ({"age": "2000"}, DEAD, True, "past 1020s"),
        ({"age": "unknown", "stale_after": "300"}, DEAD, False, "cannot be read"),
    ],
)  # fmt: skip
def test_only_a_holder_that_stopped_beating_may_be_broken(info, alive, ok, why):
    """A lock from before #867 records no limit: this deployment's own (1020s
    for the default timeouts) stands in, never none."""
    got, reason = lock.verdict(info, HERE, alive, 1020)
    assert got is ok and why in reason


def test_the_limit_is_the_holders_longest_step_and_some():
    cfg = make_config(artifact="", host="h", dest="/d", profile="p", command_timeout=900)
    assert lock.stale_after(cfg) == 900 + lock.MARGIN


def test_a_long_soak_does_not_make_its_own_lock_look_dead():
    cfg = make_config(artifact="", host="h", dest="/d", profile="p", command_timeout=900, soak=1800)
    assert lock.stale_after(cfg) == 1800 + 900 + lock.MARGIN


# ------------------------------------------------------- on a real directory


def test_taking_the_lock_records_its_holder_and_a_heartbeat(tmp_path):
    s = _server(tmp_path / "uqf")
    s.take_lock()
    info = s.locker.info()
    assert (info["command"], info["host"], info["pid"]) == ("push", HERE, str(os.getpid()))
    assert info["release"] == "20261008T120000Z-0123456789ab" and int(info["age"]) <= 2
    first = (tmp_path / "uqf" / "deploy.lock" / "owner").read_text().splitlines()[0]
    assert "push 20261008T120000Z-0123456789ab by" in first, "status shows the first line"


def test_a_beat_refreshes_the_heartbeat(tmp_path):
    dest = tmp_path / "uqf"
    s = _server(dest)
    s.take_lock()
    (dest / "deploy.lock" / "heartbeat").write_text(f"{int(time.time()) - 500}\n")
    assert int(s.locker.info()["age"]) >= 500
    s.beat()
    assert int(s.locker.info()["age"]) <= 2


def test_a_held_lock_is_refused_naming_its_holder(tmp_path):
    dest = tmp_path / "uqf"
    _held(dest, age=10)
    with pytest.raises(DeployError, match=r"push of OLD by ops@elsewhere \(pid 1\).*--break-lock"):
        _server(dest).take_lock()


def test_a_lock_with_no_owner_file_is_refused_not_unreadable(tmp_path):
    """A lock taken before #867, or by a holder that died between its mkdir
    and its owner record: the directory alone, read under pipefail."""
    dest = tmp_path / "uqf"
    (dest / "deploy.lock").mkdir(parents=True)
    with pytest.raises(DeployError, match="another deployment holds.*--break-lock"):
        _server(dest).take_lock()


def test_break_lock_refuses_a_holder_that_still_beats(tmp_path):
    dest = tmp_path / "uqf"
    _held(dest, age=10)
    # 10s, or 11s if a second ticks over between writing the beat and
    # reading it - which CI's runner did (#951)
    with pytest.raises(DeployError, match=r"not broken: it last beat 1[01]s ago, within 300s"):
        _server(dest, break_lock=True).take_lock()
    assert "OLD" in (dest / "deploy.lock" / "owner").read_text(), "left as it was"


def test_break_lock_refuses_a_live_process_on_this_machine_however_old_its_beat(tmp_path):
    dest = tmp_path / "uqf"
    _held(dest, age=5000, host=HERE, pid=os.getpid())
    with pytest.raises(DeployError, match="still running on this machine"):
        _server(dest, break_lock=True).take_lock()


def test_break_lock_removes_a_stale_lock_and_takes_it(tmp_path, caplog):
    dest = tmp_path / "uqf"
    _held(dest, age=5000)
    s = _server(dest, break_lock=True)
    s.take_lock()
    assert s.locker.info()["host"] == HERE and s.locker.info()["release"] != "OLD"


def test_break_lock_removes_a_lock_whose_process_here_is_gone(tmp_path):
    proc = subprocess.Popen(["true"])
    proc.wait()
    dest = tmp_path / "uqf"
    _held(dest, age=5000, host=HERE, pid=proc.pid)
    _server(dest, break_lock=True).take_lock()


def test_preflight_reports_the_holder_and_how_long_since_it_beat(tmp_path):
    dest = tmp_path / "uqf"
    _held(dest, age=42)
    out = subprocess.run(
        ["bash", "-c", f'lock={dest}/deploy.lock\n' + _lock_lines()],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    assert out.startswith("locked=2026-10-08T00:00:00+00:00 push OLD by ops@elsewhere pid 1")
    assert ", last beat 4" in out and "s ago" in out


def _lock_lines() -> str:
    from uqs.deploy.server import PREFLIGHT

    start = PREFLIGHT.index('if [ -d "$lock" ]; then')
    return PREFLIGHT[start:]
