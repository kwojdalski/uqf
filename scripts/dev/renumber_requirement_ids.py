#!/usr/bin/env python3
"""Renumber requirement ids E-nn -> ETL-nn (#109).

Sixteen ids meant two different things: `E-05` was "publish a nonempty page
before recording the cursor" in docs/reference/etl-framework-requirements.md
and, in the question bank that used to live in docs/decisions/, "do sources
return text q must coerce". A bare `E-05` in a commit message was genuinely
ambiguous, and commit 1df5971 proved it.

The decision (#109): the REQUIREMENTS take new prefixes, because they are
the durable artefact; the bank kept E-/F- because its ids were transient - a
question stops mattering once answered.

## Why this still runs now that the bank is gone

The bank was removed in #247 along with every citation of it, so a bare
`E-nn` or `F-nn` no longer means anything at all - which makes this a
simpler rule than the one it was written for: such an id in `src/`,
`tests/`, `scripts/`, `python/`, the README or the requirements document
is a requirement citation that was never renamed, and `--check` is what
stops one creeping back in. The ambiguity it was arbitrating is gone; the
typo it catches is not.

It used to rename `F-nn` to `FE-nn` too. The frontend requirements document
those ids pointed into was removed, and every `FE-nn` citation with it, so
there is no `FE-nn` left to rename to - and a bare `F-nn` means nothing.

`--check` reports what would change and exits 1 if anything would.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# parents[2], not parent.parent: this file sits one level deeper since
# scripts/ was foldered (#241). Getting it wrong does not raise - it
# resolves to scripts/ and the checker reports over an empty tree.
REPO = Path(__file__).resolve().parents[2]

#: Files whose E-/F- citations are requirement citations.
RENUMBER_PREFIXES = ("src/", "tests/", "scripts/", "python/")
RENUMBER_FILES = {
    "README.md",
    "docs/reference/etl-framework-requirements.md",
}
#: Explicitly excluded even under a renumbered prefix.
EXCLUDE = {
    "scripts/dev/renumber_requirement_ids.py",  # this file's own docstring
}

#: The requirement id range. E-01..E-24 is what the requirements document
#: defines; anything outside it is not a requirement id.
ETL_MAX = 24

#: An id preceded by "bank " or "bank question " is a QUESTION-BANK citation
#: living in a source file, and must not be renumbered. Those are marked
#: explicitly for exactly this reason: a blind rename would have turned
#: "the question bank (issue #73)" into "bank ETL-05", corrupting the one kind of
#: citation #109 exists to disambiguate. Five such were found in
#: source_contract.q, demo_deals.q and coercion.q before the first apply.
#: A QUOTED id - "E-05" - is a mention of the id as a string, not a citation
#: of the requirement. The alias line in the requirements document's header
#: says `an old citation like "E-05" ... means the question`, and that
#: example must keep its old spelling or the sentence explains nothing.
ID_RE = re.compile(r'(?<!bank )(?<!bank question )(?<!")(?<![A-Z-])\b(E)-(\d{2})\b(?!")')


def new_id(m: re.Match) -> str:
    prefix, num = m.group(1), int(m.group(2))
    if prefix == "E" and 1 <= num <= ETL_MAX:
        return f"ETL-{num:02d}"
    return m.group(0)


def candidates() -> list[Path]:
    out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True).stdout
    files = []
    for rel in out.split():
        if rel in EXCLUDE:
            continue
        if rel in RENUMBER_FILES or rel.startswith(RENUMBER_PREFIXES):
            if rel.endswith((".q", ".py", ".md", ".toml", ".yaml", ".yml", ".sh")):
                files.append(REPO / rel)
    return files


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="report, change nothing, exit 1 if any")
    args = ap.parse_args()

    total = 0
    for path in candidates():
        text = path.read_text(encoding="utf-8", errors="replace")
        new, n = ID_RE.subn(new_id, text)
        if n and new != text:
            total += n
            rel = path.relative_to(REPO)
            print(f"{n:4}  {rel}")
            if not args.check:
                path.write_text(new, encoding="utf-8")
    verb = "would renumber" if args.check else "renumbered"
    print(f"\n{verb} {total} citation(s)")
    return 1 if (args.check and total) else 0


if __name__ == "__main__":
    sys.exit(main())
