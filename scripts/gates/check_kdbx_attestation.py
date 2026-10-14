#!/usr/bin/env python3
"""Report whether a pull request's tree has passed the KDB-X gates (#1014).

Hosted CI has no KDB-X licence, so the q-tests, q-coverage, contract-surface
and q-facts hooks run only on a contributor's machine - and master needed a
repair commit for them on almost every day of a fortnight: a change merged
on a green CI that no KDB-X run had seen, and the next KDB-X session paid
for it inside an unrelated diff.

This binds a merge to a KDB-X run without needing q here. A KDB-X session
attests a tree with scripts/dev/attest_kdbx.py, which runs those hooks and
records the result as an empty commit carrying the trailer

    KDB-X-gates: <the tree's sha>

and this check, given a PR's base and head, says whether the head's exact
tree carries one. A rebase changes the tree and needs a fresh attestation -
which is the case that broke master. A head that does not contain the base's
tip is reported too: the tree that merges is then one no run has seen.

It is ADVISORY: CI reports the answer on the PR and never fails on it.

    python3 scripts/gates/check_kdbx_attestation.py BASE HEAD
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

TRAILER = "KDB-X-gates"


def _load(rel: str):
    spec = importlib.util.spec_from_file_location(Path(rel).stem, REPO / rel)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def judged_scopes(config_text: str | None = None) -> list[tuple[re.Pattern, re.Pattern | None]]:
    """The (files, exclude) scope of each hook attest_kdbx.py runs.

    Read from .pre-commit-config.yaml, the way check_hook_scopes.py reads it,
    for the hooks attest_kdbx.py names - not kept here as a second list. A
    hand-kept copy had already drifted narrower than the hooks: q-tests runs
    the ```q blocks in docs/ (`.md`), contract-surface reads Python and
    scripts/processes/, and a change to only those was reported as needing no
    attestation (#1334). A hook with no `files:` would match every path, and
    one that is not in the config at all is a rename this must not paper
    over, so both stop here rather than guess.
    """
    hooks = _load("scripts/dev/attest_kdbx.py").HOOKS
    scopes_mod = _load("scripts/gates/check_hook_scopes.py")
    if config_text is None:
        config_text = scopes_mod.CONFIG.read_text()
    scoped = {
        hook: (files, exclude) for hook, files, exclude in scopes_mod.scoped_hooks(config_text)
    }
    missing = [hook for hook in hooks if hook not in scoped]
    if missing:
        raise SystemExit(
            f"check_kdbx_attestation: no `files:` scope for {', '.join(missing)} in "
            ".pre-commit-config.yaml - cannot tell which paths the KDB-X gates judge"
        )
    return [
        (re.compile(files), re.compile(exclude) if exclude else None)
        for files, exclude in (scoped[hook] for hook in hooks)
    ]


def is_judged(path: str, scopes: list[tuple[re.Pattern, re.Pattern | None]]) -> bool:
    """Whether any KDB-X hook's scope - `files:` less `exclude:` - takes `path`."""
    return any(files.search(path) and not (ex and ex.search(path)) for files, ex in scopes)


ATTEST = "uv run python scripts/dev/attest_kdbx.py"


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def judged_paths(base: str, head: str) -> list[str]:
    changed = git("diff", "--name-only", f"{base}...{head}").split()
    scopes = judged_scopes()
    return [p for p in changed if is_judged(p, scopes)]


def attested(head: str) -> bool:
    """Whether a commit with head's tree - head itself, or the empty commits
    an attestation adds on top of the same tree - carries its trailer."""
    tree = git("rev-parse", f"{head}^{{tree}}").strip()
    want = f"{TRAILER}: {tree}"
    log = git("log", "--format=%T%x00%B%x01", head)
    for entry in log.split("\x01"):
        entry = entry.strip("\n")
        if not entry:
            continue
        commit_tree, _, body = entry.partition("\x00")
        if commit_tree != tree:
            return False
        if any(line.strip() == want for line in body.splitlines()):
            return True
    return False


def contains(head: str, base: str) -> bool:
    return (
        subprocess.run(["git", "merge-base", "--is-ancestor", base, head], check=False).returncode
        == 0
    )


def report(base: str, head: str) -> tuple[bool, str]:
    """(attested, what to tell the PR)."""
    judged = judged_paths(base, head)
    if not judged:
        return True, "no path the KDB-X gates judge changed - no attestation needed"
    shown = ", ".join(judged[:5]) + (f" and {len(judged) - 5} more" if len(judged) > 5 else "")
    if not attested(head):
        return False, (
            f"this tree has no KDB-X attestation, and it changes {shown}. "
            f"On a machine with KDB-X, with the branch clean: `{ATTEST}`, then push."
        )
    if not contains(head, base):
        return False, (
            "this tree is attested, but it does not contain the base's tip, so the "
            f"tree that merges is not the one attested. Rebase, then `{ATTEST}` again."
        )
    return True, "the KDB-X gates passed on exactly this tree"


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        return 2
    ok, message = report(argv[1], argv[2])
    print(f"check_kdbx_attestation: {message}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
