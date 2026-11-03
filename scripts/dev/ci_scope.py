#!/usr/bin/env python3
"""Which of CI's path-filtered jobs a run needs (#1879).

CI is the full, clean run: a Linux checkout with nothing left over, PeachQ as
its q. Two of its jobs are expensive and irrelevant to most changes, so a pull
request runs them only when it changes what they check:

* "Docs links" (`docs`) renders docs/ with Quarto, writing the q API pages
  from src/'s qDoc comments first - minutes, and the critical path. It runs
  when the change touches docs/, a q file under src/, the render's own
  tooling (DOCS_PATHS, and the repository modules its Python imports), or
  deletes a path a document names: a link to it would break.
* "PeachQ q tests" (`q`) runs the whole qUnit suite on PeachQ. It runs when
  the change touches a .q file, a file a q file names by a path literal
  (scripts/dev/test_impact.py's q_readers - the literal edge of its graph), or
  the job's own tooling (Q_PATHS, and the repository modules its Python
  imports).

Every other job runs on every run. A skipped job still counts as passed for
"Repository checks", the one required check, only when this said to skip it.

FULL MODE runs both, whatever changed:

* every push - CI runs on pushes to master alone, so master always runs
  everything - and release.yml's run (a called workflow sees its caller's
  event: a tag push, or a dispatch whose `full` input defaults true there);
* workflow_dispatch with `full: true`;
* a pull request labelled `ci:full`.

Prints `full`, `docs` and `q` (true/false) and why; `--output FILE` appends
the three as `name=value` lines (the workflow passes $GITHUB_OUTPUT) and
`--summary FILE` the why (its step summary).

    python3 scripts/dev/ci_scope.py --event pull_request --base SHA --head SHA --labels a,b
    python3 scripts/dev/ci_scope.py --event workflow_dispatch --full-input true
    python3 scripts/dev/ci_scope.py --base origin/master     # what this branch would run
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

#: The pull request label that runs every job.
FULL_LABEL = "ci:full"

#: This file and the workflow it decides for: a change to either runs both.
OWN = r"^\.github/workflows/ci\.yml$|^scripts/dev/ci_scope\.py$"

#: The docs job's inputs, beyond docs/ and the Python its tooling imports.
DOCS_PATHS = re.compile(
    rf"^docs/|^src/.*\.q$|^scripts/generate/export_api_reference\.q$|^scripts/peachq\.json$|{OWN}"
)
#: The Python the docs job runs: the render's pre-render step and the gate.
DOCS_ENTRIES = (
    "scripts/generate/q_api_docs.py",
    "scripts/gates/check_doc_links.py",
    "scripts/peachq.py",
)

#: The q job's inputs, beyond .q files, what q names, and the Python its tooling imports.
Q_PATHS = re.compile(
    rf"^scripts/portable/|^scripts/peachq\.json$|^pyproject\.toml$|^uv\.lock$|{OWN}"
)
#: The Python the q job runs: pytest's root conftest, the suite's driver, the
#: flattener, the PeachQ resolver.
Q_ENTRIES = (
    "conftest.py",
    "tests/qrun/test_q_suite.py",
    "scripts/portable/full_suite.py",
    "scripts/peachq.py",
)


def _test_impact():
    spec = importlib.util.spec_from_file_location(
        "test_impact", Path(__file__).resolve().parent / "test_impact.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("test_impact", module)
    spec.loader.exec_module(module)
    return module


def _module_file(name: str, near: Path, root: Path) -> Path | None:
    """The repository file `import name` loads from a file in `near`: a
    sibling module, or a workspace member's package under python/*/src."""
    parts = name.split(".")
    for base in (near, *sorted(root.glob("python/*/src"))):
        stem = base.joinpath(*parts)
        for candidate in (stem.with_suffix(".py"), stem / "__init__.py"):
            if candidate.is_file():
                return candidate
    return None


def imported(entries: Iterable[str], root: Path = REPO) -> set[str]:
    """`entries` and every repository module they import, transitively, read
    from the import statements - a package's __init__ included."""
    seen: set[Path] = set()
    todo = [root / e for e in entries if (root / e).is_file()]
    while todo:
        path = todo.pop()
        if path in seen:
            continue
        seen.add(path)
        for node in ast.walk(ast.parse(path.read_text())):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names = [node.module, *(f"{node.module}.{a.name}" for a in node.names)]
            for name in names:
                parts = name.split(".")
                for i in range(1, len(parts) + 1):
                    found = _module_file(".".join(parts[:i]), path.parent, root)
                    if found and found not in seen:
                        todo.append(found)
    return {p.relative_to(root).as_posix() for p in seen}


