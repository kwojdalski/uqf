#!/usr/bin/env python3
"""Which of CI's verification groups a run needs, and why (#1879, #1955).

CI is the full, clean run: a Linux checkout with nothing left over, PeachQ as
its q. "Hooks and static gates" runs every hook over every file on every run.
The four other groups are expensive and irrelevant to some changes, so a run
that is not full runs each only when the change reaches what that group checks
(and a pull request runs only the browser application of them, below), and
runs the group COMPLETE - CI never selects single tests (the commit hooks do):

* "Docs links" (`docs`) renders docs/ with Quarto, writing the q API pages
  from src/'s qDoc comments first - minutes, and the critical path. It runs
  when the change touches docs/, a q file under src/, the render's own
  tooling (DOCS_PATHS, and the repository modules its Python imports), or
  deletes a path a document names: a link to it would break.
* "PeachQ q tests" (`q`) runs the whole qUnit suite on PeachQ. It runs when
  the change touches a .q file, a file a q file names by a path literal
  (scripts/dev/q_test_impact.py's q_readers - the literal edge of its graph), or
  the job's own tooling (Q_PATHS, and the repository modules its Python
  imports).
* "Browser application" (`frontend`) checks, tests and builds web/. It runs
  when the change touches web/ - the package reads nothing outside it.
* "PeachQ Python tests" (`python`) runs every Python testpath in four shards.
  It runs for every change but one: a file under web/ that no Python file
  names. Everything else can reach a Python test - a .py file; a .q file,
  since the Python tests start q; a document, since tests/qrun runs the
  docs' q blocks; a CSV, a config or a workflow a gate's tests read. A web/
  file a Python file DOES name runs it: web/src/api/ is the browser's copy
  of the API models, and uqf_frontend's tests hold the two to each other.

  The commit hooks select by a record of what each test reads, lists and
  starts (scripts/dev/python_test_reach.py); this is that record's rule at the
  granularity of the group, without the record, which CI does not have. By
  the record every web/ file is reached only by tests that read the whole
  tree - a gate run over every file, which "Hooks and static gates" runs
  anyway, or a snapshot of the tree whose result turns on no file's content -
  and by tests starting a process the recorder cannot see into. Where a
  record exists, scripts/dev/tests/test_ci_scope.py checks the two agree.

EVERY GROUP RUNS, whatever else the change touches, for:

* the shared test configuration: this file and the workflow it decides for
  (OWN), a conftest.py, and what the python-tests hook already treats as
  shaping every test (scripts/dev/python_tests_hook.py's SHAPES_EVERY_TEST -
  pyproject.toml, uv.lock, ... - read from that file, so the two cannot
  disagree);
* an unknown path: one outside every area this file has a rule for (KNOWN).
  Whatever it is, nothing here has decided it is irrelevant.

A skipped group counts as passed for "Repository checks", the one required
check, only when this said to skip it: a skip with no decision behind it - this
failed, or the job did not run although this asked for it - fails that check.

A PULL REQUEST RUNS NONE OF THE TEST SUITES (#2167), in the default `lean`
mode: "Docs links", "PeachQ q tests" and "PeachQ Python tests" are left to
master and the nightly run (ON_MASTER), whatever the change reaches. Its hooks
already ran the reached tests at push, and its KDB-X attestation the q gates;
the suites cost minutes on every pull request and found little those had not.
What a pull request still runs: the author check, "Hooks and static gates"
and, for a web/ change, "Browser application". Label it `ci:full` to run
everything.

The mode is a switch (PR_TESTS_MODES), the repository variable CI_PR_TESTS,
which the Scope job passes as `--pr-tests`: unset or `lean` is the above, and
`full` brings back the behaviour before #2167 - each group a pull request's
change reaches runs, complete. Nothing else in the workflow changes with it;
tag `ci/full-pr-tests-baseline` is master's workflow from before.

FULL MODE runs every group, whatever changed:

* every push - CI runs on pushes to master alone, so master always runs
  everything - and release.yml's run (a called workflow sees its caller's
  event: a tag push, or a dispatch whose `full` input defaults true there);
* the nightly schedule, on master;
* workflow_dispatch with `full: true`;
* a pull request labelled `ci:full`.

Prints each group's decision (true/false) and why; `--output FILE` appends
`full` and each group as `name=value` lines (the workflow passes
$GITHUB_OUTPUT) and `--summary FILE` the why (its step summary).

    python3 scripts/dev/ci_scope.py --event pull_request --base SHA --head SHA --labels a,b
    python3 scripts/dev/ci_scope.py --event workflow_dispatch --full-input true
    python3 scripts/dev/ci_scope.py --base origin/master     # what this branch would run
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import re
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
    rf"^docs/|^src/.*\.q$|^scripts/generate/export_q_api_docs\.q$|^scripts/peachq/pin\.json$|{OWN}"
)
#: The Python the docs job runs: the render's pre-render step and the gate.
DOCS_ENTRIES = (
    "scripts/generate/q_api_docs.py",
    "scripts/gates/check_doc_links.py",
    "scripts/peachq/resolve.py",
)

#: The q job's inputs, beyond .q files, what q names, and the Python its tooling imports.
Q_PATHS = re.compile(
    rf"^scripts/portable/|^scripts/peachq/pin\.json$|^pyproject\.toml$|^uv\.lock$|{OWN}"
)
#: The Python the q job runs: pytest's root conftest, the suite's drivers (the
#: KDB-X one and PeachQ's), the PeachQ resolver.
Q_ENTRIES = (
    "conftest.py",
    "tests/qrun/test_q_suite.py",
    "scripts/peachq/full_suite.py",
    "scripts/peachq/resolve.py",
)

#: The browser application: its own npm package, reading nothing outside it.
FRONTEND = re.compile(r"^web/")

#: Where the python-tests hook keeps what shapes every test; read, not imported
#: (it imports pytest, which the Scope job does not install).
PYTHON_TESTS_HOOK = "scripts/dev/python_tests_hook.py"

#: Every area of the tree this file has a rule for. A path outside them all is
#: unknown, and runs every group. scripts/dev/tests/test_ci_scope.py fails
#: when a tracked file is unknown, so a new area is classified on purpose.
KNOWN = re.compile(
    r"^(\.claude|\.github|docs|lib|python|scripts|sidecars|src|tests|web)/"
    r"|^(README\.md|CHANGELOG\.md|CLAUDE\.md|CONTRIBUTING\.md|LICENSE|LICENSING\.md|install\.sh"
    r"|\.env\.example|\.gitattributes|\.gitignore|\.kodiak\.toml|\.panache\.toml"
    r"|\.pre-commit-config\.yaml"
    r"|\.qlinter-version|\.qlinter\.toml|conftest\.py|pyproject\.toml|uv\.lock)$"
)

#: The groups after the always-run hooks job, in report order, by job title.
GROUPS = {
    "docs": "Docs links",
    "q": "PeachQ q tests",
    "frontend": "Browser application",
    "python": "PeachQ Python tests",
}
#: The groups a pull request leaves to master and the nightly run (#2167).
ON_MASTER = ("docs", "q", "python")
#: The repository variable CI_PR_TESTS: what a pull request's test groups do.
#: An unset variable reaches here as "" and means the first, the default.
PR_TESTS_MODES = ("lean", "full")

#: What a skipped group's line says.
SKIPPED = {
    "docs": "nothing it renders or links changed",
    "q": "nothing a q process loads or reads changed",
    "frontend": "nothing under web/ changed",
    "python": "nothing outside web/ changed, and no web/ file a Python file names",
}


def _sibling(name: str):
    """scripts/dev/`name`.py, loaded by path (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parent / f"{name}.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return module


def _q_test_impact():
    return _sibling("q_test_impact")


_git = _sibling("_git")


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
    return _git.changed(f"{base}...{head}", cwd=root)


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


#: A path component naming the browser package: `"web/..."`, `/ "web" /`.
_WEB_WORD = re.compile(r"[\"'/]web[\"'/]")
#: This file and its tests name web/ paths as examples, not as reads.
_NOT_READERS = {
    "scripts/dev/ci_scope.py",
    "scripts/dev/tests/test_ci_scope.py",
}


def _names_directory(text: str, directory: Path) -> bool:
    """Does `text` name `directory` itself - `"web/src/api"` or
    `/ "web" / "src" / "api"`, a reader of every file in it - rather than a
    path below it? web/src/api/ is read whole by the API-type tests (#2168).
    Never `web` alone: a bare `"web"` is a word, not a read of the package."""
    if len(directory.parts) < 2:
        return False
    joined = " / ".join(f'"{part}"' for part in directory.parts)
    slashed = re.escape(directory.as_posix())
    return bool(
        re.search(re.escape(joined) + r"(?!\s*/)", text) or re.search(rf"[\"']{slashed}[\"']", text)
    )


def named_by_python(paths: Iterable[str], root: Path = REPO) -> dict[str, str]:
    """{path: a Python file naming it} for each web/ path a repository Python
    file names - by its whole path, or by its file name in a file that also
    names the web package (`parents[3] / "web" / "src" / "api.ts"`). Wider
    than a read on purpose: a false match runs the Python tests, a missed one
    would skip them. Vendored lib/ is not read."""
    files = [
        f
        for f in _q_test_impact().tracked(root)
        if f.endswith(".py") and not f.startswith("lib/") and f not in _NOT_READERS
    ]
    texts = {f: (root / f).read_text(errors="replace") for f in files}
    out = {}
    for path in paths:
        name = Path(path).name
        hit = next(
            (
                f
                for f, text in texts.items()
                if path in text
                or (name in text and _WEB_WORD.search(text))
                or _names_directory(text, Path(path).parent)
            ),
            None,
        )
        if hit:
            out[path] = hit
    return out


def shapes_every_test(root: Path = REPO) -> dict[str, str]:
    """The python-tests hook's SHAPES_EVERY_TEST: {path: why}, read from its
    source - the local rule and this one are the same dict."""
    tree = ast.parse((root / PYTHON_TESTS_HOOK).read_text())
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        else:
            continue
        if isinstance(target, ast.Name) and target.id == "SHAPES_EVERY_TEST":
            return ast.literal_eval(value)
    raise LookupError(f"{PYTHON_TESTS_HOOK} no longer defines SHAPES_EVERY_TEST")


def shared(path: str, root: Path = REPO) -> str | None:
    """Why `path` is shared test configuration, or None."""
    if re.search(OWN, path):
        return "the CI workflow, or the decision it obeys"
    if Path(path).name == "conftest.py":
        return "a conftest, which shapes the tests under it"
    every = shapes_every_test(root)
    if path in every:
        return f"shapes every test: {every[path]}"
    return None


@dataclass
class Scope:
    full: bool
    docs: bool
    q: bool
    frontend: bool = True
    python: bool = True
    why: dict[str, list[str]] = field(default_factory=dict)

    def outputs(self) -> dict[str, str]:
        return {k: str(getattr(self, k)).lower() for k in ("full", *GROUPS)}


def full_reason(event: str, labels: Iterable[str], full_input: bool) -> str | None:
    """Why this run is a full one, or None when it is filtered."""
    if event == "pull_request":
        return f"the pull request is labelled {FULL_LABEL}" if FULL_LABEL in labels else None
    if event == "workflow_dispatch":
        return "workflow_dispatch with full: true" if full_input else None
    # A push (to master; release.yml calls this on a tag push too), the
    # nightly schedule, or anything else: everything.
    return f"a {event} - every push and the nightly run run every job"


def _python_why(path: str) -> str:
    """Why a path outside web/ runs the Python tests."""
    if path.endswith(".py"):
        return f"{path}: Python"
    if path.endswith(".q") or path.startswith("lib/"):
        return f"{path}: q, which the Python tests start"
    if path.startswith("docs/"):
        return f"{path}: a document, whose q blocks tests/qrun runs"
    return f"{path}: outside web/, where a Python test or a gate's tests can read it"


def decide(
    changes: list[tuple[str, str]],
    full: str | None,
    root: Path = REPO,
) -> Scope:
    """The groups `changes` need; every one under `full`."""
    if full:
        return Scope(True, True, True, True, True, {g: [full] for g in GROUPS})
    docs_py = imported(DOCS_ENTRIES, root)
    q_py = imported(Q_ENTRIES, root)
    paths = [p for _, p in changes]
    q_reads = _q_test_impact().q_readers([p for p in paths if not p.endswith(".py")], root)
    gone = named_in_docs([p for s, p in changes if s == "D"], root)
    web_named = named_by_python([p for p in paths if FRONTEND.search(p)], root)
    why: dict[str, list[str]] = {g: [] for g in GROUPS}
    for path in paths:
        every = shared(path, root) or (None if KNOWN.search(path) else "an unknown path")
        if every:
            for group in GROUPS:
                why[group].append(f"{path}: {every} - every group runs")
            continue
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
        if FRONTEND.search(path):
            why["frontend"].append(f"{path}: the browser application")
            if path in web_named:
                why["python"].append(f"{path}: named by {web_named[path]}")
        else:
            why["python"].append(_python_why(path))
    return Scope(False, *(bool(why[g]) for g in GROUPS), why=why)


def for_pull_request(scope: Scope) -> Scope:
    """`scope` as a pull request without `ci:full` runs it: no ON_MASTER group.
    What the change reaches is still said, as what master will run."""
    for group in ON_MASTER:
        reached = scope.why.get(group, [])
        setattr(scope, group, False)
        scope.why[group] = [
            f"a pull request leaves it to master and the nightly run (label {FULL_LABEL} "
            f"to run it here, or set CI_PR_TESTS=full for every pull request); "
            f"the change reaches it through {len(reached)} path(s)"
        ]
    return scope


def report(scope: Scope, changes: int) -> str:
    lines = [f"### CI scope ({'full' if scope.full else f'{changes} changed path(s)'})", ""]
    lines.append("- **Hooks and static gates**: runs - every hook, every file, every run")
    for group, title in GROUPS.items():
        ran = getattr(scope, group)
        reasons = scope.why.get(group, [])
        lines.append(f"- **{title}**: {'runs' if ran else f'skipped - {SKIPPED[group]}'}")
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
    ap.add_argument(
        "--pr-tests",
        default="",
        help=f"the CI_PR_TESTS variable: {' or '.join(PR_TESTS_MODES)} (unset: lean)",
    )
    args = ap.parse_args(argv)
    pr_tests = args.pr_tests.strip().lower() or PR_TESTS_MODES[0]
    if pr_tests not in PR_TESTS_MODES:
        ap.error(f"CI_PR_TESTS is {args.pr_tests!r}: want {' or '.join(PR_TESTS_MODES)}, or unset")

    labels = {lab.strip() for lab in args.labels.split(",") if lab.strip()}
    full = full_reason(args.event, labels, args.full_input.lower() == "true")
    changes = [] if full else changed(args.base, args.head)
    scope = decide(changes, full)
    if args.event == "pull_request" and not full and pr_tests == "lean":
        scope = for_pull_request(scope)
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
