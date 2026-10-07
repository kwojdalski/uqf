"""The documentation a new process owes: a Showcase card and a stack.md line.

Both pages are authored prose, so no generator can write them - and until
#713 the scaffold only printed a note saying so, which is how a process could
ship with neither. Now it writes a placeholder into each, marked SCAFFOLDED
like every other one, and test_no_scaffold_left.py scans these two pages, so
the build stays red until someone has written the real words.

The placeholders are for a person to REPLACE, not to keep: the Showcase card
names the process and says nothing, and the stack.md line is an HTML comment
that renders as nothing at all. A placeholder that read well enough to be
left would defeat the check.
"""

from __future__ import annotations

import re
from pathlib import Path

from uqs.paths import UqsError
from uqs.scaffold.plan import FileAction, WriteMode

#: The architecture page that names every process (test_generated_docs.py
#: holds it to the registry).
STACK_PAGE = Path("docs/architecture/stack.md")

#: The Services - Showcase page: one card per process, grouped by section.
SHOWCASE_PAGE = Path("docs/services/README.md")

#: Showcase sections a scaffold places a card in, by the heading they carry.
FEEDS = "Feeds"
EXTERNAL = "External sources"
NORMALISERS = "Normalisers"
ANALYTICS = "Analytics"
BACKFILLS = "Backfills"
SECTIONS = (FEEDS, EXTERNAL, NORMALISERS, ANALYTICS, BACKFILLS)


def stack_line(proc: str) -> str:
    """The stack.md placeholder for `proc`: a comment, so it renders as nothing."""
    return (
        f"<!-- SCAFFOLDED: name {proc} in the process list at the top of this page, "
        "and say where it sits if it changes a diagram - then delete this comment -->"
    )


def showcase_card(proc: str) -> str:
    """The Showcase placeholder for `proc`, in the shape of the cards around it."""
    return (
        f"**`{proc}` · SCAFFOLDED: a few words** --- SCAFFOLDED: what it does, what goes in "
        "and what comes out, and how to see it."
    )


def doc_stub_actions(proc: str, section: str) -> list[FileAction]:
    """The two placeholders a new process `proc` owes, its card in `section`."""
    if section not in SECTIONS:
        raise UqsError(f"no Showcase section {section!r} - one of {', '.join(SECTIONS)}")
    return [
        FileAction(STACK_PAGE, f"\n{stack_line(proc)}\n", mode=WriteMode.APPEND),
        FileAction(SHOWCASE_PAGE, showcase_card(proc), mode=WriteMode.APPEND, anchor=section),
    ]


def with_card(existing: str, card: str, section: str) -> str:
    """The Showcase page with `card` as the last card of `## section`.

    Refuses rather than guesses when the heading is missing or appears twice:
    a card appended at the end of the page would land under Diagnostics, read
    as if it belonged there, and still satisfy the marker check once edited.
    """
    lines = existing.splitlines(keepends=True)
    heading = f"## {section}"
    at = [i for i, line in enumerate(lines) if line.rstrip() == heading]
    if len(at) != 1:
        raise UqsError(
            f"{SHOWCASE_PAGE} has {len(at)} `{heading}` headings, expected 1 - "
            "add the process's card by hand"
        )
    end = next((i for i in range(at[0] + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    while end > at[0] + 1 and not lines[end - 1].strip():
        end -= 1
    # A blank line, then the card; the blank lines that followed the section's
    # last card still follow it, so removing the two restores the page exactly.
    return "".join(lines[:end] + ["\n", card + "\n"] + lines[end:])


def without_stubs(text: str, proc: str) -> str:
    """`text` without any still-SCAFFOLDED line naming `proc`, and the blank
    line a scaffold put before it - what `uqs job remove` undoes."""
    named = re.compile(rf"\b{re.escape(proc)}\b")
    out: list[str] = []
    for line in text.splitlines(keepends=True):
        if "SCAFFOLDED" in line and named.search(line):
            if out and not out[-1].strip():
                out.pop()
            continue
        out.append(line)
    return "".join(out)