def changed(base: str, head: str = "HEAD", root: Path = REPO) -> list[tuple[str, str]]:
    """(status letter, path) for what `head` changes against `base`: the
    three-dot diff, as a pull request is read; a rename is a deletion and an
    addition."""
    out = subprocess.run(
        ["git", "diff", "--name-status", "--no-renames", "-z", f"{base}...{head}"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    words = [w for w in out.split("\0") if w]
    return [(words[i][0], words[i + 1]) for i in range(0, len(words) - 1, 2)]


def named_in_docs(paths: Iterable[str], root: Path = REPO) -> dict[str, str]:
    """{path: a document naming it} for each path whose file name appears in
    a document under docs/ - a link that a deletion would break."""
    docs = [p for p in sorted((root / "docs").rglob("*")) if p.suffix in (".md", ".qmd")]
    texts = {p.relative_to(root).as_posix(): p.read_text(errors="replace") for p in docs}
    out = {}
    for path in paths:
        name = Path(path).name
        doc = next((d for d, text in texts.items() if name in text), None)
        if doc:
            out[path] = doc
    return out


@dataclass
class Scope:
    full: bool
    docs: bool
    q: bool
    why: dict[str, list[str]] = field(default_factory=dict)

    def outputs(self) -> dict[str, str]:
        return {k: str(getattr(self, k)).lower() for k in ("full", "docs", "q")}


def full_reason(event: str, labels: Iterable[str], full_input: bool) -> str | None:
    """Why this run is a full one, or None when it is filtered."""
    if event == "pull_request":
        return f"the pull request is labelled {FULL_LABEL}" if FULL_LABEL in labels else None
    if event == "workflow_dispatch":
        return "workflow_dispatch with full: true" if full_input else None
    # A push (to master; release.yml calls this on a tag push too) or
    # anything else: everything.
    return f"a {event} - every push runs every job"


def decide(
    changes: list[tuple[str, str]],
    full: str | None,
    root: Path = REPO,
) -> Scope:
    """The jobs `changes` need; both under `full`."""
    if full:
        return Scope(True, True, True, {"docs": [full], "q": [full]})
    docs_py = imported(DOCS_ENTRIES, root)
    q_py = imported(Q_ENTRIES, root)
    paths = [p for _, p in changes]
    q_reads = _test_impact().q_readers([p for p in paths if not p.endswith(".py")], root)
    gone = named_in_docs([p for s, p in changes if s == "D"], root)
    why: dict[str, list[str]] = {"docs": [], "q": []}
    for path in paths:
        if DOCS_PATHS.search(path) or path in docs_py:
            why["docs"].append(f"{path}: rendered, or the render's tooling")
        elif path in gone:
            why["docs"].append(f"{path}: deleted, and {gone[path]} names it")
        if path.endswith(".q"):
            why["q"].append(f"{path}: q")
        elif Q_PATHS.search(path) or path in q_py:
            why["q"].append(f"{path}: the q job's tooling")
        elif q_reads.get(path):
            why["q"].append(f"{path}: named by {q_reads[path][0]}")
    return Scope(False, bool(why["docs"]), bool(why["q"]), why)


def report(scope: Scope, changes: int) -> str:
    lines = [f"### CI scope ({'full' if scope.full else f'{changes} changed path(s)'})", ""]
    for job, title in (("docs", "Docs links"), ("q", "PeachQ q tests")):
        ran = getattr(scope, job)
        reasons = scope.why.get(job, [])
        lines.append(f"- **{title}**: {'runs' if ran else 'skipped - nothing it checks changed'}")
        lines += [f"  - {r}" for r in reasons[:10]]
        if len(reasons) > 10:
            lines.append(f"  - ... and {len(reasons) - 10} more")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--event", default="pull_request", help="github.event_name")
    ap.add_argument("--base", default="origin/master", help="what the change is read against")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--labels", default="", help="the pull request's labels, comma-separated")
    ap.add_argument("--full-input", default="false", help="the `full` input, true or false")
    ap.add_argument("--output", type=Path, help="append name=value lines here")
    ap.add_argument("--summary", type=Path, help="append the why, as markdown, here")
    args = ap.parse_args(argv)

    labels = {lab.strip() for lab in args.labels.split(",") if lab.strip()}
    full = full_reason(args.event, labels, args.full_input.lower() == "true")
    changes = [] if full else changed(args.base, args.head)
    scope = decide(changes, full)
    text = report(scope, len(changes))
    print(text, end="")
    print(" ".join(f"{k}={v}" for k, v in scope.outputs().items()))
    if args.output:
        with args.output.open("a") as f:
            f.writelines(f"{k}={v}\n" for k, v in scope.outputs().items())
    if args.summary:
        with args.summary.open("a") as f:
            f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
