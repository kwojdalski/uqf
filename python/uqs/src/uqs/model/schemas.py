"""Reads the tickerplant table definitions out of `scripts/processes/uqs_tables.q`.

**The definitions themselves live in q, not here.** This module used to hold
them as Python string literals, which meant q source that no q parser read
until `stp1` started: a typo surfaced as a failed tickerplant rather than a
failed commit, `check_q_traps.py` never scanned them (it globs
``git ls-files '*.q'``), and the contract surface reported four tables when
the system had thirteen.

So this file is now a *reader*. The q file is the single source of truth, and
a caller asks for a table by name with `definition` - which is the same
generate-or-check discipline the rest of the tree follows: the definition
exists once, and anything that needs it derives from that one place. There
are deliberately no per-table constants: each was a second spelling of a
lookup, and a table added to the q file did not get one.
"""

from __future__ import annotations

import re

from uqs.logger import get_logger
from uqs.paths import TABLES_FILE, repo_root

log = get_logger(__name__)

#: The q file that owns these definitions. Resolved from the repository root
#: rather than a working directory, so the reader works from anywhere - and
#: from `paths`, so neither the location nor the way of finding it is spelled
#: twice.
TABLES_Q = repo_root() / TABLES_FILE

#: `name:([]...)` at the start of a line, to the end of that line. Comments in
#: the q file start with `/` and never begin a definition, so a line-anchored
#: match cannot pick one up.
_DEFINITION = re.compile(r"^([a-z_][a-z0-9_]*):\(\[\].*$", re.MULTILINE)


def _definitions() -> dict[str, str]:
    """{table name: its one-line q definition}, read from the q file.

    Read at import and cached in the constants below rather than on every
    call: these are consulted while generating `database.q`, which
    `bootstrap()` does on *every* uqs command.
    """
    if not TABLES_Q.is_file():  # pragma: no cover - a broken checkout
        raise FileNotFoundError(f"table definitions not found at {TABLES_Q}")
    text = TABLES_Q.read_text()
    found = {match.group(1): match.group(0) for match in _DEFINITION.finditer(text)}
    if not found:  # pragma: no cover - guarded by test_schemas.py
        raise ValueError(
            f"{TABLES_Q} defines no tables - the `name:([]...)` convention this "
            "reader matches on has changed, and every generated database.q "
            "would silently lose uqf's tables"
        )
    return found


def definition(table: str) -> str:
    """One table's q definition, or a refusal naming it.

    Throws rather than returning None: a missing definition that reaches
    `_generated_schema_content` becomes a tickerplant without the table, and
    the first symptom is a feed failing to publish.
    """
    found = _definitions()
    if table not in found:
        raise KeyError(
            f"{table!r} is not defined in {TABLES_Q.name} - it has {', '.join(sorted(found))}"
        )
    return found[table]


_DEFS = _definitions()
