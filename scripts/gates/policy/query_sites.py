"""Hold every place src/etl runs a query to the one road that logs it (#3001).

`.qetl.log.queries` (and `-trace`) shows what the framework actually ran, as
qSQL, one line per query - but only for a query that goes through
`.qetl.query.run` (or `.qetl.query.local`, `.qetl.source.ipc`/`ipc_call`, which
do). A site that runs its query some other way is invisible to the switch, and
nothing fails. This is what fails.

A QUERY SITE, here, is one of:

* a functional select/update/delete with FOUR arguments, `?[t;c;b;a]` or
  `![t;c;b;a]`. The vector conditional `?[cond;a;b]` has three and is not one.
* a request to the gateway or the data-access layer: a name under `.gw.` or
  `.dataaccess.` in code.

A site is allowed when the top-level definition that holds it also calls
`.qetl.query.` (it is wired), or its line says why it is not a query that
needs logging - `/ not a query: <reason>`, such as dropping a column from a
table already in hand. Handles applied directly are
`python/uqs/.../checks/traced_queries.py`'s; qSQL in select syntax over the
markout's partitions is `markout_mapped.py`'s, and those files
(mapped_books.q, mapped_depth.q, variant_run.q) are wired by their own change.

Comments and strings are not code and are not scanned.

    python3 scripts/gates/check.py query-sites
"""

from __future__ import annotations

import re
from pathlib import Path

#: The marker that keeps one site off the road, on purpose, with its reason.
EXEMPT = "/ not a query:"

#: Files not yet on the road, with why. Only shrinks: a file listed here that no
#: longer exists, or that is clean, is reported.
UNWIRED = {
    "src/etl/core/mapped_books.q": "the markout's as-of search; wired after #3000 rewrites it",
    "src/etl/core/mapped_depth.q": "the depth reader; wired after #3000 rewrites it",
    "src/etl/core/variant_run.q": "the variant run; wired after #3000 rewrites it",
}

#: What a wired definition calls.
ROAD = ".qetl.query."

#: The gateway and the data-access layer, by name.
_REMOTE = re.compile(r"(?<![\w.])\.(?:gw|dataaccess)\.\w+")
#: The head of a functional query: `?[` or `![` not preceded by an operand.
_FUNCTIONAL = re.compile(r"(?<![\w.)\]}\"`])[?!]\[")


def _blank_strings(line: str) -> str:
    """`line` with the inside of every "string" blanked, escapes honoured."""
    out, inside, i = [], False, 0
    while i < len(line):
        ch = line[i]
        if inside and ch == "\\":
            out.append("  ")
            i += 2
            continue
        if ch == '"':
            inside = not inside
            out.append(ch)
        else:
            out.append(" " if inside else ch)
        i += 1
    return "".join(out)


def code(text: str) -> list[str]:
    """The lines of a q file with comments removed and strings blanked, so the
    line numbers still match. A line starting `/` and a trailing ` /` are
    comments; a line holding only `\\` ends the file's code, as q reads it."""
    out = []
    for line in text.splitlines():
        if line.lstrip().startswith("/") or line.startswith("\\"):
            out.append("")
            continue
        line = _blank_strings(line)
        out.append(re.sub(r"(?<=\s)/(?=\s|$).*$", "", line))
    return out


def top_level_defs(lines: list[str]) -> list[tuple[int, int]]:
    """(first, last) 0-based line indexes of each top-level statement: a line
    that starts in column 0, and the indented lines that continue it."""
    spans, start = [], None
    for i, line in enumerate(lines):
        if line and not line[0].isspace():
            if start is not None:
                spans.append((start, i - 1))
            start = i
    if start is not None:
        spans.append((start, len(lines) - 1))
    return spans


def _top_level_semicolons(text: str, open_at: int) -> int:
    """How many `;` sit directly inside the bracket opened at `open_at`."""
    depth, count = 0, 0
    for ch in text[open_at:]:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                return count
        elif ch == ";" and depth == 1:
            count += 1
    return count


def sites(lines: list[str]) -> list[tuple[int, str]]:
    """(0-based line, what) of each query site in a file's code."""
    joined = "\n".join(lines)
    found = []
    for m in _FUNCTIONAL.finditer(joined):
        if _top_level_semicolons(joined, m.end() - 1) == 3:
            found.append((joined.count("\n", 0, m.start()), f"functional `{m.group(0)}`"))
    for m in _REMOTE.finditer(joined):
        found.append((joined.count("\n", 0, m.start()), f"request `{m.group(0)}`"))
    return sorted(found)


def problems(root: Path, tree: str = "src/etl", unwired: dict[str, str] | None = None) -> list[str]:
    unwired = UNWIRED if unwired is None else unwired
    found: list[str] = []
    for rel in unwired:
        if not (root / rel).is_file():
            found.append(f"{rel}: listed in UNWIRED but not there - remove it")
    for f in sorted((root / tree).rglob("*.q")):
        rel = f.relative_to(root).as_posix()
        if rel in unwired:
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        raw, lines = text.splitlines(), code(text)
        spans = top_level_defs(lines)
        for n, what in sites(lines):
            if EXEMPT in raw[n]:
                continue
            span = next((s for s in spans if s[0] <= n <= s[1]), (n, n))
            if any(ROAD in lines[i] for i in range(span[0], span[1] + 1)):
                continue
            found.append(
                f"{rel}:{n + 1}: {what} runs a query outside the road - go through "
                f"{ROAD}run / {ROAD}local so `.qetl.log.queries` shows it, or say why it is "
                f"not a query with `{EXEMPT} <reason>`"
            )
    return found
