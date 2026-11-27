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

`--override break-lock` removes a lock only when its holder is gone: refused while the
heartbeat is younger than the holder's stale_after, or while the holder's pid
is alive on this very machine (from anywhere else, the heartbeat decides) - unless
the process with that pid started after the holder recorded itself, so it only
inherited the number (`uqs.liveness.process_gone`, #2692). A
lock from before this has no heartbeat; the directory's own age stands in.

Removing a lock - releasing it, or breaking it - compares deploy.lock/owner in
the same server script that removes it (#2741). A release removes only the
lock whose owner is still the record this run wrote: a run whose lock was
broken, and taken by another, must not delete the newcomer's in its `finally`.
A break removes only the lock whose owner is still the one it judged gone,
so a lock released and taken again meanwhile is left to its new holder - and
only while its heartbeat, read again in that script, is still past the limit
it was judged by: a holder that beat in between keeps it (#2978).

FENCING (#2978). A holder whose lock was broken must stop, not go on beside
the run that took it. Every beat compares deploy.lock/owner with this run's
record first: it refreshes only its own heartbeat, and a beat that finds
another owner (or none) raises LockLost. The destructive steps - stopping and
starting processes, moving `current`, prune's removals - begin with the same
comparison in their own script (`guard`), so a lock lost between a beat and
the step still stops it there, on the server.

A BACKGROUND BEATER (#2978) beats every BEAT_EVERY seconds while the lock is
held. Beating only between stages left gaps that were sums of steps - the
transfer alone is an scp, then a sha and tar, then with --remote-user a copy,
each up to command_timeout - and prune's confirmation waited with no limit,
so a live holder could look past `stale_after` and be broken. A thread covers
every gap, a local wait included, without a beat in front of each of the
dozens of remote calls a deployment makes. It runs no destructive step and
raises nothing: a loss it sees is recorded, and the next beat on the main
thread raises it. The beats between stages stay, as those checkpoints.

A run whose remote step timed out on this side (StepTimedOut, #2979) keeps
the lock when it ends: the server may still be running that step. Every
step's server-side limit is shorter than `stale_after`, so by the time the
lock can be broken the step has ended.
"""

from __future__ import annotations

import getpass
import os
import socket
import subprocess
import threading
from collections.abc import Callable
from datetime import UTC, datetime

from uqs import liveness
from uqs.deploy.config import Config, DeployError, LockLost
from uqs.deploy.remote import Transport, _checked, q, script
from uqs.logger import get_logger

log = get_logger()

#: Slack past the holder's longest step before its silence means it is gone.
MARGIN = 60

#: The background beater's period, at most: a quarter of stale_after when
#: that is shorter.
BEAT_EVERY = 60

#: What a script prints, then exits LOST with, when deploy.lock is not this
#: run's: then the holder's first line, or `none`.
LOST_MARKER = "uqs-lock-lost:"
LOST = 75

#: The heartbeat as a number of seconds, in $hb - the directory's own age for
#: a lock from before #867, which has none - or empty when neither is readable.
HEARTBEAT = r"""hb=$(cat {lock}/heartbeat 2>/dev/null || stat -c %Y {lock} 2>/dev/null || true)"""

#: What a lock holds, as name=value lines, then `age` - seconds since its last
#: beat on the server's clock (the directory's age for a lock without one).
#: `owner_sum` is the owner file's checksum, empty when it has none: what a
#: break compares again before it removes the lock. Prints nothing when there
#: is no lock.
INFO = r"""
if [ -d {lock} ]; then
  {{ cat {lock}/owner 2>/dev/null || true; }} | tail -n +2
  echo "owner_sum=$( {{ cksum < {lock}/owner; }} 2>/dev/null || true)"
  {heartbeat}
  case "$hb" in ''|*[!0-9]*) echo "age=unknown";; *) echo "age=$(( $(date +%s) - hb ))";; esac
fi
"""

#: Remove the lock only while its owner file's checksum is still {judged}
#: and its last beat, on the server's clock, is still {limit}s old or more;
#: otherwise print `kept=` and why. Nothing to say when it is already gone.
BREAK = r"""
if [ -d {lock} ]; then
  {heartbeat}
  if [ "$( {{ cksum < {lock}/owner; }} 2>/dev/null || true)" != {judged} ]; then
    echo "kept=it changed hands since it was judged"
  else
    case "$hb" in
      ''|*[!0-9]*) echo "kept=its age cannot be read";;
      *) if [ $(( $(date +%s) - hb )) -lt {limit} ]; then echo "kept=it beat again"
         else rm -rf {lock}; fi;;
    esac
  fi
fi
"""

#: First line of a destructive step's script: go on only while {lock}/owner
#: is still {owner}, this run's record (#2978).
GUARD = (
    r"""[ "$(cat {lock}/owner 2>/dev/null || true)" = {owner} ] || """
    r"""{{ echo "{marker} $(head -n 1 {lock}/owner 2>/dev/null || echo none)"; exit {code}; }}"""
)

#: Refresh the heartbeat only while {lock}/owner is still {owner}.
BEAT = r"""
if [ "$(cat {lock}/owner 2>/dev/null || true)" != {owner} ]; then
  echo "{marker} $(head -n 1 {lock}/owner 2>/dev/null || echo none)"
else date +%s > {lock}/heartbeat; fi
"""

#: Remove the lock only while its owner file still holds {owner}, this run's
#: record; otherwise print `kept=` and the holder's first line.
RELEASE = r"""
if [ "$(cat {lock}/owner 2>/dev/null || true)" = {owner} ]; then rm -rf {lock}
elif [ -d {lock} ]; then echo "kept=$(head -n 1 {lock}/owner 2>/dev/null || echo unknown)"; fi
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
    return record(command, release, stale_after(cfg))


def record(command: str, release: str, limit: int) -> str:
    """deploy.lock/owner's text for a holder that beats at least every `limit`s."""
    who = f"{getpass.getuser()}@{socket.gethostname()}"
    fields = {
        "command": command,
        "release": release or "-",
        "user": getpass.getuser(),
        "host": socket.gethostname(),
        "pid": str(os.getpid()),
        "started": datetime.now(UTC).isoformat(timespec="seconds"),
        "stale_after": str(limit),
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


def limit_of(info: dict[str, str], default_limit: int) -> int:
    """The holder's recorded stale_after, else `default_limit`."""
    recorded = info.get("stale_after", "")
    return int(recorded) if recorded.isdigit() else default_limit


def lost(r: subprocess.CompletedProcess) -> str | None:
    """The holder a script found in place of this run, when it found one."""
    for line in (r.stdout or "").splitlines():
        if line.startswith(LOST_MARKER):
            return line.removeprefix(LOST_MARKER).strip() or "none"
    return None


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
    limit = limit_of(info, default_limit)
    if int(age) < limit:
        return False, f"it last beat {age}s ago, within {limit}s"
    why = f"it last beat {age}s ago, past {limit}s"
    if host == here and pid.isdigit():
        why += f", and its process {pid} on {host} is gone"
    return True, why


class Lock:
    """The lock at `path`, taken and released through `remote`."""

    def __init__(self, cfg: Config, remote: Transport, path: str) -> None:
        self.cfg, self.remote, self.path = cfg, remote, path
        #: the owner record this run wrote, once it holds the lock
        self.owner: str | None = None
        #: the holder the background beater found in this run's place
        self.taken_by: str | None = None
        #: a remote step this side stopped waiting for: the lock is kept (#2979)
        self.timed_out: str | None = None
        self._stop = threading.Event()
        self._beater: threading.Thread | None = None

    def info(self) -> dict[str, str]:
        r = self.remote.run(
            script(INFO.format(lock=q(self.path), heartbeat=HEARTBEAT.format(lock=q(self.path)))),
            self.cfg.command_timeout,
            "lock",
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
                "--override break-lock --reason REASON removes it - refused while its holder "
                "still beats",
            )
        here = socket.gethostname()
        ok, why = verdict(
            info,
            here,
            lambda pid: not liveness.process_gone(here, pid, info.get("started")),
            stale_after(self.cfg),
        )
        if not ok:
            raise DeployError(
                "lock", f"{self.path} is still held, so it was not broken: {why} ({describe(info)})"
            )
        log.warning("breaking {}: {} ({})", self.path, why, describe(info))
        judged = BREAK.format(
            lock=q(self.path),
            heartbeat=HEARTBEAT.format(lock=q(self.path)),
            judged=q(info.get("owner_sum", "")),
            limit=limit_of(info, stale_after(self.cfg)),
        )
        r = self.remote.run(script(judged), self.cfg.command_timeout, "lock")
        out = _checked(r, "lock", "breaking the deploy lock")
        if kept := next((ln for ln in out.splitlines() if ln.startswith("kept=")), None):
            raise DeployError("lock", f"{self.path} was not broken: {kept.removeprefix('kept=')}")
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
        self.owner = owner
        self._start_beater()
        return True

    def guard(self) -> list[str]:
        """The line a destructive step's script starts with: it exits LOST,
        changing nothing, unless this run still holds the lock (#2978). None
        without the lock - a dry run takes none, and changes nothing."""
        if self.owner is None:
            return []
        return [GUARD.format(lock=q(self.path), owner=q(self.owner), marker=LOST_MARKER, code=LOST)]

    def lost(self, holder: str) -> LockLost:
        return LockLost(
            "lock",
            f"{self.path} is no longer this run's - it was broken, and is held by {holder} - "
            "so this run stopped, changing nothing more on the server",
        )

    def release(self) -> None:
        """Remove the lock if this run still holds it. One broken and taken
        by another is left to its holder, said in a warning: the run's own
        outcome is what it reports, not this. After a remote step timed out
        on this side the lock is kept, said in a warning (#2979)."""
        self._stop_beater()
        if self.owner is None:
            return
        if self.timed_out is not None:
            log.warning(
                "{} is kept: the {} step timed out on this side, and the server may still be "
                "running it. It ends there within this run's stale_after, after which "
                "--override break-lock --reason REASON removes the lock",
                self.path,
                self.timed_out,
            )
            self.owner = None
            return
        r = self.remote.run(
            script(RELEASE.format(lock=q(self.path), owner=q(self.owner))),
            self.cfg.command_timeout,
            "lock",
        )
        out = _checked(r, "lock", "releasing the deploy lock")
        self.owner = None
        if out.startswith("kept="):
            holder = out.strip().removeprefix("kept=")
            log.warning("{} is no longer this run's, so it was left to {}", self.path, holder)

    def beat(self) -> None:
        """Refresh the heartbeat between stages, if the lock is still this
        run's; raise LockLost if it is not - found now, or by the background
        beater (#2978). Otherwise never fails a deployment: a beat that does
        not land only lets the lock look older than it is."""
        if self.taken_by is not None:
            raise self.lost(self.taken_by)
        if holder := self._beat():
            raise self.lost(holder)

    def _beat(self) -> str | None:
        """One beat; the holder found in this run's place, if any."""
        if self.owner is None:
            return None
        body = BEAT.format(lock=q(self.path), owner=q(self.owner), marker=LOST_MARKER)
        try:
            r = self.remote.run(script(body), self.cfg.command_timeout, "lock")
        except DeployError as exc:
            log.warning("could not refresh {}: {}", self.path, exc)
            return None
        return lost(r)

    def _start_beater(self) -> None:
        self._stop.clear()
        every = max(1, min(BEAT_EVERY, stale_after(self.cfg) // 4))

        def beating() -> None:
            while not self._stop.wait(every):
                try:
                    holder = self._beat()
                except Exception as exc:  # noqa: BLE001 - a thread's error has nowhere to go
                    log.warning("could not refresh {}: {}", self.path, exc)
                    continue
                if holder is not None:
                    log.error("{} was broken: it is held by {}", self.path, holder)
                    self.taken_by = holder
                    return

        self._beater = threading.Thread(target=beating, name="deploy-lock-beat", daemon=True)
        self._beater.start()

    def _stop_beater(self) -> None:
        self._stop.set()
        if self._beater is not None:
            # a beat in flight may outlast this: it compares the owner before
            # it writes, so one that lands after the release writes nothing
            self._beater.join(timeout=5)
            self._beater = None
