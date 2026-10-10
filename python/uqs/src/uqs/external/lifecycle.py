"""The lifecycle every external publisher shares: a detached process and its pid file.

Databento, Kafka and cryptorust's two recorders are processes this repository
starts but TorQ does not - they are not `process.csv` rows - so each needs
the same four things: start it detached with its output in a log, remember
its pid, say whether it is still alive, and stop it. Each feed module carried
its own copy of all four; they now share this one, and keep only what is
genuinely theirs - the command line, the environment, and what their status
says beyond running/pid/log (#715).

What the copies differed on is kept as a choice, not flattened:

  new_session   Databento and Kafka start in a new session, so a shell's
                Ctrl-C or hang-up does not reach them; the cryptorust
                recorders never did, and still do not.
  stop          a feed with no pid file logs "not running" (Databento, Kafka)
                or raises it (the recorders) - `missing_ok` says which.

The pid file holds the pid and, on its second line, the process's identity -
its start time and argv as `ps` reports them. A pid alone outlives the
process: once the OS gives it to something else, `running` would say the feed
is up and `stop` would SIGTERM a stranger (#1043). A pid whose identity no
longer matches, or a file with no identity to check, is stale: it is never
signalled.
"""

from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from uqs.logger import get_logger
from uqs.paths import UqsError

log = get_logger(__name__)

#: Bound at import, so a test that stubs `subprocess.run` for a cargo build
#: does not also stub the ps that identifies the process it spawned.
_run = subprocess.run


def process_identity(pid: int) -> str | None:
    """`pid`'s start time and argv, whitespace-normalised, or None when ps
    knows no such process. Start time to the second plus the command line is
    what tells a pid's current holder from the process that once had it."""
    try:
        out = _run(  # noqa: S603
            ["ps", "-o", "lstart=", "-o", "args=", "-p", str(pid)],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        ).stdout
    except OSError:
        return None
    identity = " ".join(out.split())
    return identity or None


@dataclass(frozen=True)
class DetachedProcess:
    """One external publisher's process, by its pid file and log."""

    #: How messages name it: "the kafka feed".
    label: str
    pid_path: Path
    log_path: Path

    def _recorded(self) -> tuple[int, str] | None:
        """The pid file's pid and identity ("" when it records none)."""
        if not self.pid_path.is_file():
            return None
        first, _, identity = self.pid_path.read_text().partition("\n")
        try:
            return int(first.strip()), " ".join(identity.split())
        except ValueError:
            return None

    def pid(self) -> int | None:
        """The recorded pid, or None when there is no readable pid file."""
        recorded = self._recorded()
        return recorded[0] if recorded else None

    def _alive(self, pid: int) -> bool:
        """Signal 0 asks the kernel about the process without touching it. A
        pid owned by another user (PermissionError) is not ours to report on,
        so it reads as not running - what every copy of this did."""
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def _ours(self) -> bool:
        """Whether the recorded pid is alive AND still the process we started."""
        recorded = self._recorded()
        if recorded is None or not self._alive(recorded[0]):
            return False
        pid, identity = recorded
        return bool(identity) and process_identity(pid) == identity

    def running(self) -> bool:
        """Whether the recorded process is still alive and still ours."""
        return self._ours()

    def start(
        self,
        cmd: Sequence[str],
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
        new_session: bool = True,
    ) -> int:
        """Start `cmd` detached, its output in the log, and record its pid.

        Refuses a second start while the first is alive: two consumers in
        one Kafka group split the partitions, and two recorders publish
        every row twice - either looks like it worked.
        """
        if self.running():
            raise UqsError(f"{self.label} is already running - stop it first")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("w") as log_file:
            process = subprocess.Popen(  # noqa: S603
                list(cmd),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                cwd=cwd,
                env=env,
                start_new_session=new_session,
            )
        self.pid_path.parent.mkdir(parents=True, exist_ok=True)
        self.pid_path.write_text(f"{process.pid}\n{process_identity(process.pid) or ''}\n")
        return process.pid

    def stop(self, *, missing_ok: bool = True) -> int | None:
        """SIGTERM the process and forget its pid; return the pid stopped.

        SIGTERM, not SIGKILL, so a streamer's `finally` runs - a Kafka
        consumer leaves its group cleanly instead of holding its partitions
        until the broker's session timeout.
        """
        pid = self.pid()
        if pid is None:
            if not missing_ok:
                raise UqsError(f"{self.label} is not running (no pid file)")
            self.pid_path.unlink(missing_ok=True)
            return None
        if self._alive(pid) and not self._ours():
            # The pid is held by a process we cannot show is the one we
            # started: never signal it (#1043).
            self.pid_path.unlink(missing_ok=True)
            log.warning("{}: stale pid file - pid {} is now another process", self.label, pid)
            if not missing_ok:
                raise UqsError(
                    f"{self.label}: stale pid file: pid {pid} is now another process "
                    "(its start time or command line differs) - not signalled"
                )
            return None
        if missing_ok and not self._alive(pid):
            # A stale pid file: nothing to stop. (With missing_ok=False the
            # dead pid is still reported as stopped, as the recorders always did.)
            self.pid_path.unlink(missing_ok=True)
            return None
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        self.pid_path.unlink(missing_ok=True)
        return pid

    def status(self, **extra: str) -> dict[str, str]:
        """What `uqs feed status` renders: running and pid, the feed's own
        fields, then the log. Strings, because it is a display table."""
        pid = self.pid()
        return {
            "running": str(self.running()),
            "pid": str(pid) if pid is not None else "",
            **extra,
            "log": str(self.log_path),
        }
