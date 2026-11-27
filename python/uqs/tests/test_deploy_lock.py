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
from uqs.deploy.config import DeployError, LockLost, StepTimedOut, make_config
from uqs.deploy.remote import Remote
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
        artifact="",
        ssh="uqf-server",
        dest=str(dest),
        profile="essential",
        break_lock="the holder died" if break_lock else None,
    )
    return Server(cfg, LocalRemote(), release="20261008T120000Z-0123456789ab")


def _held(
    dest: Path,
    *,
    age: int,
    host: str = "elsewhere",
    pid: int = 1,
    limit: int = 300,
    started: str | None = None,
):
    """A lock another deployment left, last beating `age` seconds ago."""
    d = dest / "deploy.lock"
    d.mkdir(parents=True)
    fields = f"command=push\nrelease=OLD\nuser=ops\nhost={host}\npid={pid}\nstale_after={limit}\n"
    if started is not None:
        fields += f"started={started}\n"
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
    cfg = make_config(artifact="", ssh="h", dest="/d", profile="p", command_timeout=900)
    assert lock.stale_after(cfg) == 900 + lock.MARGIN


def test_a_long_soak_does_not_make_its_own_lock_look_dead():
    cfg = make_config(artifact="", ssh="h", dest="/d", profile="p", command_timeout=900, soak=1800)
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
    with pytest.raises(
        DeployError, match=r"push of OLD by ops@elsewhere \(pid 1\).*--override break-lock"
    ):
        _server(dest).take_lock()


def test_a_lock_with_no_owner_file_is_refused_not_unreadable(tmp_path):
    """A lock taken before #867, or by a holder that died between its mkdir
    and its owner record: the directory alone, read under pipefail."""
    dest = tmp_path / "uqf"
    (dest / "deploy.lock").mkdir(parents=True)
    with pytest.raises(DeployError, match="another deployment holds.*--override break-lock"):
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


def test_break_lock_removes_a_lock_whose_pid_now_belongs_to_a_later_process(tmp_path):
    """#2692: the pid is alive, but its process began after the holder recorded
    itself - a newcomer given a dead holder's number - so the holder is gone."""
    dest = tmp_path / "uqf"
    _held(dest, age=5000, host=HERE, pid=os.getpid(), started="2000-01-01T00:00:00+00:00")
    s = _server(dest, break_lock=True)
    s.take_lock()
    assert s.locker.info()["release"] != "OLD"


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
    from uqs.deploy.preflight import PREFLIGHT

    start = PREFLIGHT.index('if [ -d "$lock" ]; then')
    return PREFLIGHT[start:]


# ------------------------------------------------------------------ removal (#2741)


def test_a_release_removes_the_lock_this_run_holds(tmp_path):
    dest = tmp_path / "uqf"
    s = _server(dest)
    s.take_lock()
    s.release_lock()
    assert not (dest / "deploy.lock").exists()


def test_a_release_leaves_a_lock_broken_and_taken_by_another_run(tmp_path, log_text):
    """The old run's `finally` once removed whatever lock stood there - the
    newcomer's, after a legitimate break-lock - and a third run went ahead."""
    dest = tmp_path / "uqf"
    old = _server(dest)
    old.take_lock()
    subprocess.run(["rm", "-rf", str(dest / "deploy.lock")], check=True)
    _held(dest, age=0)  # broken, and taken by another deployment
    old.release_lock()
    assert old.locker.info()["release"] == "OLD", "the newcomer still holds it"
    held_by = "is no longer this run's, so it was left to 2026-10-08T00:00:00+00:00 push OLD"
    assert held_by in log_text()


class TakenMeanwhile(LocalRemote):
    """Another deployment releases the judged lock and takes it again between
    the break's judgement and its removal."""

    def __init__(self, dest: Path) -> None:
        super().__init__()
        self.dest = dest

    def run(self, script: str, timeout: int, stage: str, as_login: bool = False):
        if "cksum <" in script and "rm -rf" in script:
            subprocess.run(["rm", "-rf", str(self.dest / "deploy.lock")], check=True)
            _held(self.dest, age=0, host="newcomer")
        return super().run(script, timeout, stage, as_login)


