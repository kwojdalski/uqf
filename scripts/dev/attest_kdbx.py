#!/usr/bin/env python3
"""Run the KDB-X gates this branch needs and record that they passed (#1014).

Hosted CI cannot run KDB-X, so whether a pull request passed the KDB-X hooks
(HOOKS below, by their ids in .pre-commit-config.yaml so their definitions
stay the one source) is known only on the machine that ran them.

This runs the hooks the branch NEEDS: those whose `files:` scope (less
`exclude:`), read from the config by scripts/gates/check_kdbx_attestation.py,
takes a path changed in `origin/master...HEAD` - the three-dot diff, from the
merge base. Each one runs over the WHOLE tree, not just the files a commit
staged, and when all pass the tree is recorded as an empty commit carrying

    KDB-X-gates: <the tree's sha> hooks=<the hooks run, comma-sorted>

which the check reads in CI and holds to the same needed set. `--all` runs
every hook, as this did before it was scoped. A branch that needs none
records nothing, and says so.

Why skipping a hook the change does not touch still means "every KDB-X hook
passed on the judged paths that merge": the check accepts a head only if it
contains the base's tip or the base changed no judged path since the merge
base (#1687). Either way the merge base's judged files are master's tip's,
and a hook whose scope the diff does not touch reads on the head exactly the
files it read on master's tip. That tip's result for the hook is the one the
last PR to touch its scope attested on its own exact tree, and every merge
since left those files as they were. It rests on the same assumption the
check always made for paths outside every scope: a hook's result depends only
on the files its scope takes - which is why a scope must be complete, and why
q-docs' scope is held to the documents with q-example blocks by
scripts/gates/check_hook_scopes.py.

Run it last, after the final rebase or merge of master: either changes the
tree and the attestation with it. A branch need not merge master in just to
be current - only when master has changed a judged path since the branch
forked, which is when the check would refuse it.

Refuses a dirty tree, untracked files included since the hooks see them (what
is tested must be what is committed), and any interpreter scripts/test.py does
not identify as KDB-X.

    uv run python scripts/dev/attest_kdbx.py [--all] [--base REF]
"""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
#: Where this script's sibling modules are loaded from - REPO's value at
#: import, kept apart so a test that points REPO at a scratch repository
#: still loads the real check.
_SOURCE = REPO

#: The hooks that need KDB-X, which hosted CI skips.
HOOKS = ("q-tests", "q-coverage", "q-docs", "contract-surface", "q-facts")

#: The pre-commit CI runs the hooks with.
PRE_COMMIT = "pre-commit==4.6.2"

TRAILER = "KDB-X-gates"

#: What the branch is compared with, as CI compares a PR with its base.
BASE = "origin/master"


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, _SOURCE / rel)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _test_py():
    return _load("uqf_test_lanes", "scripts/test.py")


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout


def needed(base: str) -> list[str]:
    """The hooks whose scope takes a path changed in `base...HEAD`, sorted -
    worked out by the check CI runs, so the two cannot disagree."""
    check = _load("check_kdbx_attestation", "scripts/gates/check_kdbx_attestation.py")
    changed = git("diff", "--name-only", f"{base}...HEAD").split()
    return check.needed_hooks(changed)


def run_hook(hook: str) -> bool:
    """Run one pre-commit hook over the whole tree; whether it passed."""
    cmd = ["uv", "tool", "run", "--from", PRE_COMMIT, "pre-commit", "run", hook, "--all-files"]
    return subprocess.run(cmd, cwd=REPO, check=False).returncode == 0


def dirt() -> str | None:
    """What makes the working tree differ from HEAD's tree, or None.

    Untracked files count. The hooks run q over the WORKING TREE, so a new
    `src/foo.q` that a tracked file loads but nobody `git add`ed passes them
    here and is absent from the tree the trailer names - which then fails
    wherever it is checked out (#1340). Ignored files do not count:
    `git status --porcelain` leaves them out, so build output and the venv
    are no obstacle.
    """
    lines = git("status", "--porcelain").splitlines()
    untracked = [line[3:] for line in lines if line.startswith("??")]
    if len(untracked) < len(lines):
        return "uncommitted changes - commit them, so what is tested is what is pushed"
    if untracked:
        shown = ", ".join(untracked[:5]) + (
            f" and {len(untracked) - 5} more" if len(untracked) > 5 else ""
        )
        return (
            f"untracked files ({shown}) - add or remove them, so what is tested is "
            "what is pushed: the hooks see them, the attested tree would not"
        )
    return None


def refusal() -> str | None:
    """Why this tree cannot be attested here, or None."""
    why = dirt()
    if why:
        return why
    impl = _test_py().check_interpreter()
    if impl is None:
        return "no q interpreter ($QCMD, else q on PATH)"
    if impl != "kdbx":
        return f"the interpreter is {impl}, not KDB-X - only a KDB-X run attests"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--all", action="store_true", help="run every KDB-X hook")
    parser.add_argument("--base", default=BASE, help=f"compare with this (default {BASE})")
    args = parser.parse_args(argv)

    why = dirt()
    if why:
        print(f"attest_kdbx: refused: {why}", file=sys.stderr)
        return 1
    hooks = sorted(HOOKS) if args.all else needed(args.base)
    if not hooks:
        print(
            f"attest_kdbx: nothing in {args.base}...HEAD is in a KDB-X hook's scope - "
            "no attestation needed, nothing recorded"
        )
        return 0
    why = refusal()
    if why:
        print(f"attest_kdbx: refused: {why}", file=sys.stderr)
        return 1
    tree = git("rev-parse", "HEAD^{tree}").strip()
    print(f"attest_kdbx: needed: {', '.join(hooks)}", flush=True)
    for hook in hooks:
        print(f"attest_kdbx: {hook} ...", flush=True)
        if not run_hook(hook):
            print(f"attest_kdbx: {hook} failed - nothing recorded", file=sys.stderr)
            return 1
    if git("rev-parse", "HEAD^{tree}").strip() != tree or dirt():
        print(
            "attest_kdbx: a hook changed the tree - commit that, then run this again",
            file=sys.stderr,
        )
        return 1
    why_these = "every KDB-X hook (--all)" if args.all else f"the hooks {args.base}...HEAD needs"
    trailer = f"{TRAILER}: {tree} hooks={','.join(hooks)}"
    try:
        git(
            "commit",
            "--allow-empty",
            "-m",
            f"KDB-X gates pass on tree {tree[:12]}",
            "-m",
            f"{', '.join(hooks)} - {why_these} - run on KDB-X over the whole tree.\n\n{trailer}",
        )
    except subprocess.CalledProcessError as exc:
        # The commit runs the repository's hooks too; one that refuses (a
        # qlinter off its pin, say) is named here rather than as a traceback.
        print(
            f"attest_kdbx: the gates passed but the commit was refused:\n{exc.stdout}{exc.stderr}",
            file=sys.stderr,
        )
        return 1
    print(f"attest_kdbx: recorded {trailer} - push to show it on the PR")
    return 0


if __name__ == "__main__":
    sys.exit(main())
