"""Reading TorQ's own log files through the Python logger.

Each process writes pipe-delimited lines; these are parsed back into fields
and re-emitted through loguru so a fleet's logs read as one stream with
consistent levels and formatting."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from torq_orchestrator.logger import get_logger
from torq_orchestrator.paths import UqfStackError, UqfStackPaths
from torq_orchestrator.stack.procs import list_process_names

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

# .lg.outmap's own level vocabulary (torq.q's ERROR/ERR/INF/WARN) mapped
# onto loguru's level names.
_LOGURU_LEVEL = {"ERROR": "ERROR", "ERR": "ERROR", "WARN": "WARNING", "INF": "INFO"}
_LEVEL_ORDER = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}

# Dedicated format for `logs` output - the record's own {time}/{function}/
# {line} are Python's (always core.py's _emit, useless here); the kdb
# process's own timestamp/procname/proctype (bound as `extra` below) are
# what's actually informative, so they replace them entirely rather than
# just prefixing the message.
_KDB_LOG_FMT = (
    "<green>{extra[kdb_time]}</green> | <level>{level: <8}</level> | "
    "<cyan>{extra[procname]}</cyan>/<cyan>{extra[proctype]}</cyan> - <level>{message}</level>"
)


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
    """(Re)configure the shared loguru logger with _KDB_LOG_FMT for the
    duration of a `logs` command, overriding whatever format main()'s
    configure_logging(component="uqf_stack") set up for the rest of the
    CLI - `logs` is always a leaf command, so clobbering the global sink
    here is safe.
    """
    from torq_orchestrator.logger.core import setup_logging

    return setup_logging(level="DEBUG", format_string=_KDB_LOG_FMT)


def resolve_procnames(paths: UqfStackPaths, procs: str) -> list[str]:
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

    Only the log commands route through here. `start`/`stop`/`restart`/`print`
    hand `procs` straight to the vendored torq.sh, which owns its own
    handling of an unknown name and is never edited.
    """
    if procs == "all":
        return list_process_names(paths)
    requested = procs.split()
    known = list_process_names(paths)
    unknown = [name for name in requested if name not in known]
    if unknown:
        raise UqfStackError(
            f"unknown process(es) {unknown} - known processes are {known}. "
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


def _log_files(paths: UqfStackPaths, procnames: list[str]) -> list[Path]:
    log_dir = paths.torqdata / "logs"
    files = [log_dir / f"{stream}_{name}.log" for name in procnames for stream in ("out", "err")]
    return [f for f in files if f.is_file()]


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
    paths: UqfStackPaths, procs: str = "all", lines: int = 20, min_level: str | None = None
) -> list[dict[str, str]]:
    """The last *lines* lines of each matching process's out_/err_ log,
    merged, sorted by timestamp, and filtered to min_level - the data
    print_recent_logs formats and prints through the shared loguru
    logger, and uqf_stack_mcp.py's uqf_stack_logs tool returns as-is for
    an MCP client to read directly.
    """
    procnames = resolve_procnames(paths, procs)
    files = _log_files(paths, procnames)
    if not files:
        raise UqfStackError(
            f"no log files found for {procnames} under {paths.torqdata / 'logs'} "
            "- has the demo been started at least once?"
        )

    records = []
    for f in files:
        tail = f.read_text(errors="replace").splitlines()[-lines:]
        records.extend(rec for line in tail if (rec := parse_log_line(line)) is not None)
    records.sort(key=lambda r: r["time"])

    if min_level is None:
        return records
    return [
        r for r in records if _passes_level(_LOGURU_LEVEL.get(r["loglevel"], "INFO"), min_level)
    ]


def print_recent_logs(
    paths: UqfStackPaths, procs: str = "all", lines: int = 20, min_level: str | None = None
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


def follow_logs(paths: UqfStackPaths, procs: str = "all", min_level: str | None = None) -> None:
    """Stream new lines appended to each matching process's out_/err_ log,
    live, through the shared loguru logger - Ctrl-C to stop. One `tail -F`
    subprocess per file (follows the stable alias across TorQ's own log
    rolling, no polling/inotify dependency of our own) fanned into a single
    queue by a reader thread each.
    """
    import queue
    import threading

    procnames = resolve_procnames(paths, procs)
    files = _log_files(paths, procnames)
    if not files:
        raise UqfStackError(
            f"no log files found for {procnames} under {paths.torqdata / 'logs'} "
            "- has the demo been started at least once?"
        )

    log = _configure_kdb_log_sink()
    line_queue: queue.Queue[str] = queue.Queue()
    tails = [
        subprocess.Popen(
            ["tail", "-n", "0", "-F", str(f)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        for f in files
    ]
    threads = [
        threading.Thread(target=_pump, args=(t.stdout, line_queue), daemon=True) for t in tails
    ]
    for thread in threads:
        thread.start()

    try:
        while True:
            rec = parse_log_line(line_queue.get())
            if rec is not None:
                _emit(log, rec, min_level)
    except KeyboardInterrupt:
        pass
    finally:
        for t in tails:
            t.terminate()
