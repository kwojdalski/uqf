"""Polling feeds whose fetch calls a handle directly, which `--trace` cannot see.

`uqs stream preview --trace` and the running feed log every query sent
through the traced paths - `.qetl.source.ipc_call` (a cursor's query),
`.qetl.source.ipc`/`local` and `.qetl.io.odbc.run_sql` - before it goes and
again when it returns or fails. A fetch that applies the handle itself,
`h(...)`, `h@...` or a raw string `h"select ..."`, still works and is
invisible to that switch: the trace is missing exactly the query someone
turned it on to see, and nothing fails.

Read as text, so it needs no q. Applied by `uqs install-jobs` to the sidecar
files it installs, and by the test suite to the tree's own feeds. A line that
must stay direct says why, with `/ untraced: <reason>` on that line.
"""

from __future__ import annotations

import re

#: A handle applied directly: `h(`, `h@` or `h"`, as a word - not `.qetl.source.ipc[h;`.
DIRECT = re.compile(r"(?<![\w.])h\s*[(@\"]")

#: The marker that keeps one line direct, on purpose.
EXEMPT = "/ untraced:"

#: A file declares a polling feed when it names the steps poll is made of.
_POLLS = re.compile(r"`fetch`normalize|`fetch`next_cursor|`poll\b")


def untraced_lines(text: str) -> list[tuple[int, str]]:
    """(line number, line) for each direct handle call in a polling feed's
    code - empty for a file that declares no poll. Comment lines are skipped;
    a line carrying the EXEMPT marker is allowed."""
    if not _POLLS.search(text):
        return []
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        code = line.split(" /", 1)[0]
        if line.lstrip().startswith("/") or EXEMPT in line:
            continue
        if DIRECT.search(code):
            found.append((number, line.strip()))
    return found
