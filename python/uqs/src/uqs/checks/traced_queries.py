"""Queries `--trace` cannot see: a polling feed's or a backfill source's.

`uqs stream preview --trace`, `uqs backfill --trace` and the running jobs log
every query sent through the traced paths - `.qetl.source.ipc_call` (a
cursor's query), `.qetl.source.ipc` (a window's), `.qetl.source.local` and
`.qetl.io.odbc.run_sql`/`window_query` - before it goes and again when it
returns or fails. A file that applies the handle itself, `h(...)`, `h@...`
or a raw string `h"select ..."`, still works and is invisible to that
switch: the trace is missing exactly the query someone turned it on to see,
and nothing fails.

A REQUIREMENT, not advice: `uqs install-jobs` refuses a sidecar file with
such a line, and the test suite holds the tree's own feeds and sources to
it. A line that must stay direct says why, with `/ untraced: <reason>` on
that line. Read as text, so it needs no q.
"""

from __future__ import annotations

import re

#: A handle applied directly: `h(`, `h@` or `h"`, as a word - not `.qetl.source.ipc[h;`.
DIRECT = re.compile(r"(?<![\w.])h\s*[(@\"]")

#: The marker that keeps one line direct, on purpose.
EXEMPT = "/ untraced:"

#: A file sends queries when it declares a polling feed (names the steps poll
#: is made of) or a bounded worker's source.
_QUERIES = re.compile(r"`fetch`normalize|`fetch`next_cursor|`poll\b|\.qetl\.source\.define\[")


def untraced_lines(text: str) -> list[tuple[int, str]]:
    """(line number, line) for each direct handle call in a polling feed's
    or a source's code - empty for a file that declares neither. Comment lines
    are skipped; a line carrying the EXEMPT marker is allowed."""
    if not _QUERIES.search(text):
        return []
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        code = line.split(" /", 1)[0]
        if line.lstrip().startswith("/") or EXEMPT in line:
            continue
        if DIRECT.search(code):
            found.append((number, line.strip()))
    return found
