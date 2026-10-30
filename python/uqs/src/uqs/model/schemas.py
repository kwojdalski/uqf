"""The tickerplant tables this tree declares, as q evaluates `src/etl/plant_tables.q`.

**The definitions themselves live in q, not here.** This module once held
them as Python string literals, then read them out of the q file's TEXT by
matching `name:([]...)` - which lost any table written `name: ([]...)`, or
across lines, though q loads both the same (#1781). Now q is asked:
`uqs.model.plant_metadata` holds what q evaluated, checked current against
the file on every read, and the readers below answer from it. A table q
does not define is not here; one q defines is, however it is spelled.

`definition` is that evaluated schema written back as one q statement - what
stp1's schema file is given - so the plant's copy is derived from q too.

TEXT READERS REMAIN for callers that read a tree they are EDITING or a bundle
before it is installed (the scaffold, `uqs job attachment`): `definitions_in`,
`column_pairs`, `column_names` and `_DEFINITION`. They see only the one-line
`name:([]...)` form those callers write; #1781's second part moves the rest.
"""

from __future__ import annotations

import re

from uqs.logger import get_logger
from uqs.model import plant_metadata
from uqs.paths import TABLES_FILE, repo_root

log = get_logger()

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
    """{table name: its q definition}, every table this tree defines, as q
    evaluated it (uqs.model.plant_metadata) - in definition order.

    Raises SchemaMetadataError, saying how to regenerate, when the metadata
    is missing or was made from another plant_tables.q: an incomplete schema
    here becomes a tickerplant without tables.
    """
    return {name: t.definition() for name, t in plant_metadata.load().own().items()}


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
    return plant_metadata.load().table(table).definition()


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
    """`table`'s column names in declared order, as q evaluated them."""
    return plant_metadata.load().table(table).column_names


def table_names() -> set[str]:
    """Every table plant_tables.q defines, as q evaluated it."""
    return set(plant_metadata.load().own())
