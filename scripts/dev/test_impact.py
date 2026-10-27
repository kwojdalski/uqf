#!/usr/bin/env python3
"""Which q test files a change reaches, read from q-lint's dependency graph (#1690).

Given the paths a change touches, prints the tests/q/test_*.q files whose
suites can see it - or ALL, with the reason, when the selection cannot be
trusted. `q tests/run_tests.q -files a.q,b.q` runs the suites those files
define - every file is still loaded - and `--format args` prints those
arguments (nothing for ALL, which is the runner without them).

    uv run python scripts/dev/test_impact.py --base origin/master
    uv run python scripts/dev/test_impact.py src/pricing/forwards.q
    uv run python scripts/dev/test_impact.py --format json --base origin/master
    python3 scripts/dev/test_impact.py --hook   # the q-tests hook's line

The q-tests hook and scripts/dev/attest_kdbx.py run what `--hook` (plan())
decides: the suites `origin/master...HEAD`, plus what is being committed,
reaches - or the whole suite when this says ALL, cannot run, or
UQF_Q_TESTS_FULL=1. An attestation records the subset by its digest, and CI's
check makes the same selection to verify it (scripts/gates/check_kdbx_attestation.py).

Needs a qlinter with `qlinter analyze graph` - scripts/gates/qlint_graph.py
says which and where it looks - and refuses without one rather than guess.

WHAT THE GRAPH IS DRAWN OVER. The files the test process loads: every .q
under src/ (src/init.q and src/etl/init.q load the whole tree, as the runner
wants), the suites, the runner's own `\\l` lines and what those load by a
literal path. tests/lib/ is cut into one node per top-level definition, so a
change reaching one helper reaches the suites calling THAT helper and not
every suite that calls any. A .q a test runs as a CHILD process (try_acquire.q,
an example script) is a node too, reached only by the path a test names it by:
its globals live in another process, so a suite reading a name it happens to
define is not reading it.

THE EDGES, beyond the graph's own:

* Path literals. A file naming a tracked path in a string or a `:path`
  symbol - `"src/etl/sources/"`, `read0 `:tests/run_tests.q` - depends on every
  file under it. A child it names by its own (or a computed) path is RUN, and
  reaches it as code does; anything else named is READ as text, and only a
  change to that file itself reaches the reader - one hop, not the walk.
* Registry links. A declaration registers into a shell by calling the shell's
  entry point (`.qetl.transform.define`, `.qetl.source.define`,
  `.qetl.job.stream.define`, ...), and the shell calls it back by a name built
  at run time, which no reading of the source resolves. So every file calling
  an entry point is linked to the shell that defines it, and a change to one
  declaration reaches every test that runs through that shell. The entry points
  are FOUND, not listed: a function under src/etl/core/ named define,
  register*, declare* or watch, and any there that calls one (at_bars,
  passthrough). The suites linked are the shell's own direct dependents - the
  walk does not fan out from the shell to every sibling declaration - plus any
  suite naming the symbol the declaration registers (`fx_markout). tests:
  scripts/dev/tests/test_test_impact.py fails when a declaration calls into
  src/etl/core/ at load through anything else, unless NOT_REGISTRIES says why.
* Into a suite only by a `.qt` name. A suite is reached from another file only
  through a `.qt.*` name it defines (test_docstring_examples reusing
  test_execution's builders; a harness helper building `.qt.qpipe.job.<job>`
  reaches every job suite) or a path literal. Production code never depends
  on a test file: a suite redefining a production name as a double is not one.

THE WALK. From the changed files, backwards through dependents until nothing
new is reached (cycles are fine), never on through a loader or the harness -
those LOAD what changed, they do not use it. The suites reached are selected.

ALL INSTEAD, with the reason, when:

* the harness changed: tests/lib/, tests/run_tests.q, the loaders (src/init.q,
  src/etl/init.q, src/etl/generated/load_plan.q), scripts/processes/, or
  vendored lib/;
* a changed file, outside the suites, has a `dynamic` entry - a name reached
  by a value computed at run time - that DYNAMIC_REVIEWED does not explain;
* a changed file that is not .q is read by the harness or a loader. One
  that another q file names by a path literal (a CSV, a fixture, a baseline)
  is walked from THAT file, as if it had changed: the literal is the edge.
  A non-.q file no q file names is read by no suite;
* a .q file was deleted (what depended on its names is no longer in the tree
  to read), or a .q file is not one this can place.

The full suite on master is the backstop for what the graph cannot see.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _load_qlint_graph():
    spec = importlib.util.spec_from_file_location(
        "qlint_graph", Path(__file__).resolve().parents[1] / "gates" / "qlint_graph.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("qlint_graph", module)
    spec.loader.exec_module(module)
    return module


qlint_graph = _load_qlint_graph()

#: The suites, as tests/lib/support.q's .qt.support.suite_files globs them.
SUITE_DIR = "tests/q"
RUNNER = "tests/run_tests.q"
HARNESS_DIR = "tests/lib/"
#: Vendored and not cut into helper nodes: it is the framework, not a helper.
QUNIT = "tests/lib/qunit.q"

#: A changed path under one of these runs the whole suite.
FULL_SUITE = {
    "tests/lib/": "the test harness every suite loads",
    "tests/run_tests.q": "the runner",
    "src/init.q": "the library loader",
    "src/etl/init.q": "the ETL loader",
    "src/etl/generated/load_plan.q": "the generated ETL load plan",
    "scripts/processes/": "a TorQ process script",
    "lib/": "vendored code",
}

#: The directory a registry's entry points are defined in.
CORE_DIR = "src/etl/core/"
#: An entry point's last name segment: how a declaration registers.
ENTRY_POINT = re.compile(r"^(define|register\w*|declare\w*|watch)$")
#: Declaration directories: every top-level call in them registers something.
DECLARATION_DIRS = (
    "src/etl/sources/",
    "src/etl/transforms/",
    "src/etl/workers/",
    "src/etl/streaming/",
    "src/etl/reactions/",
)

#: Files outside the suites whose `dynamic` entries (names reached by a value
#: computed at run time) have been read and found not to hide a dependent,
#: each with the reason. A file with one that is NOT here runs the whole suite
#: when it changes. `reflective` files walk live namespaces and may read any
#: name at all, so they are also taken to depend on every file.
#:
#: Kinds:
#:   job-state  - names built from a registered job's own namespace
#:                (`` ` sv ns,`pending ``): read and written by the shell that
#:                owns the kind, and reached through the registry links;
#:   own        - names inside the file's own namespace;
#:   data       - a table or a file on disk named by a value: data, not code;
#:   reflective - walks live namespaces; depends on everything.
DYNAMIC_REVIEWED: dict[str, tuple[str, str]] = {
    "src/etl/core/bars.q": ("job-state", "a bar job's buffers and callbacks in its namespace"),
    "src/etl/core/bounded_worker.q": (
        "own",
        "a worker's state and the delegates to .qetl.job.bounded.<method> it builds",
    ),
    "src/etl/core/config_audit.q": (
        "job-state",
        "the settings a job registers through .qetl.cfg.audit.watch, and its publish",
    ),
    "src/etl/core/horizon.q": ("job-state", "a horizon job's buffers and callbacks"),
    "src/etl/core/io_hdb.q": ("data", "partition files and the tables written to them"),
    "src/etl/core/io_manager.q": ("data", "the in-memory target table a batch is written to"),
    "src/etl/core/io_remote.q": ("data", "the journal file and table"),
    "src/etl/core/kafka_decoder.q": ("job-state", "a decoder job's marks, held and done"),
    "src/etl/core/normalizer.q": ("job-state", "a normalizer job's on_batch and publish"),
    "src/etl/core/react.q": (
        "job-state",
        "a reaction's init/run/cleanup, registered through .qetl.reaction.register",
    ),
    "src/etl/core/rolling.q": ("job-state", "a rolling job's buffers and callbacks"),
    "src/etl/core/run.q": ("data", "the run ledger file"),
    "src/etl/core/source_local_hdb.q": ("data", "HDB partition directories and column files"),
    "src/etl/core/source_zones.q": ("data", "the zone table file"),
    "src/etl/core/stream_delivery.q": (
        "job-state",
        "a delivery's definition under .qetl.job.stream.delivery and a job's publish",
    ),
    "src/etl/core/stream_job.q": ("job-state", "a stream job's state, publish and buffer tables"),
    "src/etl/core/tick.q": ("data", "the tick log file"),
    "src/etl/plant_tables.q": ("own", "the plant's table schemas under .qetl.plant"),
    "src/namespaces.q": ("reflective", "walks every live namespace to list what is defined"),
    "scripts/dev/coverage.q": ("reflective", "instruments every function it finds by name"),
}

_DEF = re.compile(r"^(\.[A-Za-z_][\w.]*)\s*::?")
_QT_DEF = re.compile(r"^(\.qt\.[A-Za-z_][\w.]*)\s*::?")
_QT_REF = re.compile(r"(?<![\w.])\.qt\.[A-Za-z_][\w.]*")
_LOAD = re.compile(r'^\\l\s+(\S+)|system\s*"l\s+([^"\s]+)"')
_SYMBOL_PATH = re.compile(r"`:([A-Za-z0-9_./-]+)")


# --------------------------------------------------------------------------
# Reading q text
# --------------------------------------------------------------------------
def scan(text: str) -> tuple[list[str], list[str]]:
    """(code lines with comments and strings blanked, the string literals).

    Line-based, the way q reads a script: `/` at the start of a line or after
    whitespace begins a comment, a line holding only `/` opens a block comment
    that a line holding only a backslash closes, and a backslash alone outside
    one ends the script.
    """
    code: list[str] = []
    strings: list[str] = []
    in_block = False
    for line in text.splitlines():
        stripped = line.strip()
        if in_block:
            if stripped == "\\":
                in_block = False
            code.append("")
            continue
        if stripped == "/":
            in_block = True
            code.append("")
            continue
        if line == "\\":
            break
        out: list[str] = []
        i, n = 0, len(line)
        while i < n:
            c = line[i]
            if c == '"':
                j, buf = i + 1, []
                while j < n and line[j] != '"':
                    if line[j] == "\\" and j + 1 < n:
                        buf.append(line[j : j + 2])
                        j += 2
                        continue
                    buf.append(line[j])
                    j += 1
                strings.append("".join(buf))
                out.append('""')
                i = j + 1
                continue
            if c == "/" and (i == 0 or line[i - 1] in " \t"):
                break
            out.append(c)
            i += 1
        code.append("".join(out).rstrip())
    return code, strings


def path_literals(text: str, tops: set[str]) -> set[str]:
    """The repository paths a q file names in a string or a `:path symbol."""
    code, strings = scan(text)
    found = set(strings)
    for line in code:
        found.update(_SYMBOL_PATH.findall(line))
    out: set[str] = set()
    for lit in found:
        lit = lit.strip()
        if lit.startswith(":"):
            lit = lit[1:]
        if lit.startswith("./"):
            lit = lit[2:]
        if lit in tops:  # a top-level directory on its own: "src"
            out.add(lit)
            continue
        # a path in a q command or a message - "l src/x.q", "run tests/q/x.q"
        for word in lit.split():
            word = word.rstrip(".,;:)'")
            if word.split("/", 1)[0] in tops:  # "find src scripts -name '*.q'" too
                out.add(word)
    return out


def covers(lit: str, path: str, known: set[str]) -> bool:
    """Whether a path literal names `path`: the file, a directory above it, or
    - when the literal is no path that exists - a prefix a computed name ends."""
    if path == lit:
        return True
    if path.startswith(lit.rstrip("/") + "/"):
        return True
    return lit not in known and path.startswith(lit)


def definitions(text: str) -> list[str]:
    """The names a file defines at column 0, fully qualified."""
    code, _ = scan(text)
    return [m.group(1) for line in code if (m := _DEF.match(line))]


def literal_loads(text: str) -> list[str]:
    """The paths a file loads by `\\l path` or `system "l path"`."""
    out = []
    for line in text.splitlines():
        for m in _LOAD.finditer(line.strip()):
            out.append((m.group(1) or m.group(2)).lstrip("./"))
    return out


def chunks(text: str) -> list[tuple[str, str]]:
    """A harness file cut into its top-level definitions: [(name, text)].

    A chunk starts at a column-0 line that is neither blank nor a comment and
    runs to the next. Comment lines above a definition travel with it, so
    a docstring's path is the helper's own.
    """
    out: list[tuple[str, list[str]]] = []
    pending: list[str] = []
    for line in text.splitlines():
        top = line[:1] not in ("", " ", "\t", "/", "}", ")", "]")
        if top:
            m = _DEF.match(line)
            out.append((m.group(1) if m else f"line{len(out)}", pending + [line]))
            pending = []
        elif out and not line.startswith("/"):
            out[-1][1].append(line)
        else:
            pending.append(line)
    return [(name, "\n".join(lines) + "\n") for name, lines in out]


# --------------------------------------------------------------------------
# The tree
# --------------------------------------------------------------------------
def tracked(root: Path) -> list[str]:
    """Every file of the tree: git's view when it is a checkout, else a walk."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-co", "--exclude-standard"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        return sorted(p for p in out if (root / p).is_file())
    except subprocess.CalledProcessError, FileNotFoundError:
        found = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in (".git", ".venv", "node_modules")]
            for f in filenames:
                found.append(str(Path(dirpath, f).relative_to(root)))
        return sorted(found)


def is_suite(path: str) -> bool:
    return (
        path.startswith(SUITE_DIR + "/")
        and "/" not in path[len(SUITE_DIR) + 1 :]
        and Path(path).name.startswith("test_")
        and path.endswith(".q")
    )


def full_suite_reason(path: str) -> str | None:
    for prefix, why in FULL_SUITE.items():
        if path == prefix or (prefix.endswith("/") and path.startswith(prefix)):
            return f"{path}: {why}"
    return None


@dataclass
class Tree:
    """The q files the suite loads or starts, and the edges between them."""

    root: Path
    files: list[str]
    texts: dict[str, str]
    runner: set[str]  # loaded into the test process
    children: set[str]  # started as child processes, by path
    nodes: dict[str, str]  # node -> the file it is (a harness chunk -> its file)
    deps: dict[str, set[str]]  # node -> nodes it depends on
    dynamic: dict[str, list[dict]]  # file -> its dynamic entries
    literals: dict[str, set[str]]  # node -> the path literals it names
    text_deps: dict[str, set[str]] = field(default_factory=dict)  # node -> files it reads
    registries: dict[str, str] = field(default_factory=dict)  # entry point -> shell
    declarations: dict[str, set[str]] = field(default_factory=dict)  # shell -> declarations
    registered: dict[str, set[str]] = field(default_factory=dict)  # declaration -> names

    @property
    def suites(self) -> list[str]:
        return sorted(f for f in self.files if is_suite(f))

    def dependents(self, text: bool = False) -> dict[str, set[str]]:
        """Inverted deps - or, with `text`, the files that read each one."""
        inv: dict[str, set[str]] = {n: set() for n in self.nodes}
        for a, bs in (self.text_deps if text else self.deps).items():
            for b in bs:
                inv.setdefault(b, set()).add(a)
        return inv

    def nodes_of(self, path: str) -> list[str]:
        return [n for n, f in self.nodes.items() if f == path]


def runner_files(files: list[str], texts: dict[str, str]) -> set[str]:
    """What the test process loads: src/, the suites, the harness, the runner's
    own loads, and what those load by a literal path."""
    known = set(files)
    out = {f for f in files if f.endswith(".q") and f.startswith("src/")}
    out |= {f for f in files if is_suite(f)}
    out |= {f for f in files if f.startswith(HARNESS_DIR) and f.endswith(".q")}
    todo = [RUNNER, *sorted(out)]
    seen = set()
    while todo:
        f = todo.pop()
        if f in seen or f not in texts:
            continue
        seen.add(f)
        for p in literal_loads(texts[f]):
            if p in known and p.endswith(".q") and not p.startswith("lib/"):
                if p != RUNNER:
                    out.add(p)
                todo.append(p)
    return out


def build(root: Path = REPO, qlinter: str | None = None) -> Tree:
    """Read the tree and draw its graph."""
    qlinter = qlint_graph.require(qlinter)
    files = tracked(root)
    dirs = {str(p) for f in files for p in Path(f).parents if str(p) != "."}
    known = set(files) | dirs
    tops = {f.split("/", 1)[0] for f in files}
    qfiles = [f for f in files if f.endswith(".q") and not f.startswith("lib/")]
    texts = {f: (root / f).read_text(errors="replace") for f in qfiles}
    runner = runner_files(files, texts)

    # children: .q files outside the process that a loaded file names by path
    lits = {f: path_literals(texts[f], tops) for f in qfiles}
    children: set[str] = set()
    frontier = set(runner)
    while frontier:
        new = set()
        for f in frontier:
            for lit in lits[f]:
                for q in qfiles:
                    if q not in runner and q not in children and covers(lit, q, known):
                        new.add(q)
        children |= new
        frontier = new

    # nodes: one per file, the harness cut into its definitions
    nodes: dict[str, str] = {}
    node_text: dict[str, str] = {}
    for f in sorted(runner | children):
        if f.startswith(HARNESS_DIR) and f != QUNIT:
            for i, (name, text) in enumerate(chunks(texts[f])):
                node = f"{f}.chunks/{i:04d}_{re.sub(r'[^A-Za-z0-9_]', '_', name)}.q"
                nodes[node] = f
                node_text[node] = text
        elif f != QUNIT:
            nodes[f] = f
            node_text[f] = texts[f]

    with tempfile.TemporaryDirectory(prefix="test_impact_") as tmp:
        for node, text in node_text.items():
            p = Path(tmp, node)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        g = qlint_graph.graph(
            sorted(node_text), cwd=Path(tmp), detail=True, symbols=True, qlinter=qlinter
        )

    def kind(n: str) -> str:
        f = nodes[n]
        if is_suite(f):
            return "suite"
        if f.startswith(HARNESS_DIR):
            return "harness"
        if f in children:
            return "child"
        return "prod"

    # the .qt names each suite defines, for the only name edges into a suite
    qt_defs: dict[str, set[str]] = {}
    for n in nodes:
        if kind(n) == "suite":
            qt_defs[n] = {
                m.group(1) for line in scan(node_text[n])[0] if (m := _QT_DEF.match(line))
            }
    qt_refs = {n: set(_QT_REF.findall("\n".join(scan(t)[0]))) for n, t in node_text.items()}
    # a helper naming .qt.support or .qt.fixture is about the reserved
    # subtrees, not a suite: `.qt.support.job_suite` building
    # `.qt.qpipe.job.<job>` reaches the job suites, the reserved check does not
    for n in nodes:
        if kind(n) == "harness":
            qt_refs[n] = {r for r in qt_refs[n] if not r.startswith((".qt.support", ".qt.fixture"))}

    node_lits = {n: path_literals(node_text[n], tops) for n in nodes}
    deps: dict[str, set[str]] = {n: set() for n in nodes}
    dynamic: dict[str, list[dict]] = {}
    for n, entry in g.items():
        if n not in nodes:
            continue
        if entry.get("dynamic"):
            dynamic.setdefault(nodes[n], []).extend(entry["dynamic"])
        for d in entry.get("dependencies", []):
            if d not in nodes or d == n:
                continue
            kd = kind(d)
            if kd in ("suite", "child"):
                continue  # only by a .qt name or a path, below
            if kind(n) in ("prod", "child") and nodes[d].startswith("tests/"):
                continue  # production never calls test code: a test's double of its name
            deps[n].add(d)
    # into a suite: a .qt name it defines
    for n in nodes:
        for s, defs in qt_defs.items():
            builder = kind(n) == "harness"
            if s != n and defs and any(_names(r, defs, builder) for r in qt_refs[n]):
                deps[n].add(s)
    # path literals: a file named, a directory above it, a computed prefix
    # - but production and the harness never depend on a test file by naming
    # it: that is an error message's hint (`run tests/q/smoke_kafka_feed.q`).
    # A child named is RUN, so what it loads reaches the namer as code does;
    # any other file named is READ, and only its own change reaches the
    # reader - not every change that reaches it (text_deps).
    text_deps: dict[str, set[str]] = {n: set() for n in nodes}
    for n, ls in node_lits.items():
        for lit in ls:
            for m, f in nodes.items():
                if m == n or not covers(lit, f, known):
                    continue
                if kind(n) in ("prod", "harness") and f.startswith("tests/q/"):
                    continue
                # a child named by its own path, or by a computed one, is run;
                # one under a directory named whole is only listed or read
                run = kind(m) == "child" and lit.rstrip("/") not in dirs
                (deps if run else text_deps)[n].add(m)

    tree = Tree(root, files, texts, runner, children, nodes, deps, dynamic, node_lits)
    tree.text_deps = text_deps
    _link_registries(tree)
    # a reflective file may read any name at all
    for f, (k, _why) in DYNAMIC_REVIEWED.items():
        if k == "reflective":
            for n in tree.nodes_of(f):
                deps[n] |= {m for m in nodes if m != n and kind(m) == "prod"}
    return tree


def _names(ref: str, defs: set[str], prefix: bool = False) -> bool:
    """Whether a `.qt` reference reads one of `defs`, or something under one.

    With `prefix`, a reference that is the namespace ABOVE a definition counts
    too: a helper building `.qt.qpipe.job.<job>` reaches every job suite.
    """
    return any(
        ref == d or ref.startswith(d + ".") or (prefix and d.startswith(ref + ".")) for d in defs
    )


def entry_points(tree: Tree) -> dict[str, str]:
    """{entry point: the shell defining it}.

    A function under src/etl/core/ whose last name segment is define,
    register*, declare* or watch - and every function there that calls one,
    since a wrapper (`.qetl.job.stream.at_bars` calling
    `.qetl.job.stream.bars.define`) registers just the same.
    """
    bodies: dict[str, tuple[str, str]] = {}
    for f in sorted(tree.texts):
        if f.startswith(CORE_DIR):
            for name, text in chunks(tree.texts[f]):
                if name.startswith("."):
                    bodies[name] = (f, text)
    out = {n: f for n, (f, _) in bodies.items() if ENTRY_POINT.match(n.rsplit(".", 1)[-1])}
    grew = True
    while grew:
        grew = False
        for n, (f, text) in bodies.items():
            if n not in out and any(_calls(text, e) for e in out):
                out[n] = f
                grew = True
    return out


def _calls(text: str, name: str) -> bool:
    code = "\n".join(scan(text)[0])
    return re.search(re.escape(name) + r"(?![\w.])", code) is not None


def _link_registries(tree: Tree) -> None:
    """Find each shell's declarations, and the suites that name what they register.

    A declaration is a file outside tests/ calling an entry point. What it
    registers - the symbol its call starts with, `.qetl.transform.define[`fx_markout;...]`
    - is how a suite drives it through the shell (`.qetl.transform.apply[`fx_markout;...]`),
    and that symbol is no name the graph reads, so a suite naming it is linked
    to the declaration here.
    """
    tree.registries = entry_points(tree)
    for name, shell in tree.registries.items():
        call = re.compile(re.escape(name) + r"(?![\w.])\s*\[\s*`([A-Za-z_]\w*)")
        for f in sorted(set(tree.nodes.values())):
            if f == shell or f.startswith("tests/") or f not in tree.texts:
                continue
            if not _calls(tree.texts[f], name):
                continue
            tree.declarations.setdefault(shell, set()).add(f)
            code = "\n".join(scan(tree.texts[f])[0])
            tree.registered.setdefault(f, set()).update(call.findall(code))
    symbol = {
        n: set(re.findall(r"`([A-Za-z_]\w*)", "\n".join(scan(tree.texts[f])[0])))
        for n, f in tree.nodes.items()
        if is_suite(f)
    }
    for decl, names in tree.registered.items():
        for n, syms in symbol.items():
            if names & syms:
                for d in tree.nodes_of(decl):
                    tree.deps[n].add(d)


def through_shell(tree: Tree, inv: dict[str, set[str]]) -> dict[str, set[str]]:
    """{declaration node: the suites that run through the shell it registers into}.

    The suites depending on the shell directly. The walk does not go on from
    the shell to everything else that depends on it: what changed is one
    entry in its registry, not the shell, and every other declaration
    registered beside it is untouched.
    """
    out: dict[str, set[str]] = {}
    for shell, decls in tree.declarations.items():
        suites = {
            m for n in tree.nodes_of(shell) for m in inv.get(n, ()) if is_suite(tree.nodes[m])
        }
        for d in decls:
            for n in tree.nodes_of(d):
                out.setdefault(n, set()).update(suites)
    return out


#: Top-level calls a declaration makes into src/etl/core/ that register
#: nothing, with the reason - the only hand list here, and
#: scripts/dev/tests/test_test_impact.py fails on any top-level call that is
#: neither an entry point nor in it, so a new registry cannot go unlinked.
NOT_REGISTRIES = {
    ".qetl.kafka.decoder.init": "seeds a decoder job's own state, under its namespace",
    ".qetl.kafka.decoder.check": "validates a decoder job's declaration at load",
}


def unregistered_calls(tree: Tree) -> list[str]:
    """Top-level calls in a declaration file into src/etl/core/ that are
    neither an entry point nor in NOT_REGISTRIES (`file:line: name`)."""
    core_names = {
        name for f in tree.texts if f.startswith(CORE_DIR) for name in definitions(tree.texts[f])
    }
    out = []
    for f in sorted(tree.texts):
        if not f.startswith(DECLARATION_DIRS):
            continue
        for i, line in enumerate(scan(tree.texts[f])[0], 1):
            if not line[:1] == "." or _DEF.match(line):
                continue
            m = re.match(r"(\.[A-Za-z_][\w.]*)", line)
            if (
                m
                and m.group(1) in core_names
                and m.group(1) not in tree.registries
                and m.group(1) not in NOT_REGISTRIES
            ):
                out.append(f"{f}:{i}: {m.group(1)}")
    return out


# --------------------------------------------------------------------------
# Selecting
# --------------------------------------------------------------------------
@dataclass
class Selection:
    files: list[str]  # the suites to run, when not all
    total: int
    reasons: list[str]  # why ALL; empty for a subset

    @property
    def all(self) -> bool:
        return bool(self.reasons)


def select(changed: Iterable[str], tree: Tree) -> Selection:
    """The suites `changed` reaches in `tree`, or ALL with the reasons."""
    changed = sorted(set(changed))
    total = len(tree.suites)
    reasons: list[str] = []
    starts: set[str] = set()
    known = set(tree.files) | {str(Path(f).parent) for f in tree.files}
    for path in changed:
        if why := full_suite_reason(path):
            reasons.append(why)
            continue
        exists = (tree.root / path).is_file()
        if path.endswith(".q"):
            if not exists:
                reasons.append(f"{path}: a deleted q file - what read its names is not in the tree")
                continue
            nodes = tree.nodes_of(path)
            if not nodes:
                if path.startswith("lib/") or path in tree.texts:
                    # a .q nothing in the suite loads or starts: no suite sees it
                    continue
                reasons.append(f"{path}: a q file this cannot place")
                continue
            # a child's run-time names live in its own process: only the
            # suite starting it can see them, and that suite names it
            hidden = path in tree.runner and not is_suite(path)
            if hidden and path in tree.dynamic and path not in DYNAMIC_REVIEWED:
                lines = sorted({d["line"] for d in tree.dynamic[path]})
                reasons.append(
                    f"{path}: names reached at run time (lines {', '.join(map(str, lines[:8]))}"
                    f"{', ...' if len(lines) > 8 else ''}) that DYNAMIC_REVIEWED does not explain"
                )
                continue
            starts.update(nodes)
            continue
        # a file q reads (a CSV, a fixture, a baseline): what reads it changed.
        # A bare top-level directory (`find src scripts ...`) lists q files; it
        # does not read a Dockerfile under tests/.
        readers = sorted(
            n
            for n, ls in tree.literals.items()
            if any("/" in lit and covers(lit, path, known) for lit in ls)
        )
        # a harness helper reading it is a node of its own: its suites, not all
        loaders = [n for n in readers if n == tree.nodes[n] and full_suite_reason(n)]
        if loaders:
            reasons.append(f"{path}: read by {tree.nodes[loaders[0]]}, which every suite loads")
            continue
        starts.update(readers)
    if reasons:
        return Selection(list(tree.suites), total, reasons)

    inv = tree.dependents()
    via_shell = through_shell(tree, inv)
    # what reads a changed file as text: one hop, from the changed files only
    read_by = tree.dependents(text=True)
    readers = {m for n in starts for m in read_by.get(n, ())}
    reached = set(starts) | readers
    todo = list(reached)
    while todo:
        n = todo.pop()
        if n not in starts and n == tree.nodes[n] and full_suite_reason(n):
            continue  # a loader: it loads what changed, it does not use it
        for m in inv.get(n, set()) | via_shell.get(n, set()):
            if m not in reached:
                reached.add(m)
                todo.append(m)
    picked = {tree.nodes[n] for n in reached if is_suite(tree.nodes[n])}
    return Selection(sorted(picked), total, [])


def changed_paths(base: str, root: Path = REPO, head: str = "HEAD") -> list[str]:
    """What `head` changes against `base`: the three-dot diff, as CI reads a PR.

    `--no-renames`, so a renamed file is its old path deleted and its new one
    added - a deleted .q runs the whole suite, which a rename must too."""
    out = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", f"{base}...{head}"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [p for p in out.splitlines() if p]


def uncommitted(root: Path = REPO) -> list[str]:
    """What the index and the working tree change against HEAD - in a commit
    hook, the commit being made (pre-commit has set unstaged changes aside)."""
    out = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [p for p in out.splitlines() if p]


# --------------------------------------------------------------------------
# The q-tests gate (#1690 step 4)
# --------------------------------------------------------------------------
#: What the q-tests hook and scripts/dev/attest_kdbx.py select against: the
#: branch's whole change, as CI's KDB-X attestation check reads the PR.
BASE = "origin/master"
#: `1` runs the whole q suite whatever changed.
FULL_ENV = "UQF_Q_TESTS_FULL"
#: The decision attest_kdbx.py made, handed to the hook it runs - one
#: selection per attestation, the one its trailer records, not a second one
#: worked out inside the hook.
DECIDED_ENV = "UQF_Q_TESTS_SELECTION"


@dataclass
class Plan:
    """What the q-tests gate runs: `files` (bare suite file names, sorted),
    or None for the whole suite; `why` says which and why."""

    files: list[str] | None
    why: str

    @property
    def line(self) -> str:
        """The hook's one line: ALL, NONE, or the runner's `-files` argument."""
        if self.files is None:
            return "ALL"
        if not self.files:
            return "NONE"
        return "-files " + ",".join(self.files)

    @property
    def record(self) -> str:
        """What the attestation trailer records: `all`, or the subset's digest."""
        return "all" if self.files is None else digest(self.files)


def digest(files: Iterable[str]) -> str:
    """sha256 of the sorted bare file names, one per line - the subset as the
    attestation trailer records it, and as CI recomputes it."""
    names = sorted({Path(f).name for f in files})
    return hashlib.sha256("\n".join(names).encode()).hexdigest()


def parse_line(line: str) -> Plan:
    """The inverse of Plan.line, for a decision handed over in DECIDED_ENV."""
    line = line.strip()
    if line == "ALL":
        return Plan(None, f"${DECIDED_ENV}=ALL")
    if line == "NONE":
        return Plan([], f"${DECIDED_ENV}=NONE")
    if line.startswith("-files ") and line[7:].strip():
        files = sorted({f for f in line[7:].strip().split(",") if f})
        return Plan(files, f"${DECIDED_ENV} names {len(files)} suite files")
    raise ValueError(f"${DECIDED_ENV} is {line!r}: want ALL, NONE or -files a.q,b.q")


def plan_for(changed: Iterable[str], root: Path = REPO) -> Plan:
    """The subset `changed` reaches in the tree at `root`, or the whole suite -
    when the selector says ALL, or cannot run (no graph-capable qlinter)."""
    try:
        tree = build(root)
    except qlint_graph.GraphUnavailable as e:
        short = str(e).split(". Needed:")[0]
        return Plan(None, f"the selector cannot run: {short} (see .qlinter-graph-rev)")
    except RuntimeError as e:
        return Plan(None, f"the selector failed: {e}")
    sel = select(changed, tree)
    if sel.all:
        return Plan(None, "the selector says ALL: " + "; ".join(sel.reasons))
    files = sorted(Path(f).name for f in sel.files)
    return Plan(files, f"{len(files)} of {sel.total} suite files reach the change")


def plan(base: str = BASE, root: Path = REPO, *, with_uncommitted: bool = True) -> Plan:
    """What the q-tests gate runs here: the whole suite under FULL_ENV, else
    what `base...HEAD` (and, in a hook, the commit being made) reaches."""
    if os.environ.get(FULL_ENV) == "1":
        return Plan(None, f"${FULL_ENV}=1")
    try:
        changed = changed_paths(base, root)
        if with_uncommitted:
            changed += uncommitted(root)
    except subprocess.CalledProcessError as e:
        return Plan(None, f"cannot diff against {base}: {(e.stderr or '').strip()}")
    return plan_for(changed, root)


def hook_line(root: Path = REPO) -> Plan:
    """The q-tests hook's decision: the one attest_kdbx.py handed over, else
    plan() - which falls back to the whole suite, never to nothing."""
    decided = os.environ.get(DECIDED_ENV)
    if decided is not None and os.environ.get(FULL_ENV) != "1":
        return parse_line(decided)
    return plan(root=root)


def render(sel: Selection, fmt: str) -> str:
    if fmt == "json":
        return json.dumps(
            {"all": sel.all, "reasons": sel.reasons, "files": sel.files, "total": sel.total},
            indent=2,
        )
    if fmt == "args":
        # nothing for ALL - the runner without -files - and nothing for an
        # empty selection either, which `--format json` tells apart
        if sel.all or not sel.files:
            return ""
        return "-files " + ",".join(Path(f).name for f in sel.files)
    if sel.all:
        return "\n".join(["ALL", *(f"reason: {r}" for r in sel.reasons)])
    return "\n".join(sel.files)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="*", help="changed paths, relative to the repository root")
    ap.add_argument("--base", help="select for what HEAD changes against this ref (three-dot)")
    ap.add_argument("--root", type=Path, default=REPO, help=argparse.SUPPRESS)
    ap.add_argument("--format", choices=("text", "json", "args"), default="text")
    ap.add_argument(
        "--hook",
        action="store_true",
        help=(
            f"the q-tests hook's line: ALL, NONE or `-files ...`, for {BASE}...HEAD and "
            f"what is uncommitted; ALL when ${FULL_ENV}=1 or the selector cannot run; "
            f"${DECIDED_ENV} if set. Always exits 0 with a line"
        ),
    )
    args = ap.parse_args(argv)
    if args.hook:
        try:
            decision = hook_line(args.root)
        except ValueError as e:
            print(f"test_impact: {e}", file=sys.stderr)
            return 2
        print(f"test_impact: q-tests: {decision.why}", file=sys.stderr)
        print(decision.line)
        return 0
    if not args.paths and not args.base:
        ap.error("give changed paths, or --base REF")
    changed = list(args.paths)
    if args.base:
        changed += changed_paths(args.base, args.root)
    try:
        tree = build(args.root)
    except qlint_graph.GraphUnavailable as e:
        print(f"test_impact: {e}", file=sys.stderr)
        return 2
    print(render(select(changed, tree), args.format))
    return 0


if __name__ == "__main__":
    sys.exit(main())
