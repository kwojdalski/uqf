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

# ---------------------------------------------------------------------------
# Console highlighting (#2048)
#
# One pass over the message, quote-aware, producing (text, colour) pieces that
# are joined back into exactly the message: the highlighter only ever ADDS
# loguru colour tags around runs of the message's own characters, and escapes
# whatever loguru would otherwise read as a tag. Colours:
#
#   value after `=`        True green, False red, None dim, path bold,
#                          number magenta (negative red), other light-cyan
#   q symbol `abc          yellow, each element of `a`b`c on its own
#   date/time/timestamp    green, exactly as written (2026.11.04D13:14:56.078...)
#   uuid, 32/40/64 hex     light-blue
#
# A value is the first token after `=`; a whitespace-separated vector carries on
# for as long as each next word is itself a number, symbol, time or id - a plain
# word ends it, so prose after a value stays plain. A value that opens with `(`
# is a (nested) list, and every atom in it is coloured. Symbols, times and ids
# are coloured in running text too, because their shapes are distinctive;
# numbers and words are not. A "quoted" value is one light-cyan string; text
# in "double quotes" anywhere else, and a backticked command such as
# `uqs logs rdb1`, is never looked inside.
# ---------------------------------------------------------------------------

# One value token: whatever follows `=` up to whitespace or most
# punctuation, with digit groups (11,643) kept whole.
_KV_ATOM_RE = re.compile(r"[^\s,\)\]\[|{}<>]+(?:,[0-9]+)*")

# A q (or C-style) string literal on one line, with backslash escapes.
_QUOTED_RE = re.compile(r'"(?:[^"\\\n]|\\.)*"')

# One element of a symbol vector: `EURUSD`GBPUSD is two of these back to back.
_SYMBOL_RE = re.compile(r"`[^\s`\"();\[\]{}<>!,=|]*")

# A backticked span in prose - `uqs logs rdb1` - is a command, not symbols: its
# closing backtick is followed by neither a word character nor another
# backtick, which a symbol vector's next element always is.
_CODE_SPAN_RE = re.compile(r"`[^`\n]+`(?![\w`])")

# A word of running text, for the shapes that are coloured outside values.
_WORD_RE = re.compile(r"[^\s\"`()\[\];,!<>{}|=]+")

# Atoms inside a (list): everything but its delimiters, quotes and symbols.
_LIST_ATOM_RE = re.compile(r"[^\s()\[\];!,\"`]+")

_CLOCK = r"\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?"
_TEMPORAL_RE = re.compile(
    "|".join(
        (
            rf"\d{{4}}\.\d{{2}}\.\d{{2}}(?:[DT](?:{_CLOCK})?)?",  # q date, timestamp, datetime
            rf"\d{{4}}-\d{{2}}-\d{{2}}(?:T{_CLOCK}(?:Z|[+-]\d{{2}}:\d{{2}})?)?",  # ISO 8601
            rf"\d+D{_CLOCK}",  # q timespan
            r"\d{2}:\d{2}:\d{2}(?:\.\d+)?",  # q time / second
            r"\d{4}\.\d{2}m",  # q month
        )
    )
)

_UUID_RE = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.IGNORECASE)

# A hex digest of md5/sha1/sha256 length. A heuristic: the shape does not
# prove a hash, so it also needs a digit and a letter - an all-digit value stays
# a number, and an all-letter one a word.
_HEX_RE = re.compile(r"[0-9a-f]+", re.IGNORECASE)
_DIGEST_LENGTHS = frozenset({32, 40, 64})


def _is_id(val: str) -> bool:
    """A UUID or a hex digest, printed in one case as tools print them."""
    if val not in (val.lower(), val.upper()):
        return False
    if _UUID_RE.fullmatch(val):
        return True
    return (
        len(val) in _DIGEST_LENGTHS
        and _HEX_RE.fullmatch(val) is not None
        and any(c.isdigit() for c in val)
        and any(c.isalpha() for c in val)
    )


# What loguru reads as a colour tag, and the backslashes that escape one
# (loguru/_colorizer.py's _regex_tag). Without braces: loguru splits the
# template at each format field and brace before it looks for tags, so
# `<a{b>` reaches its tag pattern as `<a{` and `b>` - never a tag.
_LOGURU_TAG_RE = re.compile(r"(\\*)(</?(?:[fb]g\s)?[^<>\s{}]*>)")
_TRAILING_BACKSLASHES_RE = re.compile(r"\\+\Z")

# Kinds a vector may continue with: a plain word ends it.
_VECTOR_KINDS = frozenset({"number", "symbol", "temporal", "id"})

