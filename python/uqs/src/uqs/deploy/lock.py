"""The deploy lock: who holds it, whether they still do, and breaking it (#867).

A push, a rollback and a prune each hold <dest>/deploy.lock/ while they run,
so two never act on one server at once. One that dies holding it - a lost
ssh session, Ctrl-C - leaves it behind, and every later run is refused. Until
this, the lock recorded only a time, so an operator could not tell a dead
holder from a live one, and removed it by hand on faith.

The holder now records itself in deploy.lock/owner: a first line a person
reads (what `uqs deploy status` shows), then name=value lines - the command,
the release, the deploying user, host and pid, and `stale_after`, how long
it may go between beats, which it derives from its own longest step. It
writes deploy.lock/heartbeat, the SERVER's clock in seconds, on taking the
lock and again between stages, so the age read back is the server's clock
against itself, never two machines' against each other.

`--break-lock` removes a lock only when its holder is gone: refused while the
heartbeat is younger than the holder's stale_after, or while the holder's pid
is alive on this very machine (from anywhere else, the heartbeat decides). A
lock from before this has no heartbeat; the directory's own age stands in.
"""

from __future__ import annotations

import getpass
import os
import socket
from collections.abc import Callable
from datetime import UTC, datetime

from uqs.deploy.config import Config, DeployError
from uqs.deploy.remote import Transport, _checked, q, script
from uqs.logger import get_logger

log = get_logger(__name__)

#: Slack past the holder's longest step before its silence means it is gone.
MARGIN = 60

#: What a lock holds, as name=value lines, then `age` - seconds since its last
#: beat on the server's clock (the directory's age for a lock without one).
#: Prints nothing when there is no lock.
INFO = r"""
if [ -d {lock} ]; then
  cat {lock}/owner 2>/dev/null | tail -n +2
  hb=$(cat {lock}/heartbeat 2>/dev/null || stat -c %Y {lock} 2>/dev/null || true)
  case "$hb" in ''|*[!0-9]*) echo "age=unknown";; *) echo "age=$(( $(date +%s) - hb ))";; esac
fi
"""


def stale_after(cfg: Config) -> int:
    """The longest a running deployment goes between beats, plus MARGIN."""
    steps = (
        cfg.command_timeout,
        cfg.verify_timeout + 60,
        cfg.smoke_timeout + 30,
        cfg.live_check_timeout + 60,
        # --soak sleeps, then reads the jobs' health with no beat between (#869)
        (cfg.soak or 0) + cfg.command_timeout,
    )
    return max(steps) + MARGIN


def owner_record(cfg: Config, command: str, release: str) -> str:
    who = f"{getpass.getuser()}@{socket.gethostname()}"
    fields = {
        "command": command,
        "release": release or "-",
        "user": getpass.getuser(),
        "host": socket.gethostname(),
        "pid": str(os.getpid()),
        "started": datetime.now(UTC).isoformat(timespec="seconds"),
        "stale_after": str(stale_after(cfg)),
    }
    head = f"{fields['started']} {command} {fields['release']} by {who} pid {fields['pid']}"
    return "\n".join([head, *(f"{k}={v}" for k, v in fields.items())])


def describe(info: dict[str, str]) -> str:
    """A held lock, as one line for a person."""
    who = f"{info.get('user', '?')}@{info.get('host', '?')}"
    age = info.get("age", "unknown")
    return (
        f"{info.get('command', 'a deployment')} of {info.get('release', '?')} by {who} "
        f"(pid {info.get('pid', '?')}), last beat {age}s ago"
    )


def verdict(
    info: dict[str, str], here: str, alive: Callable[[int], bool], default_limit: int
) -> tuple[bool, str]:
    """(may it be broken, why) - pure, so every case is tested without a server.

    A lock that records no stale_after - one from before #867 - is held to
    `default_limit`, this deployment's own, rather than to none."""
    pid, host = info.get("pid", ""), info.get("host", "")
    if host == here and pid.isdigit() and alive(int(pid)):
        return False, f"its process {pid} is still running on this machine ({host})"
    age = info.get("age", "")
    if not age.isdigit():
        return False, "its age cannot be read"
    recorded = info.get("stale_after", "")
    limit = int(recorded) if recorded.isdigit() else default_limit
    if int(age) < limit:
        return False, f"it last beat {age}s ago, within {limit}s"
    why = f"it last beat {age}s ago, past {limit}s"
    if host == here and pid.isdigit():
        why += f", and its process {pid} on {host} is gone"
    return True, why


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Lock:
    """The lock at `path`, taken and released through `remote`."""

    def __init__(self, cfg: Config, remote: Transport, path: str) -> None:
        self.cfg, self.remote, self.path = cfg, remote, path

    def info(self) -> dict[str, str]:
        r = self.remote.run(
            script(INFO.format(lock=q(self.path))), self.cfg.command_timeout, "lock"
        )
        out = _checked(r, "lock", "reading the deploy lock")
        return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)

    def take(self, command: str, release: str = "") -> None:
        if self._mkdir(owner_record(self.cfg, command, release)):
            return
        info = self.info()
        if not self.cfg.break_lock:
            raise DeployError(
                "lock",
                f"another deployment holds {self.path}: {describe(info)}. If it died, "
                "--break-lock removes it - refused while its holder still beats",
            )
        ok, why = verdict(info, socket.gethostname(), _alive, stale_after(self.cfg))
        if not ok:
            raise DeployError(
                "lock", f"{self.path} is still held, so it was not broken: {why} ({describe(info)})"
            )
        log.warning("breaking {}: {} ({})", self.path, why, describe(info))
        self.remote.run(script(f"rm -rf {q(self.path)}"), self.cfg.command_timeout, "lock")
        if not self._mkdir(owner_record(self.cfg, command, release)):
            raise DeployError("lock", f"another deployment took {self.path} as it was broken")

    def _mkdir(self, owner: str) -> bool:
        r = self.remote.run(
            script(
                f"mkdir -p {q(self.cfg.dest)}",
                f"mkdir {q(self.path)} 2>/dev/null || {{ echo held >&2; exit 3; }}",
                f"printf '%s\\n' {q(owner)} > {q(self.path)}/owner",
                f"date +%s > {q(self.path)}/heartbeat",
            ),
            self.cfg.command_timeout,
            "lock",
        )
        if r.returncode == 3:
            return False
        _checked(r, "lock", "taking the deployment lock")
        return True

    def beat(self) -> None:
        """Refresh the heartbeat between stages. Never fails a deployment: a
        beat that does not land only lets the lock look older than it is."""
        try:
            self.remote.run(
                script(f"if [ -d {q(self.path)} ]; then date +%s > {q(self.path)}/heartbeat; fi"),
                self.cfg.command_timeout,
                "lock",
            )
        except DeployError as exc:
            log.warning("could not refresh {}: {}", self.path, exc)
