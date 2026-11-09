"""Core logging functionality.

Thin wrapper around loguru: get_logger/configure_logging/setup_logging
give a small, stable API while loguru itself handles formatting, coloring,
rotation, and serialisation.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

from loguru import logger

from uqs.logger.floats import CutFloatsLogger
from uqs.logger.highlight import highlight

#: The variable that sets the log level - read by uqs's CLI and by trace_calls.
LOG_LEVEL_ENV = "LOG_LEVEL"

if TYPE_CHECKING:
    # loguru declares Record in its stub but does not export it at runtime,
    # so this import has to be type-checking-only. Annotating the filter
    # with `dict` instead makes `logger.add` match no overload: loguru's
    # FilterFunction is Callable[[Record], bool], and Record is a TypedDict.
    from loguru import Record

# ---------------------------------------------------------------------------
# Level-check helper (replaces logging.Logger.isEnabledFor)
# ---------------------------------------------------------------------------


def is_level_enabled(level: str) -> bool:
    """Return True if messages at *level* would reach at least one handler."""
    try:
        # Deliberate private-API read: loguru exposes no public way to ask
        # for the minimum enabled level, and the whole call is wrapped in a
        # try/except that returns True if loguru's internals ever change
        # shape. Narrow ignore rather than a rule-wide relaxation.
        return logger.level(level).no >= logger._core.min_level  # ty: ignore[unresolved-attribute]
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_DEFAULT_FMT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)

_PLAIN_FMT = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}"

#: A template's text right after {message} starts with a loguru tag.
_STARTS_WITH_TAG_RE = re.compile(r"</?(?:[fb]g\s)?[^<>\s]*>")


def _make_kv_format(fmt: str | Callable[[dict], str]) -> Any:
    """Return a loguru format callable that highlights key=value pairs.

    `fmt` may be a template string, or a callable choosing one PER RECORD.
    The second form exists because one sink can receive records of more than
    one shape - see stack/logs.py's _kdb_or_uqs_format.
    """

    def _format(record: dict) -> str:
        template = fmt if isinstance(fmt, str) else fmt(record)
        after = template.partition("{message}")[2]
        # highlight() escapes for loguru's colorizer as it colours. (In a
        # format callable, loguru strips the escaping \ and prints the rest.)
        highlighted = highlight(
            record["message"], followed_by_tag=bool(_STARTS_WITH_TAG_RE.match(after))
        )
        # Escape bare braces so format_map doesn't misinterpret message content.
        safe = highlighted.replace("{", "{{").replace("}", "}}")
        # loguru appends a record's traceback only to a template format, never
        # to a callable's: name it here, or log.exception prints none (#2635)
        tail = "\n{exception}" if record.get("exception") is not None else "\n"
        return template.replace("{message}", safe, 1) + tail

    return _format


def _colorize() -> bool | None:
    """loguru's `colorize` for a console sink that wants colour.

    Not True, which forced escape codes into `uqs ... | grep` and into
    captured output. None lets loguru colour a terminal and leave a pipe or a
    file plain. NO_COLOR and FORCE_COLOR (no-color.org, force-color.org) are
    read here because the loguru this tree pins, 0.7.3, reads neither.
    """
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return None


def json_requested(argv: list[str] | None = None) -> bool:
    """Whether this command line asks for `--json` (#1685).

    A command run with --json promises exactly one JSON document on stdout, so
    a log line there - a refusal's `ERROR | ...` first of all - is what makes
    the refusal unparseable. Read from the command line rather than from the
    command's own option because logging is configured before Typer parses
    anything (cli/entry.py), and a refusal can be logged before the option is
    reached. Words after `--` are arguments, not options."""
    words = sys.argv[1:] if argv is None else argv
    if "--" in words:
        words = words[: words.index("--")]
    return "--json" in words


def setup_logging(
    level: str = "INFO",
    log_file: str | None = None,
    console_output: bool = True,
    colored_output: bool = True,
    structured_logging: bool = False,
    max_file_size: int = 10 * 1024 * 1024,  # 10 MB
    backup_count: int = 5,
    format_string: str | Callable[[dict], str] | None = None,
    log_regex: str | None = None,
    stream: TextIO | None = None,
) -> Any:
    """Configure loguru sinks and return the logger.

    The console sink writes to `stream`, or to stderr when it is None - with
    --json or without (#1881). A log line is never a command's data: stdout
    carries only what the command prints through its console or
    json_out.emit, so `uqs process list > f` holds the list,
    `$(uqs config show ...)` the config, and `2>/dev/null` silences the log.
    It used to be stdout outside --json (#1685 moved only --json's). Read at
    call time, so a test that swaps sys.stderr is honoured."""
    logger.remove()

    if log_regex is None:
        log_regex = os.environ.get("LOG_REGEX")

    compiled_re = None
    if log_regex:
        try:
            compiled_re = re.compile(log_regex)
        except re.error:
            # logger has no sinks yet at this point in setup, so print is the only way out
            print(f"Invalid LOG_REGEX ignored: {log_regex}", file=sys.stderr)  # noqa: T201

    def _filter(record: Record) -> bool:
        if compiled_re is not None:
            return bool(compiled_re.search(record["message"]))
        return True

    if format_string is None:
        fmt = _DEFAULT_FMT if (colored_output and not structured_logging) else _PLAIN_FMT
    else:
        fmt = format_string

    if console_output:
        use_color = colored_output and not structured_logging
        # _make_kv_format either way when fmt is a chooser: loguru's own
        # `format=` takes a callable over its Record type, and the wrapper is
        # what turns a per-record template choice into one.
        console_fmt = _make_kv_format(fmt) if use_color or callable(fmt) else fmt
        logger.add(
            stream or sys.stderr,
            level=level.upper(),
            format=console_fmt,
            colorize=_colorize() if use_color else False,
            serialize=structured_logging,
            filter=_filter,
        )

    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            log_file,
            level=level.upper(),
            rotation=max_file_size,
            retention=backup_count,
            serialize=structured_logging,
            encoding="utf-8",
        )

    # Silence noisy third-party stdlib loggers that don't use loguru
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("selectors").setLevel(logging.WARNING)

    return logger


def get_logger() -> Any:
    """Return the loguru logger singleton, with floats in a log call's
    arguments cut to LOG_FLOAT_DECIMALS places."""
    return CutFloatsLogger(logger)


def configure_logging(
    component: str = "",
    debug: bool = False,
    level: str = "INFO",
    simplified: bool = True,
    log_dir: str | None = None,
    structured_logging: bool = False,
    include_console: bool = True,
    log_regex: str | None = None,
    stream: TextIO | None = None,
) -> Any:
    """Configure logging for a named component (sets up its own log file if log_dir is given)."""
    effective_level = "DEBUG" if debug else level

    log_file = None
    if log_dir:
        log_dir_path = Path(log_dir)
        log_dir_path.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%d")
        log_file = log_dir_path / f"{component}_{timestamp}.log"

    if simplified:
        # The level, coloured, then the message. This used to be
        # "{function}:{line} - {message}", with no colour markup at all - so
        # every command configured this way printed plain text however
        # colourful the rest of this module is - and the location it showed
        # was the helper's, not the caller's: every refusal read `_die:120`.
        # The location is worth its width only when debugging.
        fmt = "<level>{level: <8}</level> | "
        if effective_level == "DEBUG":
            fmt += "<cyan>{name}:{function}:{line}</cyan> - "
        fmt += "<level>{message}</level>"
    else:
        fmt = (
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | "
            f"{component} | <cyan>{{name}}:{{function}}:{{line}}</cyan> - "
            "<level>{message}</level>"
        )

    return setup_logging(
        level=effective_level,
        log_file=str(log_file) if log_file else None,
        console_output=include_console,
        colored_output=not structured_logging,
        structured_logging=structured_logging,
        format_string=fmt if not structured_logging else None,
        log_regex=log_regex,
        stream=stream,
    )
