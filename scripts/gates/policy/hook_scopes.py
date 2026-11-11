"""Fail if a pre-commit scope is dead, or if tracked Python is ungated.

Two distinct failures, and only the first is obvious:

1. **A dead scope** - a hook scoped to a path that no longer exists matches
   nothing, exits zero, and reports green. Worse than no hook, because the
   commit looks gated when it is not.
2. **Ungated code** - a scope that is still *valid* but too *narrow*. Python
   added outside it is simply never linted, and nothing anywhere complains.

The second is what actually happened here - twice, because the first version
of this script checked only the LINT hooks. While it reported 43/43 lint
coverage and exited zero, 229 of the repository's 231 Python tests were not
run by any hook, and no CI existed to catch them either. A gate-checker blind
to half the gates is its own version of the bug it exists to prevent, so it
now checks both.

A hook scoped with ``files:`` to a path that no longer exists does not fail -
it matches nothing, exits zero, and reports green. That is strictly worse
than having no hook: the commit looks gated when it is not.

The ruff hooks were scoped ``^python/uqf-client/`` while Python was being
added under ``python/uqs/`` and ``python/uqf_frontend/``. That
scope was never dead - the directory still exists - so a dead-scope check
alone would have passed it, which is why the coverage check exists too. Four
consecutive pull requests of Python went through a lint gate pointed at a
different directory. One bug that slipped past - a module shadowed by a
same-named endpoint function - was caught by ruff within seconds of it
finally being pointed at the code.

Run standalone to audit, or as a pre-commit hook to enforce:

    python3 scripts/gates/check.py hook-scopes

`--kdbx-only` prints, instead, the ids of the hooks tagged `# kdbx-only`,
comma-joined, for ci.yml's no-q SKIP list.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

#: This checkout: scripts/gates/policy/<this file>. Its doc_examples.py is the
#: one that reads the documents.
REPO = Path(__file__).resolve().parents[3]

#: The hook config, under the root checked.
CONFIG = Path(".pre-commit-config.yaml")

#: Hooks whose scope is deliberately allowed to match nothing, with a reason.
#: Keep this empty unless there is a real one - the point of the check is to
#: refuse dead scopes, not to accumulate exemptions.
ALLOWED_EMPTY: dict[str, str] = {}

#: Hook ids that constitute the Python lint gate. Every tracked .py file must
#: be matched by at least one of them.
LINT_HOOKS = ("ruff", "ruff-format")

#: Hook ids that constitute the Python test gate. Same requirement: tracked
#: Python outside these is never exercised on commit.
TEST_HOOKS = ("python-tests",)

#: Hook ids that constitute the Python TYPE gate. Same requirement again.
#:
#: Checked here rather than trusted because the lint gate already drifted
#: once, from a directory scope that stayed valid while the code moved out
#: from under it - 43 of 47 files ungated, and nothing complained. A type
#: gate can drift exactly the same way, and the symptom is identical:
#: everything passes because almost nothing is checked.
TYPE_HOOKS = ("ty",)

#: Hook ids that run the q blocks in docs/. Each one's scope must take
#: exactly the documents that carry a `q-example:` marker among the files
#: scripts/dev/doc_examples.py reads - a narrower scope would leave a marked
#: block unrun on a docs edit, a wider one asks a KDB-X attestation of a docs
#: edit that cannot change what runs (scripts/dev/kdbx_attestation.py).
DOC_HOOKS = ("q-docs", "q-docs-peachq")

#: Path prefixes exempt from needing lint coverage, each with its reason.
#: Vendored trees only: this repository's standing rule is never to edit a
#: vendored tree, so linting one would produce findings nobody may fix.
LINT_EXEMPT: dict[str, str] = {
    "lib/": "vendored third-party trees are never edited here, so linting them is noise",
}


def tracked_files(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"],
        capture_output=True,
        text=True,
        check=True,
        cwd=root,
    )
    return out.stdout.splitlines()


def _doc_examples():
    import importlib.util

    path = REPO / "scripts" / "dev" / "doc_examples.py"
    spec = importlib.util.spec_from_file_location("doc_examples", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Registered first: its dataclass resolves annotations through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def doc_files_and_marked(root: Path) -> tuple[set[str], set[str]]:
    """(every document doc_examples.py reads under `root`, the ones with a
    q-example marker)."""
    docex = _doc_examples()
    docs = {f.relative_to(root).as_posix() for f in docex._doc_files(root)}
    return docs, set(docex.marked_docs(root))


def doc_scope_drift(
    hooks: list[tuple[str, str, str | None]], docs: set[str], marked: set[str]
) -> list[str]:
    """What is wrong with each DOC_HOOKS scope, one line per problem.

    A hook is judged only when the config has it; a q-docs hook dropped from
    the config is `kdbx_attestation.py check`'s to refuse, not this.
    """
    problems: list[str] = []
    for hook_id, pattern, exclude in hooks:
        if hook_id not in DOC_HOOKS:
            continue
        files, excl = re.compile(pattern), re.compile(exclude) if exclude else None
        taken = {d for d in docs if files.search(d) and not (excl and excl.search(d))}
        for doc in sorted(marked - taken):
            problems.append(f"{hook_id}: {doc} has a q-example block its scope does not take")
        for doc in sorted(taken - marked):
            problems.append(f"{hook_id}: its scope takes {doc}, which has no q-example block")
    return problems


#: The comment that marks a KDB-X-only hook: on the line right under its
#: `- id:`. The one place the set is written (#2170): kdbx_attestation.HOOKS, CI's
#: no-q SKIP list and their tests all read it from here.
KDBX_ONLY_TAG = "# kdbx-only"


def kdbx_only_hooks(config_text: str) -> list[str]:
    """The ids of the hooks tagged `# kdbx-only`, in file order - the hooks
    that need a q and so run on a contributor's machine, not on hosted CI."""
    ids: list[str] = []
    current: str | None = None
    for line in config_text.splitlines():
        id_match = re.match(r"\s*-\s*id:\s*(\S+)", line)
        if id_match:
            current = id_match.group(1)
        elif current and line.strip() == KDBX_ONLY_TAG:
            ids.append(current)
            current = None
    return ids


