#!/usr/bin/env python3
"""Hold the q library's module graph to a reviewed, acyclic edge list (#626).

The library modules under src/ - one flat `.q<name>` namespace per file - used
to depend on each other in a cycle: forwards used execution's sweep_price and
markout, and execution read forwards' output-column state. q resolves names
when they are called, so nothing failed; the cycle only made every change to
either module a change to both. #626 broke it - shared book maths into
`.qbook`, synthetic cross pricing into `.qcross`, cross markouts into `.qexec`,
schema checks into `.qschema` - and this keeps it broken.

Three rules, each a failure on its own:

* **Every edge is listed.** A reference from one library namespace to another
  that ALLOWED does not hold fails, naming the file and line. Adding the edge
  to ALLOWED in the same change is the point: a new dependency becomes a line
  a reviewer sees, not one nobody noticed.
* **Every listed edge exists.** A stale entry would let the dependency come
  back unreviewed later, so an edge the code no longer has fails too.
* **No cycle, and the layers hold.** ALLOWED itself must be acyclic,
  foundation must depend only on foundation, and the edges #626 removed are
  refused by name even if someone lists them.

STRING AND COMMENT AWARE, like check_etl_layering.py: an error message or a
comment naming another module's function documents it and is not a call.

    python3 scripts/gates/check_module_deps.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

#: The library: one namespace per file directly under these src/ directories.
#: Not src/etl/ (held by check_etl_layering.py) and not the loaders.
LIBRARY_DIRS = (
    "foundation",
    "pricing",
    "portfolio",
    "execution",
    "market_data",
    "examples",
    "metadata",
    "integrations",
)

#: Foundation: depends on nothing above it.
FOUNDATION = frozenset({"qschema", "qrender", "qstats", "qccy", "qdcf", "qcal", "qrates"})

#: Edges the restructuring removed, refused even if listed, with the reason.
FORBIDDEN = {
    ("qfwd", "qexec"): "pricing does not depend on execution (#626)",
    ("qcross", "qexec"): "synthetic cross pricing does not depend on execution (#626)",
    ("qexec", "qfwd"): "execution reads no forwards state or helpers (#626)",
    ("qbook", "qcross"): "shared book maths sits below synthetic pricing (#626)",
    ("qbook", "qexec"): "shared book maths sits below execution (#626)",
}

#: Every allowed edge (from, to): `from` calls into `to`.
ALLOWED = frozenset(
    {
        ("qalloc", "qrisk"),
        ("qalloc", "qschema"),
        ("qbook", "qschema"),
        ("qcal", "qccy"),
        ("qcross", "qbook"),
        ("qcross", "qccy"),
        ("qcross", "qschema"),
        ("qdesk", "qccy"),
        ("qdesk", "qschema"),
        ("qdqc", "qmicro"),
        ("qdqc", "qrender"),
        ("qdqc", "qschema"),
        ("qexec", "qccy"),
        ("qexec", "qcross"),
        ("qexec", "qschema"),
        ("qfwd", "qbook"),
        ("qfwd", "qccy"),
        ("qfwd", "qrates"),
        ("qlimit", "qschema"),
        ("qmicro", "qbook"),
        ("qmicro", "qexec"),
        ("qmicro", "qschema"),
        ("qopt", "qrates"),
        ("qopt", "qstats"),
        ("qpos", "qccy"),
        ("qpos", "qcross"),
        ("qpos", "qrisk"),
        ("qrisk", "qstats"),
    }
)

_NS_LINE = re.compile(r"^\\d\s+\.(q[a-z]\w*)\s*$", re.MULTILINE)
_STRING = re.compile(r'"(?:\\.|[^"\\])*"')
_TRAILING_COMMENT = re.compile(r"\s/(\s.*)?$")
_REF = re.compile(r"(?<![\w`])\.(q[a-z]\w*)\.")


def modules(root: Path = REPO) -> dict[str, list[Path]]:
    """{namespace: its files} for every library module.

    Usually one file each. A module split by concern under the size gate
    (microstructure_venues.q beside microstructure.q, #990) keeps its
    namespace, and its edges are every file's.
    """
    out: dict[str, list[Path]] = {}
    for d in LIBRARY_DIRS:
        for path in sorted((root / "src" / d).glob("*.q")):
            m = _NS_LINE.search(path.read_text(errors="replace"))
            if m:
                out.setdefault(m.group(1), []).append(path)
    return out


def code(line: str) -> str:
    """`line` without its comment and its string literals."""
    if line.lstrip().startswith("/"):
        return ""
    return _TRAILING_COMMENT.sub("", _STRING.sub('""', line))


def edges(root: Path = REPO) -> dict[tuple[str, str], str]:
    """{(from, to): the first `path:line` that makes the edge}."""
    mods = modules(root)
    found: dict[tuple[str, str], str] = {}
    for ns, paths in mods.items():
        for path in paths:
            for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
                for m in _REF.finditer(code(line)):
                    to = m.group(1)
                    if to != ns and to in mods:
                        found.setdefault((ns, to), f"{path.relative_to(root)}:{n}")
    return found


def cycle(graph: frozenset[tuple[str, str]] | set[tuple[str, str]]) -> list[str]:
    """One cycle in `graph`, as a path, or [] when it is acyclic."""
    succ: dict[str, list[str]] = {}
    for a, b in sorted(graph):
        succ.setdefault(a, []).append(b)
    state: dict[str, int] = {}

    def visit(node: str, path: list[str]) -> list[str]:
        state[node] = 1
        for nxt in succ.get(node, []):
            if state.get(nxt) == 1:
                return path[path.index(nxt) :] + [nxt]
            if not state.get(nxt):
                found = visit(nxt, path + [nxt])
                if found:
                    return found
        state[node] = 2
        return []

    for start in sorted(succ):
        if not state.get(start):
            found = visit(start, [start])
            if found:
                return found
    return []


def problems(root: Path = REPO, allowed: frozenset[tuple[str, str]] = ALLOWED) -> list[str]:
    found = edges(root)
    out: list[str] = []
    for edge, where in sorted(found.items()):
        if edge in FORBIDDEN:
            out.append(f"{where}: .{edge[0]} -> .{edge[1]} is refused - {FORBIDDEN[edge]}")
        elif edge not in allowed:
            out.append(
                f"{where}: .{edge[0]} -> .{edge[1]} is a new module dependency - add it to "
                "ALLOWED in scripts/gates/check_module_deps.py in the same change, or call "
                "something the module may already depend on"
            )
    for edge in sorted(allowed - set(found)):
        out.append(f"ALLOWED lists .{edge[0]} -> .{edge[1]}, which no code makes - remove it")
    for a, b in sorted(allowed):
        if a in FOUNDATION and b not in FOUNDATION:
            out.append(f"ALLOWED lists .{a} -> .{b}: foundation depends only on foundation")
        if (a, b) in FORBIDDEN:
            out.append(f"ALLOWED lists .{a} -> .{b}, which is refused - {FORBIDDEN[(a, b)]}")
    loop = cycle(allowed | set(found))
    if loop:
        out.append("the module graph has a cycle: " + " -> ".join(f".{n}" for n in loop))
    return out


def main() -> int:
    found = problems()
    for p in found:
        print(p, file=sys.stderr)
    if found:
        return 1
    print(f"check_module_deps: {len(ALLOWED)} reviewed edges, acyclic")
    return 0


if __name__ == "__main__":
    sys.exit(main())
