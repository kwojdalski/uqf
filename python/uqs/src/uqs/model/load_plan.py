"""Which declaration files a process loads: the closure of its own jobs (#902).

src/etl/init.q used to load EVERY declaration in every process. A declaration
asks the plant for its tables at load time (`.qetl.plant.columns[`quote;...]`),
so one job whose table a deployment's schema lacks stopped every unrelated
process from starting - a sidecar against a managed TorQ schema without
`quote` could not load because the demo's market_data job needed it.

So a process may name what it runs, and loads only that: its declarations,
plus everything they reach - the sources and transforms they use, the peer
jobs whose helpers they call (superbook calls market_data's), the reactions
that run where they write - closed transitively. With no selection the whole
tree still loads, which is what tests and the docs tooling want.

READ AS TEXT, like the registry (model/declarations.py): resolving what a
process needs must not run the declarations whose failure this exists to
avoid. A reference is any of

  - `.qpipe.job.<name>` or `.qpipe.source.<name>`, outside a comment;
  - `.qetl.transform.apply[`X` (and its variants), or a declared `transform`;
  - a worker's declared `source`;

and it resolves to the file that defines that name. A reference nothing defines is
someone else's (a library namespace) and is ignored. Over-including is safe;
the closure only has to be a superset of what the load needs, never the whole
tree.

The order is init.q's: sources, transforms, workers, streaming, reactions,
each alphabetical - so a selected load is the full load with files left out,
and every within-directory rule init.q states still holds.

scripts/generate/generate_operational_docs.py writes this down as
src/etl/generated/load_plan.q, which init.q reads; CI holds the two equal.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from uqs.model.declarations import (
    declaration_calls,
    reaction_calls,
    read_file_text,
    strip_q_comments,
    symbols,
)
from uqs.paths import UqsError

#: init.q's directory order, which is load-bearing (see its header).
DIRS = ("sources", "transforms", "workers", "streaming", "reactions")
ETL = Path("src") / "etl"
PLAN_FILE = ETL / "generated" / "load_plan.q"

_NAMESPACE = re.compile(r"^\\d\s+\.qpipe\.(job|source)\.(\w+)\s*$", re.MULTILINE)
_QPIPE_REF = re.compile(r"\.qpipe\.(job|source)\.([a-zA-Z_]\w*)")
_TRANSFORM_DEF = re.compile(r"\.qetl\.transform\.define\[\s*`(\w+)")
_TRANSFORM_USE = re.compile(r"\.qetl\.transform\.\w+\[\s*`(\w+)")
_SOURCE_DEF = re.compile(r"\.qetl\.source\.define\[\s*`(\w+)")


@dataclass(frozen=True)
class FileFacts:
    """What one declaration file defines and refers to, as `kind:name` keys."""

    path: str
    defines: frozenset[str]
    refers: frozenset[str]
    #: the jobs (and workers) it declares, and the process each runs in
    jobs: tuple[tuple[str, str], ...]
    #: the datasets this file's jobs write, which a reaction may watch
    writes: frozenset[str]
    #: for a reaction file, the datasets it watches
    watches: frozenset[str]


def declaration_files(root: Path) -> list[str]:
    """Every declaration file, relative to `root`, in init.q's load order."""
    found = []
    for d in DIRS:
        found += [p.relative_to(root).as_posix() for p in sorted((root / ETL / d).glob("*.q"))]
    return found


def facts(rel: str, text: str) -> FileFacts:
    """What `text` (the file at `rel`) defines and refers to."""
    code = strip_q_comments(text)
    defines = {f"{k}:{n}" for k, n in _NAMESPACE.findall(code)}
    defines |= {f"transform:{n}" for n in _TRANSFORM_DEF.findall(code)}
    defines |= {f"source:{n}" for n in _SOURCE_DEF.findall(code)}
    refers = {f"{k}:{n}" for k, n in _QPIPE_REF.findall(code)}
    refers |= {f"transform:{n}" for n in _TRANSFORM_USE.findall(code)}
    jobs, writes = [], set()
    for _fn, name, fields in declaration_calls(text):
        defines.add(f"job:{name}")
        for key, kind in (("source", "source"), ("transform", "transform")):
            refers |= {f"{kind}:{s}" for s in symbols(fields.get(key, ""))}
        writes |= set(symbols(fields.get("dataset", ""))) | set(
            symbols(fields.get("publishes", ""))
        )
    for d in read_file_text(text, Path(rel)):
        jobs.append((d.name, d.procname))
    watches = {r.dataset for r in reaction_calls(text)}
    defines |= {f"job:{r.name}" for r in reaction_calls(text)}
    return FileFacts(
        rel,
        frozenset(defines),
        frozenset(refers - defines),
        tuple(jobs),
        frozenset(writes),
        frozenset(watches),
    )


