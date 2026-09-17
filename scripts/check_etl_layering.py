#!/usr/bin/env python3
"""Keep `src/etl/core/` from depending on `sources/`, `workers/` or `streaming/`.

Question bank F-08 settled the split: `core/` is framework, `sources/` and
`workers/` are declarations, and bank question E-02 names the property that
split buys - adding a source is a file in `sources/` plus a registration,
with NO core change - and it is the reason the ETL tree can grow without the
framework learning about each new feed.

Nothing enforced it. N-04's answer said so plainly: the directory rule is a
convention, and a file dropped into `core/` that reached into `sources/`
would break the property silently, because every test would still pass. A
convention that only holds while everyone remembers it is the state this
repository keeps finding its bugs in.

The direction is one-way on purpose. `sources/` and `workers/` SHOULD call
into `core/` - that is what a declaration over a generic shell means - so
this checks only that the arrow never points back.

STRING AND COMMENT AWARE, and it has to be. `bounded_worker.q` names
`.qwrk.demo_deals_backfill` inside an error message ("ns must be a namespace symbol such as
`.qwrk.demo_deals_backfill"), which is documentation, not a dependency. A naive search reports
it on the first run, and a checker that is wrong the day it lands gets
suppressed rather than fixed - after which it protects nothing.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CORE = REPO / "src" / "etl" / "core"
DECLARING_DIRS = ("sources", "workers", "streaming")

#: A namespace declaration, e.g. `\d .qwrk.demo_deals_backfill`.
NAMESPACE_RE = re.compile(
    r"^\\d\s+(\.[a-zA-Z][a-zA-Z0-9_]*(?:\.[a-zA-Z][a-zA-Z0-9_]*)*)\s*$", re.MULTILINE
)


def declaring_namespaces() -> dict[str, str]:
    """namespace -> the file that declares it, for every sources/ and
    workers/ module. Discovered rather than listed, so a new declaration is
    covered the day it is added.
    """
    found: dict[str, str] = {}
    for sub in DECLARING_DIRS:
        directory = REPO / "src" / "etl" / sub
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.q")):
            for ns in NAMESPACE_RE.findall(path.read_text(encoding="utf-8")):
                if ns != ".":
                    found[ns] = str(path.relative_to(REPO))
    return found


def strip_comments_and_strings(text: str) -> str:
    """Blank out q comments and string literals, keeping line structure.

    A whole-line comment starts with `/` at the start of a line (after
    optional whitespace). Trailing comments are deliberately NOT stripped:
    telling ` / ` apart from a `/` inside a string needs a real parser, and
    getting it wrong would hide a genuine call rather than merely report an
    extra one.
    """
    out_lines = []
    for raw in text.splitlines():
        if raw.lstrip().startswith("/"):
            out_lines.append("")
            continue
        buf = []
        in_string = False
        i = 0
        while i < len(raw):
            ch = raw[i]
            if ch == '"':
                in_string = not in_string
                buf.append(" ")
                i += 1
                continue
            if in_string:
                # A valid escape consumes both characters, so an escaped
                # quote does not flip the state.
                if ch == "\\" and i + 1 < len(raw):
                    buf.append("  ")
                    i += 2
                    continue
                buf.append(" ")
                i += 1
                continue
            buf.append(ch)
            i += 1
        out_lines.append("".join(buf))
    return "\n".join(out_lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="accepted for symmetry")
    parser.parse_args()

    if not CORE.is_dir():
        print(f"error: {CORE.relative_to(REPO)} is missing", file=sys.stderr)
        return 1

    declared = declaring_namespaces()
    if not declared:
        print("error: no sources/ or workers/ namespaces found - has the", file=sys.stderr)
        print("layout changed? refusing to report success over an empty set.", file=sys.stderr)
        return 1

    violations: list[str] = []
    for path in sorted(CORE.glob("*.q")):
        code = strip_comments_and_strings(path.read_text(encoding="utf-8"))
        for lineno, line in enumerate(code.splitlines(), 1):
            for ns, owner in declared.items():
                if re.search(rf"{re.escape(ns)}\b", line):
                    violations.append(
                        f"{path.relative_to(REPO)}:{lineno}: core reaches into "
                        f"{ns} (declared by {owner})"
                    )

    if violations:
        print("src/etl/core/ must not depend on sources/ or workers/:", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        print(
            "\nPer bank F-08 core/ is framework and the other two are\n"
            "declarations; bank E-02 is\n"
            "the property that buys - a new source needs no core change. An arrow\n"
            "pointing this way removes it.",
            file=sys.stderr,
        )
        return 1

    print(
        f"check_etl_layering: {len(list(CORE.glob('*.q')))} core file(s) depend on none "
        f"of the {len(declared)} declaring namespace(s)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
