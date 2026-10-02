"""Reading TorQ's own log files through the Python logger.

Each process writes pipe-delimited lines; these are parsed back into fields
and re-emitted through loguru so a fleet's logs read as one stream with
consistent levels and formatting."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from uqs.logger import get_logger
from uqs.paths import UqsError, UqsPaths
from uqs.stack.procs import list_process_names

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# logs - tail each process's out_/err_ log through the Python logger instead
# of raw per-process files. No TorQ-side changes: torq.q's own
# createlog/fileredirect (torq.q:485-489) already maintains
# out_<procname>.log/err_<procname>.log as stable symlink aliases onto the
# current run's timestamped file (dropping the timestamp suffix - see
# `-noredirectalias`), and .lg.format's default (non-jsonlogs) output is a
# fixed 7-field pipe-delimited line - see parse_log_line.
# ---------------------------------------------------------------------------

# .lg.format's plain (non-jsonlogs) line shape, torq.q:203-206:
# time|host|proctype|procname|loglevel|id|message - message itself may
# contain "|", so split with maxsplit rather than a plain split.
_LOG_FIELDS = ("time", "host", "proctype", "procname", "loglevel", "id", "message")

# .lg.outmap's own level vocabulary (torq.q's ERROR/ERR/INF/WARN) plus the two
# .qetl.log adds below it (DBG, TRC - src/etl/core/log.q), mapped onto
# loguru's level names. DBG and TRC were missing, so every debug and trace
# line fell through to INFO: labelled INFO, and kept by `--level INFO`.
_LOGURU_LEVEL = {
    "ERROR": "ERROR",
    "ERR": "ERROR",
    "WARN": "WARNING",
    "INF": "INFO",
    "DBG": "DEBUG",
    "TRC": "TRACE",
}
_LEVEL_ORDER = {"TRACE": 5, "DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}

# Dedicated format for `logs` output - the record's own {time}/{function}/
# {line} are Python's (always logger/core.py's _emit, useless here); the kdb
# process's own timestamp/procname/proctype (bound as `extra` below) are
# what's actually informative, so they replace them entirely rather than
# just prefixing the message.
_KDB_LOG_FMT = (
    "<green>{extra[kdb_time]}</green> | <level>{level: <8}</level> | "
    "<cyan>{extra[procname]}</cyan>/<cyan>{extra[proctype]}</cyan> - <level>{message}</level>"
)

# uqs's OWN records, which have none of those fields. Short because these
# interleave with hundreds of kdb lines, where module:function:line is noise.
_UQS_LOG_FMT = "<level>{level: <8}</level> | <level>{message}</level>"


def _kdb_or_uqs_format(record: Any) -> str:
    """Pick a format per RECORD, because this sink receives two kinds.

    The kdb format needs extra[kdb_time]/[procname]/[proctype], which a line
    parsed from a TorQ log has and a record uqs logs itself does not. Loguru
    formats with `format_map`, so a missing key raises inside the handler,
    which prints the record and a traceback and DROPS the message.

    That was live: replacing the global sink was safe while `logs` was a
    leaf command, and `up` is `start` plus `logs -f` and keeps logging
    afterwards. Starting a stack whose data directory needed moving printed
    eight tracebacks and none of the message saying what to run.

    Returns a TEMPLATE, not a line - _make_kv_format wraps this and keeps
    the key=value highlighting for both kinds.
    """
    return _KDB_LOG_FMT if "kdb_time" in record["extra"] else _UQS_LOG_FMT


def _format_kdb_time(t: str) -> str:
    """Trim .lg.format's nanosecond timestamp to millisecond precision for
    display, e.g. '2026.08.22D14:21:10.644413000' -> '2026.08.22D14:21:10.644'.
    """
    # Only a dot AFTER the D separator is a seconds fraction. Partitioning on
    # the last dot in the whole string found the date's dot whenever the
    # seconds had no fraction, and "2026.08.22D14:21:10" displayed as
    # "2026.08.22D" - the time of day silently discarded.
    date, d, clock = t.partition("D")
    if not d:
        return t
    whole, dot, frac = clock.partition(".")
    return f"{date}D{whole}.{frac[:3]}" if dot else t


def _configure_kdb_log_sink() -> Any:
    """(Re)configure the shared loguru logger for the duration of a `logs`
    or `up` stream, overriding whatever format main()'s
    configure_logging(component="uqs") set up for the rest of the CLI.

    The format is a CALLABLE, not a string: this sink receives kdb log lines
    AND uqs's own records, and only the first kind carries the fields the
    kdb format needs. See _kdb_or_uqs_format.
    """
    from uqs.logger.core import setup_logging

    # TRACE, not DEBUG: a TRC line from a `--trace` backfill is a loguru TRACE
    # record, and a DEBUG sink drops it. uqs logs nothing at TRACE itself, so
    # this lets through the processes' trace lines and nothing else.
    return setup_logging(level="TRACE", format_string=_kdb_or_uqs_format)


def resolve_procnames(paths: UqsPaths, procs: str) -> list[str]:
    """'all' -> every process.csv row (not just startwithall=1 - a stopped
    process's last-run log is still worth reading); otherwise the given
    space-separated names, each of which must name a real process.

    These names used to be unvalidated, and the mixed case was the bad
    one. `logs posbook1 typo1` silently dropped the typo and returned
    posbook1's log as though one process had been asked for - so a reader
    diagnosing a quiet process saw an empty section and concluded it was
    idle, when in fact they had misspelled its name.

    The distinction that makes this fixable rather than a trade-off: a name
    ABSENT FROM process.csv is a typo and gets reported, while a name present
    with NO LOG FILE YET is legitimate and is still skipped downstream in
    `_log_files` - a process that has never started has no log, and
    complaining about that on every invocation would be noise. The old
    docstring conflated the two by calling both "just skipped".

    Only the log commands route through here, because only they need the
    names expanded. `start`/`stop`/`restart`/`start --print` check the same thing
    without expanding, via `procs.assert_known_procnames`.
    """
    if procs == "all":
        return list_process_names(paths)
    requested = procs.split()
    known = list_process_names(paths)
    unknown = [name for name in requested if name not in known]
    if unknown:
        raise UqsError(
            f"unknown process(es) {unknown} - known processes are {sorted(known)}. "
            "A process that exists but has never started has no log file yet; "
            "that case is skipped silently rather than reported here."
        )
    return requested


def parse_log_line(line: str) -> dict[str, str] | None:
    """One .lg.format line -> a field dict, or None if it doesn't match the
    expected 7-field shape (e.g. a startup banner line written before
    .lg's own redirect kicks in).
    """
    parts = line.rstrip("\n").split("|", len(_LOG_FIELDS) - 1)
    if len(parts) != len(_LOG_FIELDS):
        return None
    return dict(zip(_LOG_FIELDS, parts, strict=True))


def _expected_log_files(paths: UqsPaths, procnames: list[str]) -> list[Path]:
    log_dir = paths.torqdata / "logs"
    return [log_dir / f"{stream}_{name}.log" for name in procnames for stream in ("out", "err")]


def _log_files(paths: UqsPaths, procnames: list[str]) -> list[Path]:
    return [f for f in _expected_log_files(paths, procnames) if f.is_file()]


def _passes_level(level: str, min_level: str | None) -> bool:
    if min_level is None:
        return True
    return _LEVEL_ORDER.get(level, 0) >= _LEVEL_ORDER.get(min_level.upper(), 0)


def _emit(log: Any, rec: dict[str, str], min_level: str | None) -> None:
    level = _LOGURU_LEVEL.get(rec["loglevel"], "INFO")
    if not _passes_level(level, min_level):
        return
    log.bind(
        kdb_time=_format_kdb_time(rec["time"]), procname=rec["procname"], proctype=rec["proctype"]
    ).log(level, rec["message"])


def get_recent_logs(
    paths: UqsPaths, procs: str = "all", lines: int = 20, min_level: str | None = None
) -> list[dict[str, str]]:
    """The last *lines* lines of each matching process's out_/err_ log,
    merged, sorted by timestamp, and filtered to min_level - the data
    print_recent_logs formats and prints through the shared loguru
    logger, and uqs.mcp's uqs_logs tool returns as-is for
    an MCP client to read directly.
    """
    procnames = resolve_procnames(paths, procs)
    files = _log_files(paths, procnames)
    if not files:
        raise UqsError(
            f"no log files found for {procnames} under {paths.torqdata / 'logs'} "
            "- has the demo been started at least once?"
        )

    return _recent_records(files, lines, min_level)


def _recent_records(files: list[Path], lines: int, min_level: str | None) -> list[dict[str, str]]:
    """The last *lines* parsed lines of each file, merged by timestamp."""
    records = []
    for f in files:
        tail = f.read_text(errors="replace").splitlines()[-lines:] if lines else []
        records.extend(rec for line in tail if (rec := parse_log_line(line)) is not None)
    records.sort(key=lambda r: r["time"])

    if min_level is None:
        return records
    return [
        r for r in records if _passes_level(_LOGURU_LEVEL.get(r["loglevel"], "INFO"), min_level)
    ]


def print_recent_logs(
    paths: UqsPaths, procs: str = "all", lines: int = 20, min_level: str | None = None
) -> None:
    """Print the last *lines* lines of each matching process's out_/err_
    log, merged and sorted by timestamp, through the shared loguru logger.
    """
    records = get_recent_logs(paths, procs, lines, min_level)
    log = _configure_kdb_log_sink()
    for rec in records:
        _emit(log, rec, None)


def _pump(stream: Any, out_queue: Any) -> None:
    for line in stream:
        out_queue.put(line)


def follow_logs(
    paths: UqsPaths, procs: str = "all", min_level: str | None = None, lines: int = 20
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
    procnames = resolve_procnames(paths, procs)
    files = _log_files(paths, procnames)
    if not files:
        raise UqsError(
            f"no log files found for {procnames} under {paths.torqdata / 'logs'} "
            "- has the demo been started at least once?"
        )
    _follow(files, min_level, history=lambda: _recent_records(files, lines, min_level))


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
    log that does not exist yet - a first run, or after `uqs remove output` -
    is waited for here and read from its first line once it appears.
    """
    expected = _expected_log_files(paths, procnames)
    present = [f for f in expected if f.is_file()]
    awaited = [f for f in expected if not f.is_file()]
    _follow(present, min_level, awaited=awaited, before=start)


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


def _follow(
    present: list[Path],
    min_level: str | None,
    *,
    awaited: list[Path] | None = None,
    before: Callable[[], None] | None = None,
    history: Callable[[], list[dict[str, str]]] | None = None,
) -> None:
    import queue

    log = _configure_kdb_log_sink()
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
            _emit(log, rec, None)
        if before is not None:
            before()
        if awaited:
            threading.Thread(target=await_new, args=(list(awaited),), daemon=True).start()
        while True:
            rec = parse_log_line(line_queue.get())
            if rec is not None:
                _emit(log, rec, min_level)
    except KeyboardInterrupt:
        pass
    finally:
        done.set()
        with lock:
            for tail in tails:
                tail.terminate()
