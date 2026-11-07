"""Refuse a markout that copies market data into its heap, or reads its depth (#2288).

Market data can run to tens of terabytes. A markout's as-of join runs against the
memory-mapped HDB partitions (src/etl/core/mapped_books.q) and its memory must grow with
its executions, never with the market. Two things break that, and both are visible in the
text of the code on the markout's path:

* a `select` of `market_data` with a `where` beyond the date. `select ... from market_data
  where date=d` keeps every column memory-mapped; any other constraint (`sym in`, a time
  range, a source) materialises a copy of every column it names. The `where` of a
  markout-path query may constrain `date` and nothing else.
* a depth column. A markout prices from the flat top of book (`bid`, `ask`); a ladder
  (`bid_prices`, `ask_prices`, `bid_sizes`, `ask_sizes`) is a nested column the select would
  copy, and `.qf.mkt.top_sides` is what read it. The depth reader (`depth_at`, in
  mapped_depth.q) is deliberately off this path, and nothing on it may call it.

WHAT IS SCANNED is the single list PATH below: the markout core and its reference, the
three domains' transforms, the three bounded sources and their workers, and the live jobs.
Comments are not code and are not scanned. Add a file to the list to bring it under the
rule.

    python3 scripts/gates/check.py markout-mapped
"""

from __future__ import annotations

import re
from pathlib import Path

#: The markout's path: every file that reads market_data for a markout, or scores it.
PATH = (
    "src/etl/core/mapped_books.q",
    "src/etl/core/reference_variant.q",
    "src/etl/transforms/markout_core.q",
    "src/etl/transforms/config_markout.q",
    "src/etl/transforms/fx_markout.q",
    "src/etl/transforms/fx_markout_outputs.q",
    "src/etl/transforms/fx_domain.q",
    "src/etl/transforms/crypto_markout.q",
    "src/etl/transforms/crypto_markout_outputs.q",
    "src/etl/transforms/eq_domain.q",
    "src/etl/transforms/eq_markout_outputs.q",
    "src/etl/sources/hdb_fx_markouts.q",
    "src/etl/sources/hdb_crypto_markouts.q",
    "src/etl/sources/hdb_eq_markouts.q",
    "src/etl/workers/hdb_fx_markouts_backfill.q",
    "src/etl/workers/hdb_fx_markout_wide_backfill.q",
    "src/etl/workers/hdb_crypto_markout_backfill.q",
    "src/etl/workers/hdb_eq_markout_backfill.q",
    "src/etl/streaming/fx_markout.q",
    "src/etl/streaming/fx_markout_wide.q",
    "src/etl/streaming/crypto_markout.q",
    "src/etl/streaming/eq_markout.q",
)

#: Names a markout-path file may not use.
FORBIDDEN = ("bid_prices", "ask_prices", "bid_sizes", "ask_sizes", "top_sides", "depth_at")

_TOKEN = re.compile(r"\b(" + "|".join(FORBIDDEN) + r")\b")
#: `from market_data` / ``from `market_data`` / `from `.x.market_data`.
_FROM = re.compile(r"\bfrom\s+`?(?:\.?\w+\.)*market_data\b(?![\w.])")
#: A functional select: ``?[`market_data;...``.
_FUNCTIONAL = re.compile(r"[?!]\[\s*`market_data\b")
_DATE_ONLY = re.compile(r"^`?date\b")
_CLOSERS = {"(": ")", "[": "]", "{": "}"}


def code(text: str) -> list[tuple[int, str]]:
    """The (line number, text) pairs of a q file with its comments removed: a line
    that starts with `/`, and a trailing ` /` comment."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("/"):
            line = ""
        else:
            line = re.sub(r"(?<=\s)/(?=\s|$).*$", "", line)
            # a `/` after text on the line: only a comment when it follows whitespace
        out.append((n, line))
    return out


def _where_items(rest: str) -> list[str] | None:
    """The comma-separated constraints of the `where` clause that starts `rest`, up to the
    end of the statement; None when `rest` holds no `where`."""
    m = re.search(r"\bwhere\b", rest)
    if m is None:
        return None
    body = rest[m.end() :]
    depth, item, items = 0, [], []
    for ch in body:
        if ch in _CLOSERS:
            depth += 1
        elif ch in _CLOSERS.values():
            if depth == 0:
                break
            depth -= 1
        elif ch == ";" and depth == 0:
            break
        if ch == "," and depth == 0:
            items.append("".join(item).strip())
            item = []
        else:
            item.append(ch)
    items.append("".join(item).strip())
    return [i for i in items if i]


def _statement(lines: list[tuple[int, str]], index: int, start: int) -> str:
    """The text of the statement that begins at column `start` of line `index`, joined
    over the continuation lines (a q statement continues on lines that begin with space)."""
    parts = [lines[index][1][start:]]
    j = index + 1
    while j < len(lines) and lines[j][1][:1] in (" ", "\t") and lines[j][1].strip():
        parts.append(lines[j][1])
        j += 1
    return " ".join(parts)


def problems(root: Path, path: tuple[str, ...] = PATH) -> list[str]:
    found: list[str] = []
    for rel in path:
        f = root / rel
        if not f.is_file():
            found.append(f"{rel}: listed in PATH but not there - remove it or restore it")
            continue
        lines = code(f.read_text(encoding="utf-8", errors="replace"))
        for n, line in lines:
            for token in _TOKEN.findall(line):
                found.append(
                    f"{rel}:{n}: `{token}` on a markout's path - a markout prices from the flat "
                    "top of book (bid, ask) and never reads a ladder or calls the depth reader"
                )
        for i, (n, line) in enumerate(lines):
            for m in _FROM.finditer(line):
                items = _where_items(_statement(lines, i, m.end()))
                if items is None:
                    continue
                for item in items:
                    if not _DATE_ONLY.match(item):
                        found.append(
                            f"{rel}:{n}: market_data selected with `where {item}` - only the date "
                            "may constrain it: any other where clause copies every column it names "
                            "into the heap (select the date's columns and let the as-of search do "
                            "the rest, mapped_books.q)"
                        )
                        break
            for m in _FUNCTIONAL.finditer(line):
                rest = _statement(lines, i, m.start())
                if not re.search(r"enlist\s*\(\s*(?:=|within)\s*;\s*`date\b", rest):
                    found.append(
                        f"{rel}:{n}: a functional select of market_data whose constraint is not a "
                        "lone `date` one - the date is the only constraint that keeps it mapped"
                    )
    return found
