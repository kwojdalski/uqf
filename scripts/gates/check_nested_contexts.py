#!/usr/bin/env python3
"""Refuse a `\\d` that names more than one level, in this repository's own q (#1411).

PeachQ cannot switch into a nested working context (`\\d .qf.px`,
`\\d .qpipe.job.demo`): it fails the line it appears on, and with it the file.
Everything this tree writes therefore defines its names fully qualified from
the root, and at most opens one level:

    \\d .                          allowed (back to the root)
    \\d .qtorq                     allowed (one level)
    .qpipe.job.demo.facts:{...}   how a name is defined
    \\d .qetl.job                  refused
    system"d .qf.px"              refused (the same switch, spelled as a command)

WHAT IS SCANNED is the single list SCOPE below: every `.q` file under those
paths, and the ```q fences of every `.md`/`.qmd` file there. Add a path to the
list to bring it under the rule; tests/ joins when its own conversion lands
(#1412). lib/ is vendored and never scanned. It is the tree's OWN q the rule is
about: a site's declarations written nested still load on KDB-X, and
uqs deploy build converts them for kdb+ 4.0.

    python3 scripts/gates/check_nested_contexts.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

#: The one list. Directories are scanned for `.q` (and `.md`/`.qmd` q fences);
#: a file is scanned as itself.
SCOPE = (
    "src",
    "scripts",
    "sidecars",
    "docs",
    "README.md",
    "python/uqs/README.md",
)

#: Never scanned, even inside SCOPE. scripts/torqcode and scripts/torqconfig
#: are TorQ's own overrides and are single-level anyway.
EXCLUDED = ("scripts/portable/tests/",)

_NESTED = re.compile(r"^\\d\s+\.[A-Za-z_]\w*\.\S")
_COMMAND = re.compile(r'system\s*"d\s+\.[A-Za-z_]\w*\.\S')
_FENCE_OPEN = re.compile(r"^\s*```+\s*q\s*$")
_FENCE_CLOSE = re.compile(r"^\s*```+\s*$")


def files(root: Path = REPO, scope: tuple[str, ...] = SCOPE) -> list[Path]:
    out: list[Path] = []
    for entry in scope:
        path = root / entry
        if path.is_file():
            out.append(path)
        elif path.is_dir():
            out += sorted(p for p in path.rglob("*") if p.suffix in {".q", ".md", ".qmd"})
    return [
        p for p in out if not any(p.relative_to(root).as_posix().startswith(x) for x in EXCLUDED)
    ]


def q_lines(path: Path) -> list[tuple[int, str]]:
    """The (line number, text) pairs of q in `path`: all of a `.q` file, the
    fenced ```q blocks of a document."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if path.suffix == ".q":
        return list(enumerate(lines, 1))
    out, inside = [], False
    for n, line in enumerate(lines, 1):
        if not inside and _FENCE_OPEN.match(line):
            inside = True
        elif inside and _FENCE_CLOSE.match(line):
            inside = False
        elif inside:
            out.append((n, line))
    return out


def problems(root: Path = REPO, scope: tuple[str, ...] = SCOPE) -> list[str]:
    found = []
    for path in files(root, scope):
        for n, line in q_lines(path):
            if _NESTED.match(line) or _COMMAND.search(line):
                found.append(
                    f"{path.relative_to(root).as_posix()}:{n}: `{line.strip()}` names more than "
                    "one level - define the names fully qualified from the root instead "
                    "(`.qpipe.job.x.f:{...}`), with at most a one-level `\\d .name`"
                )
    return found


def main() -> int:
    found = problems()
    for p in found:
        print(p, file=sys.stderr)
    if found:
        return 1
    print(f"check_nested_contexts: no `\\d` names more than one level in {', '.join(SCOPE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
