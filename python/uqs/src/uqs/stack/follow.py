"""Follow a log file by NAME, re-resolving it on every poll: one multitail pane.

`uqs logs --multitail` runs one of these per pane (multitail's `-l`) instead
of handing multitail the file (`-f`). With `-f`, multitail follows through the
system `tail`, and what that does when the name starts pointing at a different
file depends on which `tail` it is. TorQ's `out_<procname>.log` is a symlink
it re-points with `ln -sf` on every start and at the daily roll, so a pane that
kept reading the file the link USED to name went quiet after the first
restart, while the process logged on into the new one.

This checks what the name points at whenever it runs out of lines, and
switches when that changes - after reading what the old file still held.
Standard library only: a pane per log file means dozens of these start at once.
"""

from __future__ import annotations

import os
import sys
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import TextIO


def _identity(st: os.stat_result) -> tuple[int, int]:
    return (st.st_dev, st.st_ino)


def follow(
    path: Path,
    lines: int,
    out: TextIO,
    *,
    stop: Callable[[], bool] = lambda: False,
    poll: float = 0.25,
) -> None:
    """Write `path`'s last `lines` lines to `out`, then every line appended,
    until `stop()` is true.

    A file `path` comes to name later is read from its first line: it is a
    new run's log, and all of it is new. A file that does not exist yet is
    waited for. Only whole lines are written, so a line the process is still
    writing never arrives in two halves.
    """
    handle: TextIO | None = None
    identity: tuple[int, int] | None = None
    first = True
    pending = ""
    while not stop():
        if handle is None:
            try:
                handle = path.open(errors="replace")
            except FileNotFoundError:
                time.sleep(poll)
                continue
            identity = _identity(os.fstat(handle.fileno()))
            if first:
                out.writelines(deque(handle, maxlen=lines))
                out.flush()
                first = False
        chunk = handle.readline()
        if chunk:
            pending += chunk
            if pending.endswith("\n"):
                out.write(pending)
                out.flush()
                pending = ""
            continue
        try:
            current = os.stat(path)
        except FileNotFoundError:
            time.sleep(poll)
            continue
        if _identity(current) != identity:
            # Re-pointed. What the old file got before the switch is still
            # owed - then the new one, from its start.
            rest = pending + handle.read()
            out.write(rest if not rest or rest.endswith("\n") else rest + "\n")
            out.flush()
            pending = ""
            handle.close()
            handle = None
            continue
        if current.st_size < handle.tell():
            handle.seek(0)  # truncated in place
        time.sleep(poll)


def main(argv: list[str]) -> int:
    """`python -m uqs.stack.follow LINES PATH` - what each multitail pane runs."""
    lines, path = int(argv[0]), Path(argv[1])
    try:
        follow(path, lines, sys.stdout)
    except KeyboardInterrupt, BrokenPipeError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
