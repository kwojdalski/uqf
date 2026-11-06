"""Following process logs live: `uqs logs -f` and `start -f` (split from stack/logs.py).

The reading, parsing, filtering and printing of single records stay in
stack/logs.py; this is the part that keeps a set of files open and feeds their
new lines through it. It calls `logs.emit` and the rest by module attribute, so
what a caller or test replaces in stack/logs.py is what is used here.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from uqs.paths import UqsError, UqsPaths
from uqs.stack import logs


def _pump(stream: Any, out_queue: Any) -> None:
    for line in stream:
        out_queue.put(line)


def follow_logs(
    paths: UqsPaths,
    procs: str = "all",
    min_level: str | None = None,
    lines: int = 20,
    channels: Iterable[str] | None = None,
) -> None:
    """The last *lines* lines of each matching process's out_/err_ log, merged
    and sorted by time as `uqs logs` prints them, then every line appended,
    live, through the shared loguru logger - Ctrl-C to stop.

    The history is the point of opening it: it used to start at the end of
    every file (`tail -n 0`), so `uqs logs -f` on a quiet process showed
    nothing at all where `uqs logs` showed its last lines, and read as broken.
    The followers start before the history is read, so a line written while
    it is printed is shown - at worst twice, never not at all.
    """
    procnames = logs.resolve_procnames(paths, procs)
    files = logs._log_files(paths, procnames)
    if not files:
        raise UqsError(f"no log yet for {procs} under {paths.log_dir} to follow - start it first")
    logs.min_level_name(min_level)  # refuse a bad --level before following anything
    chosen = logs.channel_names(channels)
    follow_files(
        files,
        min_level,
        history=lambda: logs._recent_records(files, lines, min_level, chosen),
        channels=chosen,
    )


#: How long `follow_during` waits for a log file the start has not created
#: yet. Generous: a process creates its log as it starts, but a fleet start
#: brings up dozens one after another.
AWAIT_LOG_SECONDS = 60.0


def follow_during(
    paths: UqsPaths,
    procnames: list[str],
    start: Callable[[], None],
    min_level: str | None = None,
) -> None:
    """Follow `procnames`' logs from BEFORE `start` runs, until Ctrl-C.

    The order is the point: following after the start would miss everything
    a process prints while it loads, which for fxpositions1 is forty seconds.

    A log that already exists is the previous run's, followed from its end;
    when the process starts, TorQ points the alias at this run's file and the
    follower (stack/follow.py) moves there and reads it from its first line. A
    log that does not exist yet - a first run, or after `uqs clean` -
    is waited for here and read from its first line once it appears.
    """
    expected = logs._expected_log_files(paths, procnames)
    present = [f for f in expected if f.is_file()]
    awaited = [f for f in expected if not f.is_file()]
    logs.min_level_name(min_level)
    follow_files(present, min_level, awaited=awaited, before=start)


#: What "from its first line" asks the follower for: every line it holds.
_ALL_LINES = 10**9


def _follower(path: Path, from_start: bool) -> list[str]:
    """One file's follower: stack/follow.py, which re-checks what the
    out_/err_<procname>.log alias points at. Not the system `tail -F`: TorQ
    re-points the alias on every start and at the daily roll, and whether a
    `tail` follows it there depends on which `tail` it is - BSD's can stay on
    the file the alias used to name, and the stream goes quiet."""
    lines = _ALL_LINES if from_start else 0
    return [sys.executable, "-m", "uqs.stack.follow", str(lines), str(path)]


def follow_files(
    present: list[Path],
    min_level: str | None,
    *,
    awaited: list[Path] | None = None,
    before: Callable[[], None] | None = None,
    history: Callable[[], list[dict[str, str]]] | None = None,
    parse: Callable[[str], dict[str, str] | None] = logs.parse_log_line,
    channels: tuple[str, ...] = (),
) -> None:
    import queue

    log = logs.log_sink()
    line_queue: queue.Queue[str] = queue.Queue()
    tails: list[subprocess.Popen[str]] = []
    lock = threading.Lock()
    done = threading.Event()

    def attach(path: Path, from_start: bool) -> None:
        # Under the lock, and not once `done` is set: a file that appears as
        # the command ends must not leave a tail nobody will terminate.
        with lock:
            if done.is_set():
                return
            tail = subprocess.Popen(
                _follower(path, from_start),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            tails.append(tail)
        threading.Thread(target=_pump, args=(tail.stdout, line_queue), daemon=True).start()

    def await_new(pending: list[Path]) -> None:
        deadline = time.monotonic() + AWAIT_LOG_SECONDS
        while pending and not done.is_set() and time.monotonic() < deadline:
            for path in [p for p in pending if p.is_file()]:
                pending.remove(path)
                attach(path, from_start=True)
            done.wait(0.2)

    for path in present:
        attach(path, from_start=False)

    try:
        for rec in history() if history is not None else []:
            if logs._on_channels(rec, channels):
                logs.emit(log, rec, min_level)
        if before is not None:
            before()
        if awaited:
            threading.Thread(target=await_new, args=(list(awaited),), daemon=True).start()
        while True:
            rec = parse(line_queue.get())
            if rec is not None and logs._on_channels(rec, channels):
                logs.emit(log, rec, min_level)
    except KeyboardInterrupt:
        pass
    finally:
        done.set()
        with lock:
            for tail in tails:
                tail.terminate()
