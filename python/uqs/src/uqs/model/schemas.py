"""Reads the tickerplant table definitions out of `src/etl/plant_tables.q`.

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
from pathlib import Path

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

#: The same, also as `uqs deploy build --q-target 4.0` leaves it (#861): the
#: converter qualifies every name under the file's `\d .qetl.plant`, so a
#: release built for kdb+ 4.0 prefixes each `name:([]...)` with that context. Group 1 is
#: the definition WITHOUT that context - what database.q, loaded in the root
#: context, must carry - and group 2 the name (#1158).
_RELEASE_DEFINITION = re.compile(
    r"^(?:\.qetl\.plant\.)?(([a-z_][a-z0-9_]*):\(\[\].*)$", re.MULTILINE
)


def _definitions(root: Path | None = None) -> dict[str, str]:
    """{table name: its one-line q definition}, read from the q file of the
    tree at `root` - by default the checkout uqs was imported from.

    Read at import and cached in the constants below rather than on every
    call: these are consulted while generating `database.q`, which
    `bootstrap()` does on *every* uqs command. `root` is for a tree that is
    not this one: `uqs deploy build` composes the schema of a STAGED release,
    whose installed bundles declare tables this checkout does not (#1158).
    """
    tables_q = TABLES_Q if root is None else root / TABLES_FILE
    if not tables_q.is_file():  # pragma: no cover - a broken checkout
        raise FileNotFoundError(f"table definitions not found at {tables_q}")
    text = tables_q.read_text()
    found = {match.group(2): match.group(1) for match in _RELEASE_DEFINITION.finditer(text)}
    if not found:  # pragma: no cover - guarded by test_schemas.py
        raise ValueError(
            f"{tables_q} defines no tables - the `name:([]...)` convention this "
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


def table_names(root: Path | None = None) -> set[str]:
    """Every table plant_tables.q defines - the tree at `root`'s, if given."""
    return set(_DEFS) if root is None else set(_definitions(root))
