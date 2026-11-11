"""Real child processes for the stop tests (#2689, #2688): one that exits on
SIGTERM, one that takes a while to, and one that ignores it - the busy q
under KDB-X every stop path has to survive."""

from __future__ import annotations

import subprocess
import sys

#: Each prints "ready" once its SIGTERM handler is in place, so no test signals
#: a child that has not yet decided what SIGTERM means to it.
_PROLOGUE = "import signal, sys, time\n"
_READY = "print('ready', flush=True)\ntime.sleep(60)\n"

#: SIGTERM ends it at once.
OBEYS = _PROLOGUE + _READY

#: SIGTERM ends it a second later: a stop must wait for it, not give up on it.
SLOW = (
    _PROLOGUE
    + "signal.signal(signal.SIGTERM, lambda *_: (time.sleep(1.0), sys.exit(0)))\n"
    + _READY
)

#: SIGTERM does nothing: only SIGKILL ends it.
IGNORES = _PROLOGUE + "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n" + _READY


def spawn(script: str, *argv: str) -> subprocess.Popen:
    """`script` as a child, returned once it has said it is ready."""
    child = subprocess.Popen(
        [sys.executable, "-c", script, *argv],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert child.stdout is not None and child.stdout.readline().strip() == "ready"
    return child


def reap(*children: subprocess.Popen) -> None:
    """SIGKILL and reap whatever a test left running."""
    for child in children:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)
        if child.stdout is not None:
            child.stdout.close()
