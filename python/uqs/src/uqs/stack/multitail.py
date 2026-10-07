"""`uqs logs --multitail`: the same files as `logs -f`, one multitail pane each.

`logs -f` answers "what is the fleet doing"; a pane per process answers "what
is THIS process doing next to that one", which a single time-sorted stream
hides once two chatty processes interleave.
"""

from __future__ import annotations

import os
import shlex
import shutil
import sys

from uqs.logger import get_logger
from uqs.paths import UqsError, UqsPaths
from uqs.stack.logs import resolve_procnames

log = get_logger(__name__)

#: Which of a process's two log files `multitail` opens.
MULTITAIL_STREAMS = {"out": ("out",), "err": ("err",), "both": ("out", "err")}


def multitail_command(
    paths: UqsPaths,
    procs: str = "all",
    stream: str = "both",
    columns: int = 1,
    lines: int = 20,
) -> list[str]:
    """The `multitail` argv for *procs*: one pane per existing log file,
    titled with its file name, each opening on its last *lines* lines.

    Each pane runs stack/follow.py on the out_/err_<procname>.log alias
    rather than handing multitail the file: TorQ re-points the alias on every
    start and at the daily roll, and multitail's own `-f` leaves following it
    to whichever `tail` the system has - which can stay on the file the alias
    used to name. A named process with no log yet is skipped, and a name
    absent from process.csv is refused - resolve_procnames' rule.
    """
    if stream not in MULTITAIL_STREAMS:
        raise UqsError(f"--stream {stream!r} is not one of {', '.join(sorted(MULTITAIL_STREAMS))}")
    if columns < 1:
        raise UqsError(f"--columns must be at least 1, not {columns}")
    if lines < 0:
        raise UqsError(f"--lines must not be negative, not {lines}")
    procnames = resolve_procnames(paths, procs)
    files = [
        paths.torq_log(name, kind)
        for name in procnames
        for kind in MULTITAIL_STREAMS[stream]
        if paths.torq_log(name, kind).is_file()
    ]
    if not files:
        raise UqsError(
            f"no {stream} log files found for {procnames} under {paths.log_dir} "
            "- has the demo been started at least once?"
        )
    # Stacked panes are multitail's default, and it REFUSES `-s 1` ("must
    # not be 1") while still exiting 0 - so the flag is only for a split.
    argv = ["multitail"] + (["-s", str(columns)] if columns > 1 else [])
    for f in files:
        follower = [sys.executable, "-m", "uqs.stack.follow", str(lines), str(f)]
        argv += ["-t", f.name, "-l", shlex.join(follower)]
    return argv


def run_multitail(argv: list[str]) -> None:
    """Replace this process with *argv*'s multitail, which owns the terminal
    until it quits (`q`). exec rather than a child: a curses program needs
    the tty to itself, and a Python parent waiting on it has nothing to add.
    """
    binary = shutil.which(argv[0])
    if binary is None:
        raise UqsError(
            "multitail is not installed (macOS: brew install multitail; "
            "Debian/Ubuntu: apt install multitail). "
            "`uqs logs -f` follows the same files merged into one stream without it."
        )
    log.debug("exec {}", shlex.join(argv))
    os.execv(binary, argv)
