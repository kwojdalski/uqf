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
its start time and argv: on Linux the kernel's start time in ticks since
boot, elsewhere ps's lstart (see `process_identity`, #1167). A pid alone
outlives the process: once the OS gives it to something else, `running` would say the feed
is up and `stop` would SIGTERM a stranger (#1043). A pid whose identity no
longer matches, or a file with no identity to check, is stale: it is never
signalled. Only the start time is compared - the kernel's ticks on Linux, ps's
lstart elsewhere: it is fixed at fork, while the argv can be recorded
mid-exec, before the kernel has set it (#1398), and changes when a launcher
script execs its real program (#1492). The argv is kept for the operator and
for telling a formatting-only mismatch from a stranger.

`ps` prints the start time in the caller's locale and timezone, so the
identity is always read under a fixed one: a feed started from an en_GB
terminal and stopped from cron is the same process (#1055). A pid file
written before that, in another environment - or on Linux before #1167,
with ps's lstart - can mismatch on the start time's form alone; when the
live process's command line is the one recorded it is neither signalled nor
forgotten - its pid file stays and the operator decides.
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

log = get_logger()

#: Bound at import, so a test that stubs `subprocess.run` for a cargo build
#: does not also stub the ps that identifies the process it spawned.
_run = subprocess.run

#: The locale and timezone every identity is read in, whoever asks (#1055).
_PS_ENV = {"LC_ALL": "C", "TZ": "UTC0"}


def _ps(pid: int, *fields: str, fixed: bool = True) -> str | None:
    """ps's `fields` for `pid`, whitespace-normalised; None when it has none."""
    args = ["ps"] + [a for f in fields for a in ("-o", f"{f}=")] + ["-p", str(pid)]
    try:
        out = _run(  # noqa: S603
            args,
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, **_PS_ENV} if fixed else None,
        ).stdout
    except OSError:
        return None
    return " ".join(out.split()) or None


#: How many whitespace-separated fields ps's lstart has in the C locale:
#: "Tue Oct 20 19:43:43 2026". The identity's argv follows them.
_LSTART_FIELDS = 5

#: Where Linux keeps each process's start time as the kernel stored it.
_PROC = Path("/proc")


def _boot_ticks(pid: int) -> str | None:
    """`pid`'s start time as Linux stores it - clock ticks since boot, field 22
    of /proc/<pid>/stat - or None where there is no /proc (macOS).

    Linux's `ps -o lstart` is not this number: it adds it to a boot time ps
    derives from the wall clock at the moment it runs, so one process's lstart
    can move by a second between two calls - and stop would then refuse the
    feed it started as "another process" (#1167). The ticks never move."""
    try:
        stat = (_PROC / str(pid) / "stat").read_text()
    except OSError:
        return None
    # Field 2, the command name, is parenthesised and may itself hold spaces
    # or ")": the fixed fields start after the LAST ")", at field 3.
    fields = stat[stat.rfind(")") + 1 :].split()
    return f"boot+{fields[19]}" if len(fields) > 19 else None


def process_identity(pid: int) -> str | None:
    """`pid`'s start time and argv, whitespace-normalised, or None when it
    has no such process. The start time plus the command line is what tells
    a pid's current holder from the process that once had it.

    On Linux the start time is the kernel's own (`boot+<ticks>`), since ps's
    lstart there is recomputed from the wall clock on every call (#1167).
    Elsewhere it is ps's lstart, which macOS reads from a stored timestamp,
    read in the C locale and UTC so it does not depend on who asks (#1055)."""
    ticks = _boot_ticks(pid)
    if ticks is None:
        return _ps(pid, "lstart", "args")
    args = _ps(pid, "args")
    return f"{ticks} {args}" if args else None


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
        if identity.startswith("boot+"):
            # Linux: the kernel's start ticks alone. They are set at fork and
            # never change, and no two processes holding one pid share them.
            # The argv beside them can be read before it exists: Popen returns
            # once exec closes the child's close-on-exec fds, but the kernel
            # sets the new argv only later in that exec, so a start read in
            # between records ps's "[comm]" - and would disown its own feed
            # on every later look (#1398).
            return identity.split(" ", 1)[0] == _boot_ticks(pid)
        if not identity:
            return False
        # Elsewhere (macOS) the start time alone decides too: exec keeps the
        # pid and the start time but replaces the argv, so a launcher script
        # that execs its real program - kafka-server-start.sh execs the JVM -
        # would otherwise read as a stranger a moment after start (#1492).
        if " ".join(identity.split()[:_LSTART_FIELDS]) == _ps(pid, "lstart"):
            return True
        # A pid file from before #1055, in the caller's environment: it still
        # matches when read back in the same one.
        return identity == _ps(pid, "lstart", "args", fixed=False)

    def _unconfirmed(self) -> int | None:
        """The pid, when it is alive and not provably ours but runs the very
        command line recorded: a start time formatted in another locale or
        timezone (#1055) looks just like that, so it may well be the feed."""
        recorded = self._recorded()
        if recorded is None or not recorded[1] or self._ours():
            return None
        pid, identity = recorded
        args = _ps(pid, "args")
        if args and self._alive(pid) and identity.endswith(" " + args):
            return pid
        return None

    def _refuse_unconfirmed(self, pid: int) -> UqsError:
        return UqsError(
            f"{self.label}: cannot confirm pid {pid} is the one it started - it runs "
            "the recorded command line, but its start time was recorded in another "
            f"format. Not signalled; check it with `ps -p {pid}`, stop it by hand "
            f"if it is the feed, then remove {self.pid_path}"
        )

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
        if (unconfirmed := self._unconfirmed()) is not None:
            raise self._refuse_unconfirmed(unconfirmed)
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
        if self._unconfirmed() is not None:
            # Maybe the feed, maybe not: neither signal it nor lose track of it.
            error = self._refuse_unconfirmed(pid)
            if not missing_ok:
                raise error
            log.warning("{}", error)
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
        """What `uqs connector status` renders: running and pid, the feed's own
        fields, then the log. Strings, because it is a display table."""
        pid = self.pid()
        return {
            "running": str(self.running()),
            "pid": str(pid) if pid is not None else "",
            **extra,
            "log": str(self.log_path),
        }