def scoped_hooks(config_text: str) -> list[tuple[str, str, str | None]]:
    """Every (hook id, files pattern, exclude pattern) triple, in file order.

    Parsed with a regex rather than a YAML library so this script has no
    dependencies and can run as a plain system hook. The config's shape is
    stable and this only needs three fields.

    `exclude` is read as well as `files`, and both halves of the check apply
    it. The per-hook diagnostic once counted the 5 vendored files under `lib/`
    that every hook excludes, reporting "54 file(s)" for a gate that enforced
    on 49. The coverage half ignored it for longer, and that was not cosmetic:
    an exclude widened to `^(lib|python)/` took nearly all Python out of the
    lint, test and type gates while coverage still read 396/396 (#1335).

    A hook's keys are collected until the next `- id:`, in whatever order
    they are written, and a column-0 `files:`/`exclude:` is pre-commit's
    global filter, applied to every hook as well as its own: an exclude
    written before its `files:`, or one written once at the top, took Python
    out of the gates while coverage still read full (#2539).
    """
    keys: list[tuple[str, dict[str, str]]] = []
    top: dict[str, str] = {}
    for line in config_text.splitlines():
        id_match = re.match(r"\s*-\s*id:\s*(\S+)", line)
        if id_match:
            keys.append((id_match.group(1), {}))
            continue
        key_match = re.match(r"""(\s*)(files|exclude):\s*['"]?(.+?)['"]?\s*$""", line)
        if key_match is None:
            continue
        indent, key, value = key_match.groups()
        if not indent:
            top[key] = value
        elif keys:
            keys[-1][1][key] = value
    hooks: list[tuple[str, str, str | None]] = []
    for hook_id, own in keys:
        if "files" not in own:
            continue
        files = own["files"]
        if "files" in top:
            # both must search-match: the global one as a lookahead from the start
            files = f"^(?=.*?(?:{top['files']})).*?(?:{files})"
        excludes = [x for x in (top.get("exclude"), own.get("exclude")) if x]
        if len(excludes) > 1:
            excludes = ["|".join(f"(?:{x})" for x in excludes)]
        hooks.append((hook_id, files, next(iter(excludes), None)))
    return hooks