def read_facts(root: Path, extra: Mapping[str, str] | None = None) -> list[FileFacts]:
    """Every declaration file's facts, in load order. `extra` (relative path ->
    q) stands in for files on disk, or adds them."""
    texts = {rel: (root / rel).read_text(encoding="utf-8") for rel in declaration_files(root)}
    texts.update(extra or {})
    order = {d: i for i, d in enumerate(DIRS)}
    rels = sorted(texts, key=lambda r: (order.get(Path(r).parent.name, len(DIRS)), r))
    return [facts(rel, texts[rel]) for rel in rels]


class LoadPlan:
    """The closure of each job and each process over the declaration files."""

    def __init__(self, files: Iterable[FileFacts]):
        self.files = list(files)
        self._order = {f.path: i for i, f in enumerate(self.files)}
        self._definer: dict[str, str] = {}
        for f in self.files:
            for key in f.defines:
                # Two files defining one name (a transform declared twice) both
                # count; the first in load order is the one a reference needs.
                self._definer.setdefault(key, f.path)
        self.jobs: dict[str, str] = {}
        self.procs: dict[str, list[str]] = {}
        for f in self.files:
            for name, procname in f.jobs:
                self.jobs[name] = f.path
                self.procs.setdefault(procname, []).append(name)

    def _deps(self, f: FileFacts) -> set[str]:
        found = {self._definer[k] for k in f.refers if k in self._definer}
        # A reaction runs inside whichever process writes what it watches.
        found |= {r.path for r in self.files if r.watches & f.writes}
        return found - {f.path}

    def closure(self, paths: Iterable[str]) -> list[str]:
        """`paths` and everything they reach, in load order."""
        by_path = {f.path: f for f in self.files}
        seen: set[str] = set()
        todo = list(paths)
        while todo:
            p = todo.pop()
            if p in seen:
                continue
            seen.add(p)
            todo += sorted(self._deps(by_path[p]) - seen)
        return sorted(seen, key=self._order.__getitem__)

    def for_job(self, name: str) -> list[str]:
        return self.closure([self.jobs[name]])

    def for_proc(self, procname: str) -> list[str]:
        return self.closure(self.jobs[j] for j in self.procs[procname])

    def select(self, names: Iterable[str]) -> list[str]:
        """The files a process naming `names` (procnames or job names) loads;
        none for no names - the infrastructure-only selection."""
        paths: list[str] = []
        unknown = []
        for n in names:
            if n in self.procs:
                paths += [self.jobs[j] for j in self.procs[n]]
            elif n in self.jobs:
                paths.append(self.jobs[n])
            else:
                unknown.append(n)
        if unknown:
            raise UqsError(
                f"no declared job or process named {', '.join(unknown)} - "
                f"the load plan knows {len(self.jobs)} job(s) under src/etl"
            )
        return self.closure(paths)


def load_plan(root: Path, extra: Mapping[str, str] | None = None) -> LoadPlan:
    return LoadPlan(read_facts(root, extra))


def _q_strings(paths: list[str]) -> str:
    body = ";".join(f'"{p}"' for p in paths)
    return f"enlist {body}" if len(paths) == 1 else f"({body})" if paths else "()"


def render(plan: LoadPlan) -> str:
    """The plan as q: every file in load order, and each job's and process's
    files, and the jobs each file declares (for a load error to name)."""
    lines = [
        "/ GENERATED BY scripts/generate/generate_operational_docs.py - DO NOT EDIT.",
        "/ .",
        "/ Each job's and each process's declaration files, closed over what they",
        "/ use (python/uqs/src/uqs/model/load_plan.py, #902), in src/etl/init.q's",
        "/ load order. init.q loads only these when a process names what it runs;",
        "/ the whole tree otherwise (src/etl/core/declaration_load.q).",
        "",
        f".qetl.load.order:{_q_strings([f.path for f in plan.files])}",
        ".qetl.load.declares:(`symbol$())!()",
    ]
    for f in plan.files:
        if f.jobs:
            names = "".join(f"`{n}" for n, _ in f.jobs)
            value = names if len(f.jobs) > 1 else f"enlist {names}"
            lines.append(f'.qetl.load.declares[`$"{f.path}"]:{value}')
    lines.append(".qetl.load.jobs:(`symbol$())!()")
    for name in sorted(plan.jobs):
        lines.append(f".qetl.load.jobs[`{name}]:{_q_strings(plan.for_job(name))}")
    lines.append(".qetl.load.procs:(`symbol$())!()")
    for proc in sorted(plan.procs):
        lines.append(f".qetl.load.procs[`{proc}]:{_q_strings(plan.for_proc(proc))}")
    return "\n".join(lines) + "\n"
