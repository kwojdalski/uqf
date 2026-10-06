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


@dataclass(frozen=True)
class DetachedProcess:
    """One external publisher's process, by its pid file and log."""

    #: How messages name it: "the kafka feed".
    label: str
    pid_path: Path
    log_path: Path

    def pid(self) -> int | None:
        """The recorded pid, or None when there is no readable pid file."""
        if not self.pid_path.is_file():
            return None
        try:
            return int(self.pid_path.read_text().strip())
        except ValueError:
            return None

    def running(self) -> bool:
        """Whether the recorded process is still alive.

        Signal 0 asks the kernel about the process without touching it. A pid
        owned by another user (PermissionError) is not ours to report on, so
        it reads as not running - what every copy of this did.
        """
        pid = self.pid()
        if pid is None:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

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
        self.pid_path.write_text(str(process.pid))
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
        if missing_ok and not self.running():
            # A stale pid file: nothing to stop. (With missing_ok=False the
            # recorded pid is signalled regardless, as the recorders always did.)
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
