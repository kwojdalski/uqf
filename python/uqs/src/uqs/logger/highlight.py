"""The console log highlighter: loguru markup for one message (#2048).

Shared by every coloured console sink - `uqs logs`, `start -f`, and through
them a `job backfill --trace` run's query lines (core.py's _make_kv_format).

One pass over the message, quote-aware, producing (text, colour) pieces that
are joined back into exactly the message: the highlighter only ever ADDS
loguru colour tags around runs of the message's own characters, and escapes
whatever loguru would otherwise read as a tag. Colours:

  value after `=`        True/1b green, False/0b red, None dim, path bold,
                         number magenta (negative red), other light-cyan;
                         q-typed atoms (-187i, 2f, 0N, -0w) are numbers
  q symbol `abc          yellow, each element of `a`b`c on its own
  date/time/timestamp    green, exactly as written (2026.11.04D13:14:56.078...)
  uuid, 32/40/64 hex     light-blue

A value is the first token after `=`; a whitespace-separated vector carries on
for as long as each next word is itself a number, symbol, time or id - a plain
word ends it, so prose after a value stays plain. A value that opens with `(`
is a (nested) list, and every atom in it is coloured. Symbols, times and ids
are coloured in running text too, because their shapes are distinctive;
numbers and words are not. A "quoted" value is one light-cyan string; text
in "double quotes" anywhere else, and a backticked command such as
`uqs logs rdb1`, is never looked inside.
"""

from __future__ import annotations

import re

from uqs.logger.qatoms import q_bool_colour, q_number_colour

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
        return q_number_colour(val)  # -187i, 1.5e, 0N, -0w (.Q.s1, #2079)
    return "red" if num < 0 else "magenta"


def _value_kind(val: str) -> tuple[str, str]:
    """(kind, colour) for one value token."""
    if val == "True":
        return "bool", "green"
    if val == "False":
        return "bool", "red"
    if val == "None":
        return "none", "dim"
    q_bool = q_bool_colour(val)  # 1b, 0b, 101b
    if q_bool:
        return "bool", q_bool
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


def highlight(msg: str, *, followed_by_tag: bool = False) -> str:
    """`msg` as loguru markup: its values, symbols, ids and times coloured.

    The rules are in this module's docstring. Whatever the colours, the
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
