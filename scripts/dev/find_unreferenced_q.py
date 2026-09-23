#!/usr/bin/env python3
"""List q functions under src/ that nothing references, or only tests do.

Written for the `dead-code-hunter` agent, and useful on its own. It is a
TEXT scan, so its output is a list of CANDIDATES, never a verdict:

* q binds names at call time, so a function reached by name - `value`,
  `` ` sv ns,name ``, a delegate that looks methods up in a namespace, a
  TorQ process loading a script - reads here as unreferenced. `.qbw`'s
  lifecycle methods are the standing example: `delegate[worker;nm]` calls
  them by symbol, and no line names them.
* "Only tests" is normal for a LIBRARY. Most of src/pricing, src/portfolio
  and src/execution is public API that the tree itself never calls; a
  function with an `@eg` is documented as callable, and is not dead because
  nothing in-tree calls it.

A definition is `name:{` at the start of a line inside a `\\d .ns` block, or a
dotted `.ns.name:{`. A reference is the fully qualified name anywhere, or the
bare name inside the defining file, in src/, scripts/, tests/, python/ (IPC
query strings) or web/src/ - excluding comment lines, the definition line
itself and docs/man.q, which registers every function and so would make
everything look used.

    python3 scripts/dev/find_unreferenced_q.py            # both lists
    python3 scripts/dev/find_unreferenced_q.py --nowhere  # only the stronger one
"""

from __future__ import annotations

import argparse
import re
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

#: Where a reference may come from. Python and TypeScript are included
#: because the gateway and the frontend reach q by sending query TEXT.
CORPUS = (
    "src/**/*.q",
    "scripts/**/*.q",
    "scripts/**/*.py",
    "tests/**/*.q",
    "python/**/*.py",
    "web/src/**/*.ts",
    "web/src/**/*.tsx",
)
_SKIP_PARTS = {".venv", "node_modules", "__pycache__"}
_NS = re.compile(r"\\d\s+(\.\S+|\.)\s*$")
_DEF = re.compile(r"(\.[\w.]+|\w+)\s*:\s*\{")


def definitions() -> dict[str, tuple[Path, int, str]]:
    """fully qualified name -> (file, line, bare name)."""
    found: dict[str, tuple[Path, int, str]] = {}
    for path in sorted(REPO.glob("src/**/*.q")):
        ns = ""
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if m := _NS.match(line):
                ns = "" if m.group(1) == "." else m.group(1)
                continue
            if m := _DEF.match(line):
                name = m.group(1)
                fq = name if name.startswith(".") else (f"{ns}.{name}" if ns else name)
                found[fq] = (path, number, name.split(".")[-1])
    return found


def corpus() -> list[tuple[Path, list[str]]]:
    files = []
    for pattern in CORPUS:
        for path in REPO.glob(pattern):
            if _SKIP_PARTS & set(path.parts):
                continue
            files.append((path, path.read_text(errors="ignore").splitlines()))
    return files


def is_test(path: Path) -> bool:
    rel = path.relative_to(REPO).parts
    return rel[0] == "tests" or "tests" in rel


def references(defs: dict[str, tuple[Path, int, str]]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    files = corpus()
    for fq, (def_path, def_line, bare) in defs.items():
        qualified = re.compile(re.escape(fq) + r"(?!\w)")
        unqualified = re.compile(r"(?<![\w.`])" + re.escape(bare) + r"(?![\w:])")
        for path, lines in files:
            own_file = path == def_path
            for number, line in enumerate(lines, 1):
                if line.lstrip().startswith(("/", "#")) or (own_file and number == def_line):
                    continue
                if qualified.search(line) or (own_file and unqualified.search(line)):
                    counts[fq]["test" if is_test(path) else "code"] += 1
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--nowhere", action="store_true", help="only functions nothing references")
    args = ap.parse_args()
    defs = definitions()
    refs = references(defs)
    nowhere = sorted(fq for fq in defs if not refs[fq])
    tests_only = sorted(fq for fq in defs if refs[fq] and not refs[fq]["code"])
    print(
        f"{len(defs)} q functions: {len(nowhere)} referenced nowhere, "
        f"{len(tests_only)} referenced only by tests"
    )
    print("\n-- referenced nowhere (check for call-by-name before believing it) --")
    for fq in nowhere:
        path, line, _ = defs[fq]
        print(f"{path.relative_to(REPO)}:{line}  {fq}")
    if not args.nowhere:
        print("\n-- referenced only by tests (normal for public library API) --")
        for fq in tests_only:
            path, line, _ = defs[fq]
            print(f"{path.relative_to(REPO)}:{line}  {fq}  ({refs[fq]['test']} test refs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
