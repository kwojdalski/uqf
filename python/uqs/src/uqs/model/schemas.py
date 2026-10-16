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
_DEFINITION = re.compile(r"^(?:\.qetl\.plant\.)?([a-z_][a-z0-9_]*):\(\[\].*$", re.MULTILINE)

#: The same, also as `uqs deploy build --q-target 4.0` leaves it (#861): the
#: converter qualifies every name under the file's `\d .qetl.plant`, so a
#: release built for kdb+ 4.0 prefixes each `name:([]...)` with that context. Group 1 is
#: the definition WITHOUT that context - what database.q, loaded in the root
#: context, must carry - and group 2 the name (#1158).
_RELEASE_DEFINITION = re.compile(
    r"^(?:\.qetl\.plant\.)?(([a-z_][a-z0-9_]*):\(\[\].*)$", re.MULTILINE
)


def definitions_in(text: str) -> dict[str, str]:
    """{table name: its one-line q definition} for the q source `text`.

    The one place a `name:([]...)` line is recognised, bare or under the
    `.qetl.plant.` prefix a release carries; anything that reads a plant
    declaration - from the tree or from text a test supplies - goes through it.
    """
    return {match.group(2): match.group(1) for match in _RELEASE_DEFINITION.finditer(text)}


def _definitions() -> dict[str, str]:
    """{table name: its one-line q definition}, read from the q file.

    Read at import and cached in the constants below rather than on every
    call: these are consulted while generating `database.q`, which
    `bootstrap()` does on *every* uqs command. `uqs deploy build` reads a
    STAGED release's by running uqs from it (deploy/build.py's tree_python,
    #1163), so this always reads the tree uqs was imported from.
    """
    if not TABLES_Q.is_file():  # pragma: no cover - a broken checkout
        raise FileNotFoundError(f"table definitions not found at {TABLES_Q}")
    found = definitions_in(TABLES_Q.read_text())
    if not found:  # pragma: no cover - guarded by test_schemas.py
        raise ValueError(
            f"{TABLES_Q} defines no tables - the `name:([]...)` convention this "
            "reader matches on has changed, and every generated database.q "
            "would silently lose uqf's tables"
        )
    return found


def definition_from(found: dict[str, str], table: str, source: str) -> str:
    """`table`'s definition in `found`, or a refusal naming the table and `source`."""
    if table not in found:
        raise KeyError(f"{table!r} is not defined in {source} - it has {', '.join(sorted(found))}")
    return found[table]


def definition(table: str) -> str:
    """One table's q definition, or a refusal naming it.

    Throws rather than returning None: a missing definition that reaches
    `_generated_schema_content` becomes a tickerplant without the table, and
    the first symptom is a feed failing to publish.
    """
    return definition_from(_definitions(), table, TABLES_Q.name)


#: One `name:literal` column of a `([]...)` body, split at the top level.
_COLUMN = re.compile(r"^\s*([a-z_][a-z0-9_]*)\s*:\s*(.+?)\s*$", re.IGNORECASE | re.DOTALL)


def column_pairs(definition_text: str) -> list[tuple[str, str]]:
    """`t:([]a:`float$(); b:())` as [("a", "`float$()"), ("b", "()")] - literals as
    written, attributes included.

    The ONE splitter of a declaration's column list. It reads to the `)` that
    closes the table literal and splits on `;` at the top level only, so a
    trailing comment cannot widen the body and a `;` inside a bracketed
    literal cannot cut a column in two. A chunk that is not `name:literal` is
    skipped, not guessed at.
    """
    start = definition_text.index("([]") + 3
    depth, chunk, parts = 1, start, []
    for i in range(start, len(definition_text)):
        c = definition_text[i]
        if c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
            if depth == 0:
                parts.append(definition_text[chunk:i])
                break
        elif c == ";" and depth == 1:
            parts.append(definition_text[chunk:i])
            chunk = i + 1
    else:
        parts.append(definition_text[chunk:])
    return [(m.group(1), m.group(2)) for part in parts if (m := _COLUMN.match(part))]


def column_names(definition_text: str) -> list[str]:
    """The column names of a definition, in declared order."""
    return [name for name, _ in column_pairs(definition_text)]


def columns(table: str) -> list[str]:
    """`table`'s column names in declared order, from the tree's plant_tables.q."""
    return column_names(definition(table))


_DEFS = _definitions()


def table_names() -> set[str]:
    """Every table plant_tables.q defines."""
    return set(_DEFS)
