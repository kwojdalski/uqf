"""Reading TorQ's own log files through the Python logger.

Each process writes pipe-delimited lines; these are parsed back into fields
and re-emitted through loguru so a fleet's logs read as one stream with
consistent levels and formatting."""

from __future__ import annotations

import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from uqs.generated import q_facts
from uqs.logger import get_logger
from uqs.paths import UqsError, UqsPaths, UqsUsageError
from uqs.stack.procs import list_process_names, unknown_processes
from uqs.stack.trace_render import render_query_trace

log = get_logger()


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
# contain "|", so split with maxsplit rather than a plain split. `id` is the
# record's CHANNEL (#2274): the subsystem it concerns, one of CHANNELS for a
# line .qetl.log wrote through a channel call. Any other id - a native TorQ
# category such as `main`, or an older line's worker name - is shown as it is,
# and never dropped; it is not a channel, so `--channel` does not select it.
_LOG_FIELDS = ("time", "host", "proctype", "procname", "loglevel", "id", "message")

# The five level names a log line carries, least to most severe. .qetl.log
# writes them (src/etl/core/log.q), and scripts/torqconfig/settings/default.q
# renames TorQ's own INF/WARN/ERR to them in every TorQ process.
_LEVEL_ORDER = {"TRACE": 5, "DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}

# The short names a log written before the rename still carries, read as the
# level each one is. Only for READING such a file: they are not accepted
# anywhere a level is chosen, and a parsed record never shows them.
LEGACY_LEVEL = {"ERR": "ERROR", "WARN": "WARNING", "INF": "INFO", "DBG": "DEBUG", "TRC": "TRACE"}

# A line's level as one of the five. An unknown one reads as INFO, so a line
# is never dropped for its label.
_LOGURU_LEVEL = {**{name: name for name in _LEVEL_ORDER}, **LEGACY_LEVEL}

#: `--channel`'s choices: the subsystems a record can concern - q's own declared
#: list (`.qetl.log.channels`), generated, so a channel added in q reaches the
#: option, its completion and its help without a second copy.
CHANNELS: tuple[str, ...] = q_facts.LOG_CHANNELS
CHANNEL_HELP = (
    "Only show lines on this channel (repeatable, any of them): "
    + ", ".join(CHANNELS)
    + ". A view filter - it does not stop other lines being written"
)

#: `--level`'s choices, least to most severe - derived, so a level added to
#: _LEVEL_ORDER reaches every command's completion and help without a third
#: copy (#609).
LEVEL_CHOICES = tuple(sorted(_LEVEL_ORDER, key=_LEVEL_ORDER.__getitem__))
LEVEL_HELP = "Only show this level and above"

# Dedicated format for `logs` output - the record's own {time}/{function}/
# {line} are Python's (always logger/core.py's emit, useless here); the kdb
# process's own timestamp/procname/proctype (bound as `extra` below) are
# what's actually informative, so they replace them entirely rather than
# just prefixing the message.
_KDB_LOG_FMT = (
    "<green>{extra[kdb_time]}</green> | <level>{level: <8}</level> | "
    "<cyan>{extra[procname]}</cyan>/<cyan>{extra[proctype]}</cyan> | "
    "<magenta>{extra[channel]}</magenta> - <level>{message}</level>"
)
# The same without a channel, for a record bound by something that has none.
_KDB_LOG_FMT_NO_CHANNEL = (
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
    leaf command, and `start -f` is `start` plus `logs -f` and keeps logging
    afterwards. Starting a stack whose data directory needed moving printed
    eight tracebacks and none of the message saying what to run.

    Returns a TEMPLATE, not a line - _make_kv_format wraps this and keeps
    the key=value highlighting for both kinds.
    """
    extra = record["extra"]
    if "kdb_time" not in extra:
        return _UQS_LOG_FMT
    return _KDB_LOG_FMT if "channel" in extra else _KDB_LOG_FMT_NO_CHANNEL


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


def log_sink() -> Any:
    """(Re)configure the shared loguru logger for the duration of a `logs`
    or `start -f` stream, overriding whatever format main()'s
    configure_logging(component="uqs") set up for the rest of the CLI.

    The format is a CALLABLE, not a string: this sink receives kdb log lines
    AND uqs's own records, and only the first kind carries the fields the
    kdb format needs. See _kdb_or_uqs_format. On stdout, unlike any other
    command's log (#1881): these lines are what `logs` and `start -f` print.
    """
    from uqs.logger.core import setup_logging

    # TRACE, not DEBUG: a TRACE line from a `--trace` backfill is a loguru TRACE
    # record, and a DEBUG sink drops it. uqs logs nothing at TRACE itself, so
    # this lets through the processes' trace lines and nothing else.
    return setup_logging(level="TRACE", format_string=_kdb_or_uqs_format, stream=sys.stdout)


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
    names expanded. `start`/`stop`/`restart`/`start --dry-run` check the same thing
    without expanding, via `procs.assert_known_procnames`.
    """
    if procs == "all":
        return list_process_names(paths)
    requested = procs.split()
    known = list_process_names(paths)
    unknown = [name for name in requested if name not in known]
    if unknown:
        raise unknown_processes(unknown)
    return requested


def parse_log_line(line: str) -> dict[str, str] | None:
    """One .lg.format line -> a field dict, or None if it doesn't match the
    expected 7-field shape (e.g. a startup banner line written before
    .lg's own redirect kicks in).
    """
    parts = line.rstrip("\n").split("|", len(_LOG_FIELDS) - 1)
    if len(parts) != len(_LOG_FIELDS):
        return None
    rec = dict(zip(_LOG_FIELDS, parts, strict=True))
    # One vocabulary out, whichever a file was written in.
    rec["loglevel"] = LEGACY_LEVEL.get(rec["loglevel"], rec["loglevel"])
    return rec


def _expected_log_files(paths: UqsPaths, procnames: list[str]) -> list[Path]:
    return [paths.torq_log(name, stream) for name in procnames for stream in ("out", "err")]


def _log_files(paths: UqsPaths, procnames: list[str]) -> list[Path]:
    return [f for f in _expected_log_files(paths, procnames) if f.is_file()]


def min_level_name(min_level: str | None) -> str | None:
    """`--level` as a loguru level name, or None for no filter.

    One of the five names the log lines print - TRACE, DEBUG, INFO, WARNING,
    ERROR - case-insensitively. Anything else is refused, naming the five:
    an unknown name used to rank as 0 and pass every line.
    """
    if min_level is None:
        return None
    name = min_level.strip().upper()
    if name not in _LEVEL_ORDER:
        raise UqsError(f"--level {min_level!r} is not a level: one of {', '.join(LEVEL_CHOICES)}")
    return name


def _passes_level(level: str, min_level: str | None) -> bool:
    wanted = min_level_name(min_level)
    if wanted is None:
        return True
    return _LEVEL_ORDER.get(level, 0) >= _LEVEL_ORDER[wanted]


def channel_names(channels: Iterable[str] | None) -> tuple[str, ...]:
    """`--channel` as a tuple of channel names, each one declared, or UqsError.

    A misspelt channel selects nothing, which reads as "that subsystem is
    quiet"; so it is refused, naming the declared ones. `--channel` used to
    mean "subscribe" (#1067), and its old use is the likelier mistake, so a value
    that is not a name says where that went.
    """
    chosen = tuple(channels or ())
    for name in chosen:
        if name not in CHANNELS:
            raise UqsUsageError(
                f"--channel {name!r} is not a channel: one of {', '.join(CHANNELS)}; "
                "subscribing to the published lines was renamed `--subscribe` (#2274)"
            )
    return chosen


def _on_channels(rec: dict[str, str], channels: tuple[str, ...]) -> bool:
    """Whether a record is on any of the channels - every record when none is chosen."""
    return not channels or rec.get("id", "") in channels


def emit(log: Any, rec: dict[str, str], min_level: str | None) -> None:
    level = _LOGURU_LEVEL.get(rec["loglevel"], "INFO")
    # a line with no level of its own (an attached run's RESULT, #1725) always shows
    if not rec.get("unleveled") and not _passes_level(level, min_level):
        return
    # A query trace's code is shown as a block; every other message as it is.
    # Here, the one path both the recent and the follow modes print through.
    log.bind(
        kdb_time=_format_kdb_time(rec["time"]),
        procname=rec["procname"],
        proctype=rec["proctype"],
        channel=rec.get("id") or "-",
    ).log(level, render_query_trace(rec["message"]))


def get_recent_logs(
    paths: UqsPaths,
    procs: str = "all",
    lines: int = 20,
    min_level: str | None = None,
    channels: Iterable[str] | None = None,
) -> list[dict[str, str]]:
    """The last *lines* lines of each matching process's out_/err_ log,
    merged, sorted by timestamp, and filtered to min_level and to any of
    `channels` (the lines are taken first, then filtered, as the level is) - the data
    print_recent_logs formats and prints through the shared loguru
    logger, and uqs.mcp's uqs_logs tool returns as-is for
    an MCP client to read directly.
    """
    if lines < 0:
        # A negative slice would skip the first lines, not keep the last (#2468).
        raise UqsUsageError(f"lines must be 0 or more, not {lines}")
    procnames = resolve_procnames(paths, procs)
    min_level_name(min_level)
    chosen = channel_names(channels)
    files = _log_files(paths, procnames)
    if not files:
        # Nothing written yet is an empty result, not a failure (#1885).
        log.info("no log yet for {} under {} - none of it has started", procs, paths.log_dir)
        return []
    return _recent_records(files, lines, min_level, chosen)


def _recent_records(
    files: list[Path], lines: int, min_level: str | None, channels: tuple[str, ...] = ()
) -> list[dict[str, str]]:
    """The last *lines* parsed lines of each file, merged by timestamp."""
    records = []
    for f in files:
        tail = f.read_text(errors="replace").splitlines()[-lines:] if lines else []
        records.extend(rec for line in tail if (rec := parse_log_line(line)) is not None)
    records.sort(key=lambda r: r["time"])

    return [
        r
        for r in records
        if (min_level is None or _passes_level(_LOGURU_LEVEL.get(r["loglevel"], "INFO"), min_level))
        and _on_channels(r, channels)
    ]


def print_recent_logs(
    paths: UqsPaths,
    procs: str = "all",
    lines: int = 20,
    min_level: str | None = None,
    channels: Iterable[str] | None = None,
) -> None:
    """Print the last *lines* lines of each matching process's out_/err_
    log, merged and sorted by timestamp, through the shared loguru logger.
    """
    records = get_recent_logs(paths, procs, lines, min_level, channels)
    log = log_sink()
    for rec in records:
        emit(log, rec, None)