def run(root: Path, kdbx_only: bool = False) -> int:
    """`root`'s hook scopes against its tracked files; the exit status. With
    `kdbx_only`, print the `# kdbx-only` hook ids instead. The findings are a
    per-hook table and per-gate counts, so the rule reports them itself."""
    config = root / CONFIG
    if not config.is_file():
        print(f"no pre-commit config at {config}", file=sys.stderr)
        return 1
    if kdbx_only:
        # comma-joined, for ci.yml's SKIP: `SKIP=$SKIP,$(... --kdbx-only)`
        print(",".join(kdbx_only_hooks(config.read_text())))
        return 0

    files = tracked_files(root)
    hooks = scoped_hooks(config.read_text())
    if not hooks:
        # A regex that matches nothing would make this check pass vacuously -
        # the exact failure mode it exists to prevent.
        print(
            "hook-scopes: parsed no scoped hooks, which cannot be right",
            file=sys.stderr,
        )
        return 1

    dead: list[tuple[str, str]] = []
    print(f"hook-scopes: {len(hooks)} path-scoped hook(s) in {CONFIG}")
    for hook_id, pattern, exclude in hooks:
        try:
            matcher = re.compile(pattern)
        except re.error as exc:
            print(f"  {hook_id:28} INVALID REGEX {pattern!r}: {exc}", file=sys.stderr)
            dead.append((hook_id, pattern))
            continue
        try:
            excluder = re.compile(exclude) if exclude else None
        except re.error as exc:
            print(f"  {hook_id:28} INVALID EXCLUDE {exclude!r}: {exc}", file=sys.stderr)
            dead.append((hook_id, pattern))
            continue
        matched = [f for f in files if matcher.search(f)]
        if excluder is not None:
            matched = [f for f in matched if not excluder.search(f)]
        count = len(matched)
        status = "ok" if count else "MATCHES NOTHING"
        shown = pattern if not exclude else f"{pattern} less {exclude}"
        print(f"  {hook_id:28} {shown:34} {count:>5} file(s)  {status}")
        if not count and hook_id not in ALLOWED_EMPTY:
            dead.append((hook_id, pattern))

    # --- coverage: is any tracked Python outside either gate? -----------
    py_files = [
        f
        for f in files
        if f.endswith(".py")
        and "/.venv/" not in f
        and not any(f.startswith(prefix) for prefix in LINT_EXEMPT)
    ]

    ungated: list[str] = []
    print()
    for gate_name, gate_hooks in (
        ("lint", LINT_HOOKS),
        ("test", TEST_HOOKS),
        ("type", TYPE_HOOKS),
    ):
        # A hook reaches a file its `files:` matches AND its `exclude:` does
        # not. Reading `files:` alone reported 396/396 covered while an
        # exclude of `^(lib|python)/` had taken 356 of them out of every gate
        # (#1335) - a narrowed scope, the very thing this half exists to see.
        scopes = [
            (re.compile(p), re.compile(x) if x else None)
            for hook_id, p, x in hooks
            if hook_id in gate_hooks
        ]
        if not scopes:
            print(
                f"  NO {gate_name.upper()} GATE  none of {gate_hooks} is configured",
                file=sys.stderr,
            )
            ungated.extend(py_files)
            continue
        missed = [
            f
            for f in py_files
            if not any(m.search(f) and not (x and x.search(f)) for m, x in scopes)
        ]
        print(
            f"hook-scopes: {len(py_files) - len(missed)}/{len(py_files)} "
            f"tracked .py file(s) covered by the {gate_name} gate {gate_hooks}"
        )
        for f in missed:
            if f not in ungated:
                ungated.append(f)
        if missed:
            by_dir: dict[str, int] = {}
            for f in missed:
                top = "/".join(f.split("/")[:2])
                by_dir[top] = by_dir.get(top, 0) + 1
            for directory, count in sorted(by_dir.items()):
                print(
                    f"  UNGATED ({gate_name})  {directory:30} {count:>5} file(s)",
                    file=sys.stderr,
                )

    drift: list[str] = []
    if any(hook_id in DOC_HOOKS for hook_id, _, _ in hooks):
        drift = doc_scope_drift(hooks, *doc_files_and_marked(root))
        for problem in drift:
            print(f"  DOC SCOPE  {problem}", file=sys.stderr)

    if dead:
        print(file=sys.stderr)
        print(
            "These hooks are scoped to paths that match no tracked file:",
            file=sys.stderr,
        )
        for hook_id, pattern in dead:
            print(f"  - {hook_id}: {pattern}", file=sys.stderr)
        print(
            "\nA hook that matches nothing exits zero and reports green, so the commit "
            "looks gated when it is not. Fix the scope, or add the hook to ALLOWED_EMPTY "
            "with a reason.",
            file=sys.stderr,
        )
        return 1

    if drift:
        print(file=sys.stderr)
        print(
            f"The scopes of {DOC_HOOKS} must name exactly the documents with a "
            "`<!-- q-example: ... -->` block (scripts/dev/doc_examples.py). Edit the "
            "`docs/(...)\\.md$` alternation in .pre-commit-config.yaml to match.",
            file=sys.stderr,
        )
        return 1

    if ungated:
        print(file=sys.stderr)
        print(
            f"{len(ungated)} tracked Python file(s) fall outside the lint gate "
            f"{LINT_HOOKS}, the test gate {TEST_HOOKS} or the type gate "
            f"{TYPE_HOOKS}, so they are not fully "
            f"checked on commit. Widen the relevant scopes, or add a path to "
            f"LINT_EXEMPT with a reason.",
            file=sys.stderr,
        )
        return 1

    return 0