def test_a_break_removes_only_the_lock_it_judged(tmp_path):
    dest = tmp_path / "uqf"
    _held(dest, age=5000)
    s = _server(dest, break_lock=True)
    s.remote = s.locker.remote = TakenMeanwhile(dest)
    with pytest.raises(DeployError, match="not broken: it changed hands since it was judged"):
        s.take_lock()
    assert s.locker.info()["host"] == "newcomer", "the lock taken meanwhile is left standing"


def test_a_break_of_a_lock_with_no_owner_file_still_removes_it(tmp_path):
    """A holder that died between its mkdir and its owner record: an empty
    checksum is what is judged, and what is compared again."""
    dest = tmp_path / "uqf"
    (dest / "deploy.lock").mkdir(parents=True)
    (dest / "deploy.lock" / "heartbeat").write_text(f"{int(time.time()) - 5000}\n")
    s = _server(dest, break_lock=True)
    s.take_lock()
    assert s.locker.info()["host"] == HERE


class BeatMeanwhile(LocalRemote):
    """The holder beats between the break's judgement and its removal."""

    def __init__(self, dest: Path) -> None:
        super().__init__()
        self.dest = dest

    def run(self, script: str, timeout: int, stage: str, as_login: bool = False):
        if "cksum <" in script and "rm -rf" in script:
            (self.dest / "deploy.lock" / "heartbeat").write_text(f"{int(time.time())}\n")
        return super().run(script, timeout, stage, as_login)


def test_a_break_leaves_a_holder_that_beat_since_it_was_judged(tmp_path):
    """#2978: the break reads the heartbeat again in the script that removes it."""
    dest = tmp_path / "uqf"
    _held(dest, age=5000)
    s = _server(dest, break_lock=True)
    s.remote = s.locker.remote = BeatMeanwhile(dest)
    with pytest.raises(DeployError, match="not broken: it beat again"):
        s.take_lock()
    assert s.locker.info()["release"] == "OLD"


# ------------------------------------------------------------------ fencing (#2978)


def _broken_and_taken(dest: Path) -> str:
    """Another operator breaks this run's lock and takes it; its heartbeat."""
    subprocess.run(["rm", "-rf", str(dest / "deploy.lock")], check=True)
    _held(dest, age=0, host="newcomer")
    return (dest / "deploy.lock" / "heartbeat").read_text()


def test_a_beat_after_the_lock_was_broken_stops_the_run_and_leaves_the_newcomers_heartbeat(
    tmp_path,
):
    dest = tmp_path / "uqf"
    s = _server(dest)
    s.take_lock()
    theirs = _broken_and_taken(dest)
    time.sleep(1.1)
    with pytest.raises(LockLost, match="no longer this run's.*push OLD by ops@elsewhere"):
        s.beat()
    assert (dest / "deploy.lock" / "heartbeat").read_text() == theirs


def test_a_beat_after_the_lock_was_removed_stops_the_run(tmp_path):
    dest = tmp_path / "uqf"
    s = _server(dest)
    s.take_lock()
    subprocess.run(["rm", "-rf", str(dest / "deploy.lock")], check=True)
    with pytest.raises(LockLost, match="held by none"):
        s.beat()


def test_a_fenced_step_changes_nothing_once_the_lock_is_anothers(tmp_path):
    """The guard is in the step's own script: a lock lost after the last beat
    still stops it, on the server."""
    dest = tmp_path / "uqf"
    s = _server(dest)
    s.take_lock()
    s.run("start", "a destructive step", f"touch {dest}/ran", fenced=True)
    assert (dest / "ran").exists(), "while the lock is this run's, the step runs"
    (dest / "ran").unlink()
    _broken_and_taken(dest)
    with pytest.raises(LockLost):
        s.run("start", "a destructive step", f"touch {dest}/ran", fenced=True)
    assert not (dest / "ran").exists()


