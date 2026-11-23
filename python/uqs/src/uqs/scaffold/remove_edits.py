"""The text edits `uqs job remove` makes to the files a scaffold registers a
job in: a table's definition, `expected` entry and catalog lines, and the
process's profile lines. Each takes a file's text and returns it changed;
scaffold/remove.py decides which to apply, and scaffold/remove_policy.py what
may go at all.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from uqs.paths import CATALOG_FILE, STACK_TABLES_LIST, STACK_TABLES_TEST, TABLES_FILE
from uqs.scaffold.remove_policy import refuse_still_profiled

if TYPE_CHECKING:
    from uqs.scaffold.remove import Removal


def tables_defined_by(repo_root: Path, owners: set[str]) -> list[str]:
    """The plant tables whose definition's comment opens "/ <owner>'s", as a
    scaffold of one of `owners` writes it."""
    path = repo_root / TABLES_FILE
    if not path.is_file():
        return []
    lines = path.read_text().splitlines()
    owner = re.compile(rf"/ (?:{'|'.join(map(re.escape, sorted(owners)))})'s ")
    tables = []
    for i, line in enumerate(lines):
        if m := re.match(r"(?:\.qetl\.plant\.)?(\w+):\(\[\]", line):
            start = i
            while start > 0 and lines[start - 1].startswith("/"):
                start -= 1
            if any(owner.match(c) for c in lines[start:i]):
                tables.append(m.group(1))
    return tables


def edit(removal: Removal, repo_root: Path, path: Path, change) -> None:
    """Apply `change` to `path`'s pending text, keeping only real changes."""
    if path not in removal.rewrites and not (repo_root / path).is_file():
        return
    current = removal.rewrites.get(path) or (repo_root / path).read_text()
    new = change(current)
    if new != current:
        removal.rewrites[path] = new


def drop_table(removal: Removal, repo_root: Path, table: str) -> None:
    """Queue `table`'s definition, `expected` entry and catalog lines to go."""
    edit(removal, repo_root, TABLES_FILE, lambda t: drop_definition(t, table))
    expected = f"{STACK_TABLES_LIST}:"
    edit(removal, repo_root, STACK_TABLES_TEST, lambda t: drop_token(t, expected, f"`{table}"))
    edit(removal, repo_root, CATALOG_FILE, lambda t: drop_catalog(t, table))


def drop_token(text: str, prefix: str, token: str) -> str:
    """`text` with `token` taken out of its one line starting `prefix`."""
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            lines[i] = re.sub(rf"{re.escape(token)}(?=[`;\s]|$)", "", line, count=1)
    return "".join(lines)


def drop_definition(text: str, table: str) -> str:
    """`text` without `table`'s definition, the comment above it, and the
    blank line a scaffold puts before that comment."""
    lines = text.splitlines(keepends=True)
    at = [
        i for i, line in enumerate(lines) if re.match(rf"(?:\.qetl\.plant\.)?{table}:\(\[\]", line)
    ]
    if len(at) != 1:
        return text
    start = at[0]
    while start > 0 and lines[start - 1].startswith("/"):
        start -= 1
    if start > 0 and not lines[start - 1].strip():
        start -= 1
    # A list column's `.qetl.plant.nested` declaration goes with its table:
    # left behind, it names a table that no longer exists and the plant fails
    # to load (#1567).
    nested = f".qetl.plant.nested[`{table};"
    rest = [line for line in lines[at[0] + 1 :] if not line.startswith(nested)]
    return "".join(lines[:start] + rest)


def drop_catalog(text: str, table: str) -> str:
    """`text` without `table`'s `.qcat.describe`, `.qcat.hidden` and
    `.qcat.unbounded` entries and their continuation lines."""
    heads = ("describe", "hidden", "unbounded")
    for head in (f".qcat.{h}[`{table}]:" for h in heads):
        lines = text.splitlines(keepends=True)
        for i, line in enumerate(lines):
            if line.startswith(head):
                end = i + 1
                while end < len(lines) and lines[end][:1] in (" ", "\t"):
                    end += 1
                text = "".join(lines[:i] + lines[end:])
                break
    return text


def drop_from_profiles(text: str, proc: str) -> str:
    """profiles.py without `proc` in any profile; unprofiled.py without its exemption.

    Each edit is confined to its own dict: a process may share its name with
    a profile, and PROFILES["default"] is keyed exactly as an exemption for a
    process called `default` would be (#1566). A profile is a one-line tuple
    or one written a member per line, as the scaffold's "add it by hand"
    leaves a long one (#2926); any other layout still naming `proc` is
    refused, since a profile naming a process that is gone cannot start.
    """
    quoted = f'"{proc}"'
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    block = None
    skipping = False
    members = False
    opened = 0
    for line in lines:
        if m := re.match(r"^([A-Z_]+): dict\[", line):
            block = m.group(1)
        elif line.rstrip("\n") == "}":
            block = None
        if skipping:
            skipping = line.strip() != "),"
            continue
        if members:
            # Inside a profile written a member per line, up to its `),`.
            members = not re.match(r"^\s*\),\s*$", line)
            if members and line.strip() == f"{quoted},":
                continue
            if not members and len(out) == opened + 1:
                # Its last member went: `(\n),` is `()` to ruff format (#2917).
                out[opened] = out[opened].rstrip() + "),\n"
                continue
        elif block == "UNPROFILED" and line.startswith(f"    {quoted}: ("):
            skipping = not line.rstrip().endswith("),")
            continue
        elif block == "PROFILES" and re.match(r'^\s+"\w+": \(\s*$', line):
            members = True
            opened = len(out)
        elif block == "PROFILES" and quoted in line and re.match(r'^\s+"\w+": \(.*\),\s*$', line):
            head, inner = line.split(": (", 1)
            kept = [m.strip() for m in inner.rsplit("),", 1)[0].split(",") if m.strip()]
            kept = [m for m in kept if m != quoted]
            joined = ", ".join(kept) + ("," if len(kept) == 1 else "")
            line = f"{head}: ({joined}),\n"
        out.append(line)
    new = "".join(out)
    refuse_still_profiled(new, proc)
    return new
