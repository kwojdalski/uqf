"""Running one q script as one test: the process handling every module here shares.

What scripts/test.py's `_run` did for its lanes, kept to what a test needs:

* stdin is /dev/null, so a script that ends without `exit` exits at EOF rather
  than waiting at the q prompt for a terminal (#891) - or, for a script that
  must end by its own `exit` (a timer replay racing EOF, #1338), a pipe that
  never reaches EOF;
* the process leads its own process group, and a timeout, an interrupt or any
  other way out of the wait stops the whole group - q and anything it started;
* QCMD names the interpreter the script runs on, so a script that starts a q
  of its own starts the same one, and QHOME defaults as the README says;
* the output is captured and shown when the test fails.

A q error must not pass for exiting 0 (#1808). q exits 1 when a script it was
given errors with stdin at EOF, but an error in a timer, a callback or a script
fed more input prints its `'error` and carries on - so `failure` also fails a
run whose output has q's error display in it.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

#: q's display of an uncaught error: `'` then the error, KDB-X putting a
#: timestamp first (`'2026.10.30T13:56:52.808 type`), PeachQ not (`'type`).
#: An error a script traps and prints itself does not start with `'`.
Q_ERROR = re.compile(r"^'(?:\d{4}\.\d\d\.\d\dT[\d:.]+ )?\S")

#: How long a process group has to stop on SIGTERM before SIGKILL.
GRACE_SECONDS = 5


@dataclass(frozen=True)
class QRun:
    argv: tuple[str, ...]
    returncode: int | None  #: None when it was stopped at the timeout
    output: str  #: stdout and stderr, interleaved as q wrote them
    timeout: float | None

    def errors(self) -> list[str]:
        """The lines of the output that are q's display of an uncaught error."""
        return [line for line in self.output.splitlines() if Q_ERROR.match(line)]

    def failure(self) -> str | None:
        """Why this run is not a pass, or None."""
        if self.returncode is None:
            return f"still running after {self.timeout:g}s - stopped"
        if self.returncode != 0:
            return f"exit {self.returncode}"
        errors = self.errors()
        if errors:
            return f"exit 0, but q reported an error: {errors[0]}"
        return None


def q_env(q: Path, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """This process's environment, with QCMD naming `q` and QHOME defaulted."""
    env = {**os.environ, "QCMD": str(q), **(extra or {})}
    env.setdefault("QHOME", str(Path.home() / ".kx"))
    return env


def _stop(proc: subprocess.Popen[str]) -> None:
    """Stop `proc`'s whole process group: SIGTERM, then SIGKILL."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=GRACE_SECONDS)
            return
        except subprocess.TimeoutExpired:
            continue


def run_q(
    q: Path,
    script: Path | str,
    *args: str,
    cwd: Path,
    env: Mapping[str, str],
    timeout: float,
    stdin_open: bool = False,
) -> QRun:
    """Run `q script args` from `cwd` and wait, at most `timeout` seconds."""
    argv: Sequence[str] = (str(q), str(script), *args)
    read_fd = write_fd = -1
    if stdin_open:
        read_fd, write_fd = os.pipe()
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        env=dict(env),
        stdin=read_fd if stdin_open else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        start_new_session=True,
    )
    try:
        try:
            output, _ = proc.communicate(timeout=timeout)
            code: int | None = proc.returncode
        except subprocess.TimeoutExpired:
            _stop(proc)
            code = None
            try:
                output, _ = proc.communicate(timeout=GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                # Something that left q's process group holds the output open:
                # give up on it rather than wait as long as that process lives.
                if proc.stdout:
                    proc.stdout.close()
                output = "(output not collected: a process q started holds it open)\n"
    finally:
        # An interrupt, or any other way out, leaves no process behind.
        if proc.poll() is None:
            _stop(proc)
        if stdin_open:
            os.close(read_fd)
            os.close(write_fd)
    return QRun(tuple(argv), code, output or "", timeout)


def assert_passed(run: QRun, what: str) -> None:
    """Fail the calling test, with the run's output, unless `run` passed."""
    why = run.failure()
    if why is not None:
        raise AssertionError(f"{what}: {why}\n$ {' '.join(run.argv)}\n{run.output}")