def test_without_the_lock_a_fenced_step_carries_no_guard(tmp_path):
    """A dry run takes no lock, and runs no destructive step; nothing to fence."""
    s = _server(tmp_path / "uqf")
    assert s.locker.guard() == []


def test_the_background_beater_keeps_the_lock_young_between_stages(tmp_path, monkeypatch):
    monkeypatch.setattr(lock, "BEAT_EVERY", 1)
    dest = tmp_path / "uqf"
    s = _server(dest)
    s.take_lock()
    try:
        (dest / "deploy.lock" / "heartbeat").write_text(f"{int(time.time()) - 500}\n")
        deadline = time.monotonic() + 10
        while int(s.locker.info()["age"]) >= 500 and time.monotonic() < deadline:
            time.sleep(0.2)
        assert int(s.locker.info()["age"]) <= 2, "no stage beat; the thread did"
    finally:
        s.release_lock()
    assert not (dest / "deploy.lock").exists()


def test_a_loss_the_background_beater_sees_stops_the_next_stage(tmp_path, monkeypatch):
    monkeypatch.setattr(lock, "BEAT_EVERY", 1)
    dest = tmp_path / "uqf"
    s = _server(dest)
    s.take_lock()
    theirs = _broken_and_taken(dest)
    deadline = time.monotonic() + 10
    while s.locker.taken_by is None and time.monotonic() < deadline:
        time.sleep(0.2)
    assert s.locker.taken_by is not None
    # the newcomer's lock is now changed by nothing but its own beats
    assert (dest / "deploy.lock" / "heartbeat").read_text() == theirs
    with pytest.raises(LockLost):
        s.beat()
    s.release_lock()
    assert (dest / "deploy.lock").is_dir(), "left to its holder"


# ------------------------------------------------- a step that outlives this side (#2979)


class TimesOut(LocalRemote):
    def run(self, script: str, timeout: int, stage: str, as_login: bool = False):
        if stage == "prepare":
            raise StepTimedOut(stage, "the remote step did not finish within 900s")
        return super().run(script, timeout, stage, as_login)


def test_after_a_step_timed_out_on_this_side_the_lock_is_kept(tmp_path, log_text):
    dest = tmp_path / "uqf"
    cfg = make_config(artifact="", ssh="uqf-server", dest=str(dest), profile="essential")
    s = Server(cfg, TimesOut(), release="20261008T120000Z-0123456789ab")
    s.take_lock()
    with pytest.raises(StepTimedOut):
        s.run("prepare", "preparing", "true")
    s.release_lock()
    assert (dest / "deploy.lock").is_dir(), "the server may still be running prepare"
    assert "is kept: the prepare step timed out on this side" in log_text()


def test_every_step_ends_on_the_server_before_this_side_stops_waiting():
    remote = Remote("deploy@uqf-server", 10)
    # 900 - connect 10 - KILL grace 10 - slack 5
    assert remote.ssh_argv(timeout=900)[-1] == "timeout -k 10 875 bash -l -s"
    svc = Remote("deploy@uqf-server", 10, remote_user="svc")
    assert svc.ssh_argv(timeout=900)[-1] == "sudo -n -iu svc timeout -k 10 875 bash -s"
    assert svc.ssh_argv(as_login=True, timeout=900)[-1] == "timeout -k 10 875 bash -s"
    assert remote.server_limit(5) == 1, "never zero, which timeout reads as no limit"


def test_the_remote_runs_each_script_under_the_servers_limit():
    seen: list[list[str]] = []

    def runner(argv, **kwargs):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    Remote("h", 10, runner=runner).run("true", 300, "lock")
    assert seen[0][-1] == "timeout -k 10 275 bash -l -s"


def test_a_client_side_timeout_is_a_step_timed_out():
    def runner(*_, **kwargs):
        raise subprocess.TimeoutExpired("ssh", kwargs["timeout"])

    with pytest.raises(StepTimedOut, match="within 30s"):
        Remote("h", 5, runner=runner).run("true", 30, "prepare")