_PATH_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".py",
        ".yaml",
        ".yml",
        ".npy",
        ".parquet",
        ".json",
        ".csv",
        ".txt",
        ".pkl",
        ".pt",
        ".pth",
        ".log",
        ".db",
        ".q",
        ".k",
    }
)


def _looks_like_path(val: str) -> bool:
    if val.startswith(("/", "./", "../")):
        return True
    dot = val.rfind(".")
    return dot > 0 and val[dot:] in _PATH_EXTENSIONS


def _strong_kind(val: str) -> tuple[str, str] | None:
    """(kind, colour) for the shapes coloured even in running text."""
    if _TEMPORAL_RE.fullmatch(val):
        return "temporal", "green"
    if _is_id(val):
        return "id", "light-blue"
    return None


def _number_colour(val: str) -> str | None:
    """magenta or red for a number (with a unit, %, or as N/M), else None."""
    # Strip trailing % and common unit suffixes for numeric check
    check_val = val.rstrip("%")
    for suffix in ("us/step", "us", "ms"):
        if check_val.endswith(suffix) and len(check_val) > len(suffix):
            check_val = check_val[: -len(suffix)]
            break

    # Handle N/M fraction (e.g., step=96921/969218) and N/s rate
    if "/" in check_val:
        parts = check_val.split("/")
        if len(parts) == 2:
            left, right = parts
            left_ok = left.replace(".", "", 1).replace("-", "", 1).isdigit()
            right_ok = right == "s" or right.replace(".", "", 1).replace("-", "", 1).isdigit()
            if left_ok and right_ok:
                return "magenta"

    try:
        num = float(check_val.replace(",", ""))
    except ValueError:
        return None
    return "red" if num < 0 else "magenta"


def _value_kind(val: str) -> tuple[str, str]:
    """(kind, colour) for one value token."""
    if val == "True":
        return "bool", "green"
    if val == "False":
        return "bool", "red"
    if val == "None":
        return "none", "dim"
    if val.startswith('"') and _QUOTED_RE.fullmatch(val):
        return "string", "light-cyan"
    if val.startswith("`") and _SYMBOL_RE.fullmatch(val):
        return "symbol", "yellow"
    strong = _strong_kind(val)
    if strong:
        return strong
    if _looks_like_path(val):
        return "path", "bold"
    number = _number_colour(val)
    if number:
        return "number", number
    return "string", "light-cyan"


def _escape_markup(text: str, *, followed_by_tag: bool) -> str:
    """`text` such that loguru's colorizer prints it as it is.

    loguru reads `<...>` as a tag unless an odd number of backslashes precedes
    it, and halves a run of backslashes that precedes a tag. So a tag-shaped
    run gets its backslashes doubled plus one, and - when a tag follows this
    piece - so must a trailing run of backslashes be doubled, or it would
    escape that tag. A `<` that is not tag-shaped (`a<b c`) is left alone:
    loguru does not touch it, so neither may we.
    """
    out = _LOGURU_TAG_RE.sub(lambda m: m.group(1) * 2 + "\\" + m.group(2), text)
    if followed_by_tag:
        out = _TRAILING_BACKSLASHES_RE.sub(lambda m: m.group(0) * 2, out)
    return out


def _list_end(msg: str, start: int) -> int | None:
    """Index just past the `)` that closes the `(` at msg[start], on its line."""
    depth = 0
    i = start
    while i < len(msg):
        ch = msg[i]
        if ch == "\n":
            return None
        if ch == '"':
            m = _QUOTED_RE.match(msg, i)
            if m:
                i = m.end()
                continue
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None


class _Pieces:
    """The message as (text, colour or None) pieces, adjacent plain text merged."""

    def __init__(self) -> None:
        self.items: list[tuple[str, str | None]] = []

    def add(self, text: str, colour: str | None = None) -> None:
        if not text:
            return
        if colour is None and self.items and self.items[-1][1] is None:
            self.items[-1] = (self.items[-1][0] + text, None)
        else:
            self.items.append((text, colour))


def _symbols(msg: str, i: int, pieces: _Pieces) -> int:
    """Colour each element of the symbol vector at msg[i]; return its end."""
    while i < len(msg) and msg[i] == "`":
        m = _SYMBOL_RE.match(msg, i)
        if m is None:  # pragma: no cover - the pattern always matches a backtick
            break
        pieces.add(m.group(0), "yellow")
        i = m.end()
    return i


def _list(msg: str, start: int, end: int, pieces: _Pieces) -> None:
    """Colour every atom of the (nested) list msg[start:end]."""
    i = start
    while i < end:
        ch = msg[i]
        if ch == '"':
            m = _QUOTED_RE.match(msg, i, end)
            if m:
                pieces.add(m.group(0), "light-cyan")
                i = m.end()
                continue
        elif ch == "`":
            i = _symbols(msg[:end], i, pieces)
            continue
        else:
            m = _LIST_ATOM_RE.match(msg, i, end)
            if m:
                pieces.add(m.group(0), _value_kind(m.group(0))[1])
                i = m.end()
                continue
        pieces.add(ch)
        i += 1


