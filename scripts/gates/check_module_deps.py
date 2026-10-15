#!/usr/bin/env python3
"""Hold the q library's module graph to a reviewed, acyclic edge list (#626).

The library modules under src/ - one file each, or a few files for one
concern - used to depend on each other in a cycle: forwards used execution's
sweep_price and markout, and execution read forwards' output-column state. q
resolves names when they are called, so nothing failed; the cycle only made
every change to either module a change to both. #626 broke it - shared book
maths into `book`, synthetic cross pricing into `cross`, cross markouts into
`execution`, schema checks into `schema` - and this keeps it broken.

A MODULE here is a source file (a few files that belong together are grouped
in GROUPS), and no longer a namespace: since #1411 the modules load into seven
`.qf.<domain>` namespaces, so several modules share one namespace and the
namespace says nothing about who depends on whom. An edge is found by reading
a fully qualified reference, `.qf.px.fwd_simple`, and asking which file DEFINES
`fwd_simple` in `.qf.px`. That works because a call from one module to another
is always fully qualified (a bare name only reaches the file's own
definitions), and because of the fourth rule below.

Four rules, each a failure on its own:

* **Every edge is listed.** A reference from one library module to another
  that ALLOWED does not hold fails, naming the file and line. Adding the edge
  to ALLOWED in the same change is the point: a new dependency becomes a line
  a reviewer sees, not one nobody noticed.
* **Every listed edge exists.** A stale entry would let the dependency come
  back unreviewed later, so an edge the code no longer has fails too.
* **No cycle, and the layers hold.** ALLOWED itself must be acyclic,
  foundation must depend only on foundation, and the edges #626 removed are
  refused by name even if someone lists them.
* **A name is defined once per namespace.** Two files that load into the same
  `.qf.<domain>` and both define `name:` leave the winner to load order, and
  the loser's callers silently get the other function. Refused, naming both
  files; rename the one that means something narrower.

STRING AND COMMENT AWARE, like check_etl_layering.py: an error message or a
comment naming another module's function documents it and is not a call.

    python3 scripts/gates/check_module_deps.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

#: The library: one module per file directly under these src/ directories.
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

#: Files that load into a library namespace but are not modules of the graph:
#: the namespace-discovery file and the synthetic-market constants the ETL
#: loads. They are read for the duplicate-definition rule only, since a name
#: they define shares its namespace with the modules above.
NAMESPACE_ONLY_FILES = ("src/namespaces.q", "src/etl/synthetic_market.q")

#: File stem -> the module it belongs to, for a module split by concern under
#: the size gate (#990). The split keeps the module's edges: every file's
#: references are the module's.
GROUPS = {
    "attribution_routes": "cross",
    "execution_attribution": "execution",
    "microstructure_venues": "microstructure",
}

#: Foundation: depends on nothing above it.
FOUNDATION = frozenset({"schema", "render", "stats", "ccy", "daycount", "calendar", "rates"})

#: Edges the restructuring removed, refused even if listed, with the reason.
FORBIDDEN = {
    ("forwards", "execution"): "pricing does not depend on execution (#626)",
    ("cross", "execution"): "synthetic cross pricing does not depend on execution (#626)",
    ("execution", "forwards"): "execution reads no forwards state or helpers (#626)",
    ("book", "cross"): "shared book maths sits below synthetic pricing (#626)",
    ("book", "execution"): "shared book maths sits below execution (#626)",
}

#: Every allowed edge (from, to): `from` calls into `to`.
ALLOWED = frozenset(
    {
        ("allocation", "risk"),
        ("allocation", "schema"),
        ("asof", "schema"),
        ("book", "schema"),
        ("calendar", "ccy"),
        ("cross", "book"),
        ("cross", "ccy"),
        ("cross", "schema"),
        ("desk_positions", "ccy"),
        ("desk_positions", "risk"),
        ("desk_positions", "schema"),
        ("dqchecks", "book"),
        ("dqchecks", "render"),
        ("dqchecks", "schema"),
        ("execution", "asof"),
        ("execution", "ccy"),
        ("execution", "cross"),
        ("execution", "schema"),
        ("forwards", "book"),
        ("forwards", "ccy"),
        ("forwards", "rates"),
        ("limits", "schema"),
        ("microstructure", "book"),
        ("microstructure", "execution"),
        ("microstructure", "schema"),
        ("options", "rates"),
        ("options", "stats"),
        ("positions", "ccy"),
        ("positions", "cross"),
        ("positions", "risk"),
        ("risk", "stats"),
    }
)

_NS_LINE = re.compile(r"^\\d\s+(\.qf\.[a-z]+|\.qdata)\s*$", re.MULTILINE)
_NS_ANY = re.compile(r"^\\d\s+(\S+)\s*$")
_DEF = re.compile(r"^([A-Za-z_]\w*)\s*:")
_STRING = re.compile(r'"(?:\\.|[^"\\])*"')
_TRAILING_COMMENT = re.compile(r"\s/(\s.*)?$")
_REF = re.compile(r"(?<![\w`])(\.qf\.[a-z]+|\.qdata)\.([A-Za-z_]\w*)")


def module_of(path: Path, groups: dict[str, str] = GROUPS) -> str:
    return groups.get(path.stem, path.stem)


def files(root: Path = REPO) -> dict[Path, str]:
    """{file: its namespace} for every module file of the library."""
    out: dict[Path, str] = {}
    for d in LIBRARY_DIRS:
        for path in sorted((root / "src" / d).glob("*.q")):
            m = _NS_LINE.search(path.read_text(errors="replace"))
            if m:
                out[path] = m.group(1)
    return out


def modules(root: Path = REPO, groups: dict[str, str] = GROUPS) -> dict[str, list[Path]]:
    """{module: its files} for every library module."""
    out: dict[str, list[Path]] = {}
    for path in files(root):
        out.setdefault(module_of(path, groups), []).append(path)
    return out


def code(line: str) -> str:
    """`line` without its comment and its string literals."""
    if line.lstrip().startswith("/"):
        return ""
    return _TRAILING_COMMENT.sub("", _STRING.sub('""', line))


def definitions(root: Path = REPO) -> dict[tuple[str, str], list[Path]]:
    """{(namespace, name): the files defining `name:` at column 0 inside it}.

    The same reading docs/man.q and check_doc_references make: a definition is
    `name:` at column 0 between a `\\d .ns` line and the next `\\d` line.
    """
    found: dict[tuple[str, str], list[Path]] = {}
    paths = list(files(root))
    paths += [root / rel for rel in NAMESPACE_ONLY_FILES if (root / rel).is_file()]
    for path in paths:
        ns = ""
        for line in path.read_text(errors="replace").splitlines():
            if m := _NS_ANY.match(line):
                ns = m.group(1) if m.group(1) != "." else ""
                continue
            if ns and (m := _DEF.match(line)):
                found.setdefault((ns, m.group(1)), []).append(path)
    return found


def edges(root: Path = REPO, groups: dict[str, str] = GROUPS) -> dict[tuple[str, str], str]:
    """{(from, to): the first `path:line` that makes the edge}."""
    owner: dict[tuple[str, str], str] = {}
    for key, paths in definitions(root).items():
        mods = {module_of(p, groups) for p in paths}
        if len(mods) == 1:
            owner[key] = next(iter(mods))
    found: dict[tuple[str, str], str] = {}
    for path in files(root):
        me = module_of(path, groups)
        for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            for m in _REF.finditer(code(line)):
                to = owner.get((m.group(1), m.group(2)))
                if to and to != me:
                    found.setdefault((me, to), f"{path.relative_to(root)}:{n}")
    return found


def duplicates(root: Path = REPO) -> list[str]:
    """Two files of one namespace defining the same name."""
    out: list[str] = []
    for (ns, name), paths in sorted(definitions(root).items()):
        if len(paths) > 1:
            where = " and ".join(str(p.relative_to(root)) for p in paths)
            out.append(
                f"{ns}.{name} is defined in {where} - the later load silently wins; "
                "rename the narrower one"
            )
    return out


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


def problems(
    root: Path = REPO,
    allowed: frozenset[tuple[str, str]] = ALLOWED,
    groups: dict[str, str] = GROUPS,
) -> list[str]:
    found = edges(root, groups)
    out: list[str] = []
    for edge, where in sorted(found.items()):
        if edge in FORBIDDEN:
            out.append(f"{where}: {edge[0]} -> {edge[1]} is refused - {FORBIDDEN[edge]}")
        elif edge not in allowed:
            out.append(
                f"{where}: {edge[0]} -> {edge[1]} is a new module dependency - add it to "
                "ALLOWED in scripts/gates/check_module_deps.py in the same change, or call "
                "something the module may already depend on"
            )
    for edge in sorted(allowed - set(found)):
        out.append(f"ALLOWED lists {edge[0]} -> {edge[1]}, which no code makes - remove it")
    for a, b in sorted(allowed):
        if a in FOUNDATION and b not in FOUNDATION:
            out.append(f"ALLOWED lists {a} -> {b}: foundation depends only on foundation")
        if (a, b) in FORBIDDEN:
            out.append(f"ALLOWED lists {a} -> {b}, which is refused - {FORBIDDEN[(a, b)]}")
    loop = cycle(allowed | set(found))
    if loop:
        out.append("the module graph has a cycle: " + " -> ".join(loop))
    out.extend(duplicates(root))
    return out


def main() -> int:
    found = problems()
    for p in found:
        print(p, file=sys.stderr)
    if found:
        return 1
    print(f"check_module_deps: {len(ALLOWED)} reviewed edges, acyclic, no name defined twice")
    return 0


if __name__ == "__main__":
    sys.exit(main())
