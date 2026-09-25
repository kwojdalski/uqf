#!/usr/bin/env python3
"""Hold docs/reference/pipeline-declarations.md to the declaring q code.

The page is hand-written, because "what this key means and when it is
refused" is prose no generator can produce. A hand-written reference drifts
the day a key is added or renamed, so this checks both directions for each
block's key table:

* every key the q file LISTS - its required keys, and `.qetl.job.bounded`'s optional ones -
  has a row;
* every row names a key the q file mentions as a symbol (`` `key ``), so a
  renamed or removed key cannot live on in the page.

Optional keys that no list names (`procname`, `note`, `start_with_all`, `transport`,
`as_of`, the stream handlers) are held by the second direction only: the page
can describe them, and cannot describe one that no longer exists.

    python3 scripts/gates/check_declaration_reference.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PAGE = REPO / "docs" / "reference" / "pipeline-declarations.md"
CORE = REPO / "src" / "etl" / "core"

#: section heading prefix -> (declaring file, the key lists it defines,
#: keys a list names that are derived rather than declared)
BLOCKS: dict[str, tuple[str, tuple[str, ...], frozenset[str]]] = {
    "## Source": ("source_contract.q", ("required_declarations",), frozenset()),
    "## Transform": ("transform.q", ("required_keys",), frozenset()),
    "## Bounded worker": ("bounded_worker.q", ("required_cfg", "optional_cfg"), frozenset()),
    # `ns` is in the list because register stamps it before checking; a job
    # never writes it, and the page says so in prose.
    "## Streaming job": ("stream_job.q", ("required_declarations",), frozenset({"ns"})),
    "## Normalizer": ("normalizer.q", ("required_keys",), frozenset()),
}

_ROW_KEY = re.compile(r"^\s*\|\s*`(\w+)`\s*\|")


def q_list(source: str, name: str) -> list[str]:
    m = re.search(rf"^{name}:(.*)$", source, re.MULTILINE)
    if not m:
        raise SystemExit(f"{name} not found - has the declaring file changed shape?")
    return re.findall(r"`(\w+)", m.group(1))


def section_keys(page: str, heading: str) -> list[str]:
    """Keys in the first table under `heading`, up to the next heading."""
    start = page.index(heading)
    body = page[start + len(heading) :]
    end = re.search(r"^#{2,3} ", body, re.MULTILINE)
    body = body[: end.start()] if end else body
    return [m.group(1) for line in body.splitlines() if (m := _ROW_KEY.match(line))]


def main() -> int:
    page = PAGE.read_text()
    problems: list[str] = []
    for heading, (file, lists, derived) in BLOCKS.items():
        if heading not in page:
            problems.append(f"{PAGE.name}: no section starting '{heading}'")
            continue
        source = (CORE / file).read_text()
        rows = section_keys(page, heading)
        listed = {k for name in lists for k in q_list(source, name)} - derived
        for k in sorted(listed - set(rows)):
            problems.append(f"{heading[3:]}: {file} declares `{k}` but the page has no row for it")
        for k in rows:
            if not re.search(rf"`{k}\b", source):
                problems.append(
                    f"{heading[3:]}: the page describes `{k}`, which {file} never mentions"
                )
    for p in problems:
        print(p, file=sys.stderr)
    if problems:
        print(f"\n{len(problems)} problem(s) in {PAGE.relative_to(REPO)}", file=sys.stderr)
        return 1
    print(f"{PAGE.relative_to(REPO)}: every declared key has a row, every row a key")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
