"""Refuse a reference-variant change that lets the gather read beyond the deduped rids (#2289).

A list of variants is evaluated over a SLICE: the few books their SELECTs matched, with their
ladders, gathered once by `rid`. The market is touched in exactly one place,
`.qetl.mapped.gather` (src/etl/core/mapped_depth.q), and that is what keeps the cost of a
thousand variants the cost of one gather. Three things would break it, and each is visible in
the text of the code:

* variant code that reaches the market. The variant engine, the transform library and the
  declarations in src/etl/variants/ receive a slice and never name `market_data`: a variant
  that selected from it would read the market once per variant, not once per batch.
* a ladder read that does not go through the gather. `depth_at` is the one reader of
  ladders; outside mapped_depth.q only `gather` may reach it (the twin side calls `gather`).
* a gather that does not dedupe, or does not count what it reads. `gather` must take the
  DISTINCT books, hand them to `depth_at` once, and compare the ladder rows read
  (`depth_reads`) with the books it was given.

    python3 scripts/gates/check.py variant-gather
"""

from __future__ import annotations

import re
from pathlib import Path

#: Files that must never name the market: the variant engine, its library, the declarations.
NO_MARKET = (
    "src/etl/core/variant_run.q",
    "src/etl/core/reference_library.q",
    "src/etl/core/reference_variant.q",
    "src/etl/transforms/markout_variants.q",
)
#: Directories whose every .q file is a variant declaration.
DECLARATIONS = ("src/etl/variants",)

DEPTH_READER = "src/etl/core/mapped_depth.q"

_MARKET = re.compile(r"\bmarket_data\b")
_DEPTH_AT = re.compile(r"\.qetl\.mapped\.depth_at\b")
_GATHER = re.compile(r"^\.qetl\.mapped\.gather:", re.M)


def code(text: str) -> str:
    """A q file with its comments removed: whole-line comments and trailing ` /` ones."""
    out = []
    for line in text.splitlines():
        if line.lstrip().startswith("/"):
            continue
        out.append(re.sub(r"(?<=\s)/(?=\s|$).*$", "", line))
    return "\n".join(out)


def _gather_body(text: str) -> str | None:
    """The body of `.qetl.mapped.gather`: from its definition to the next top-level one."""
    m = _GATHER.search(text)
    if m is None:
        return None
    rest = text[m.start() :]
    nxt = re.search(r"^\.\w", rest[1:], re.M)
    return rest if nxt is None else rest[: nxt.start() + 1]


def problems(root: Path) -> list[str]:
    found: list[str] = []
    files = list(NO_MARKET)
    for d in DECLARATIONS:
        files += sorted(str(p.relative_to(root)) for p in (root / d).glob("*.q"))
    for rel in files:
        f = root / rel
        if not f.is_file():
            found.append(f"{rel}: listed but not there - remove it or restore it")
            continue
        for line in code(f.read_text(encoding="utf-8")).splitlines():
            if _MARKET.search(line):
                found.append(
                    f"{rel}: names market_data - a variant is evaluated over the gathered "
                    "slice and never reads the market itself (the gather is the one reader)"
                )
                break
    for p in sorted((root / "src").rglob("*.q")):
        rel = str(p.relative_to(root))
        if rel == DEPTH_READER:
            continue
        if _DEPTH_AT.search(code(p.read_text(encoding="utf-8"))):
            found.append(
                f"{rel}: calls .qetl.mapped.depth_at - ladders are read only through "
                ".qetl.mapped.gather, which reads the deduped rids once"
            )
    src = root / DEPTH_READER
    body = _gather_body(code(src.read_text(encoding="utf-8"))) if src.is_file() else None
    if body is None:
        found.append(f"{DEPTH_READER}: .qetl.mapped.gather is missing")
    else:
        if not re.search(r"distinct\s+raze", body):
            found.append(
                f"{DEPTH_READER}: gather must take the DISTINCT books "
                "across the selects (distinct raze ...)"
            )
        if len(_DEPTH_AT.findall(body)) != 1:
            found.append(
                f"{DEPTH_READER}: gather must call depth_at exactly once, over the deduped books"
            )
        if "depth_reads" not in body or not re.search(r"reads\s*<>\s*count\s+books", body):
            found.append(
                f"{DEPTH_READER}: gather must hold the ladder rows it read "
                "(depth_reads) equal to the books it was given"
            )
    return found