def _value(msg: str, i: int, pieces: _Pieces) -> int:
    """Colour the value that starts at msg[i], just after an `=`; return its end."""
    if i >= len(msg):
        return i
    ch = msg[i]
    if ch == '"':
        m = _QUOTED_RE.match(msg, i)
        if m:
            pieces.add(m.group(0), "light-cyan")
            return m.end()
    if ch == "(":
        end = _list_end(msg, i)
        if end is not None:
            _list(msg, i, end, pieces)
            return end
    first = _element(msg, i, pieces)
    if first is None:
        return i
    i, kind = first
    # A vector: carry on over single-line whitespace while each next element
    # is a typed atom, not a word and not the next key.
    while kind in _VECTOR_KINDS:
        gap = re.match(r"[ \t]+", msg[i:])
        if gap is None:
            break
        j = i + gap.end()
        probe = _Pieces()
        nxt = _element(msg, j, probe)
        if nxt is None or nxt[1] not in _VECTOR_KINDS:
            break
        pieces.add(gap.group(0))
        for text, colour in probe.items:
            pieces.add(text, colour)
        i, kind = nxt
    return i


def _element(msg: str, i: int, pieces: _Pieces) -> tuple[int, str] | None:
    """Colour one vector element at msg[i]: (its end, its kind), or None."""
    if i < len(msg) and msg[i] == "`" and not _CODE_SPAN_RE.match(msg, i):
        return _symbols(msg, i, pieces), "symbol"
    m = _KV_ATOM_RE.match(msg, i)
    if m is None:
        return None
    kind, colour = _value_kind(m.group(0))
    pieces.add(m.group(0), colour)
    return m.end(), kind


def _pieces(msg: str) -> list[tuple[str, str | None]]:
    pieces = _Pieces()
    i = 0
    n = len(msg)
    while i < n:
        ch = msg[i]
        if ch == '"':
            m = _QUOTED_RE.match(msg, i)
            if m:
                pieces.add(m.group(0))
                i = m.end()
                continue
        elif ch == "`":
            prev = msg[i - 1] if i else ""
            m = _CODE_SPAN_RE.match(msg, i)
            if m and not (prev.isalnum() or prev in "_`"):
                pieces.add(m.group(0))
                i = m.end()
                continue
            i = _symbols(msg, i, pieces)
            continue
        elif ch == "=":
            pieces.add(ch)
            i = _value(msg, i + 1, pieces)
            continue
        else:
            m = _WORD_RE.match(msg, i)
            if m:
                word = m.group(0)
                # A sentence's full stop or colon is not part of the token.
                core = word.rstrip(".:")
                strong = _strong_kind(core) if core else None
                if strong:
                    pieces.add(core, strong[1])
                    pieces.add(word[len(core) :])
                else:
                    pieces.add(word)
                i = m.end()
                continue
        pieces.add(ch)
        i += 1
    return pieces.items


def _highlight_kv(msg: str, *, followed_by_tag: bool = False) -> str:
    """`msg` as loguru markup: its values, symbols, ids and times coloured.

    The rules are in the comment above _KV_ATOM_RE. Whatever the colours, the
    text loguru prints is `msg` exactly, colour on or off: every piece is
    escaped for loguru's colorizer here, in the same pass that colours it.
    Splitting the two is what broke before - `SERVER=<host>` reached loguru
    as `<light-cyan>\\</light-cyan><host>`, loguru raised from inside the
    handler, and the line naming the credential to set was dropped.

    `followed_by_tag`: whether the template puts a colour tag right after the
    message (`<level>{message}</level>` does), which changes what a trailing
    backslash means to loguru.
    """
    items = _pieces(msg)
    out: list[str] = []
    for index, (text, colour) in enumerate(items):
        last = index == len(items) - 1
        if colour is None:
            out.append(_escape_markup(text, followed_by_tag=followed_by_tag or not last))
        else:
            out.append(f"<{colour}>{_escape_markup(text, followed_by_tag=True)}</{colour}>")
    return "".join(out)


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
        # _highlight_kv escapes for loguru's colorizer as it colours. (In a
        # format callable, loguru strips the escaping \ and prints the rest.)
        highlighted = _highlight_kv(
            record["message"], followed_by_tag=bool(_STARTS_WITH_TAG_RE.match(after))
        )
        # Escape bare braces so format_map doesn't misinterpret message content.
        safe = highlighted.replace("{", "{{").replace("}", "}}")
        return template.replace("{message}", safe, 1) + "\n"

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
