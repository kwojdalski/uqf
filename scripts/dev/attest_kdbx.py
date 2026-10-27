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

    KDB-X-gates: <the tree's sha> hooks=<the hooks run, comma-sorted> q-tests=<what ran>

which the check reads in CI and holds to the same needed set. `--all` runs
every hook, and the whole q suite, as this did before it was scoped. A branch
that needs none records nothing, and says so.

q-tests runs only the q suites the change reaches (#1690): the selection
scripts/dev/test_impact.py makes for `base...HEAD` from q-lint's dependency
graph, handed to the hook in UQF_Q_TESTS_SELECTION so the hook runs exactly
what is recorded. `q-tests=<sha256>` records that subset by its digest
(test_impact.digest: the sorted file names), and CI's check, with the qlinter
.qlinter-graph-rev pins, makes the same selection from the PR's diff and
accepts the trailer only when the digests agree. `q-tests=all` records the
whole suite, which needs no recomputation: what ran when the selector said
ALL, could not run here (no graph-capable qlinter), or UQF_Q_TESTS_FULL=1 was
set - the way to an attestation CI accepts whatever its selector says. The
other hooks are unchanged; q-coverage still runs the whole suite. The full
suite still runs on master, and a test only a full run would have caught is
caught there.

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
import os
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
    # registered before it runs: a @dataclass looks its own module up there
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _test_py():
    return _load("uqf_test_lanes", "scripts/test.py")


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout


def _test_impact():
    return _load("test_impact", "scripts/dev/test_impact.py")


def q_tests_plan(base: str, full: bool):
    """What q-tests runs: the whole suite with `full`, else test_impact's plan
    for `base...HEAD` - which falls back to the whole suite itself."""
    impact = _test_impact()
    if full:
        return impact.Plan(None, "--all")
    return impact.plan(base, REPO, with_uncommitted=False)


def needed(base: str) -> list[str]:
    """The hooks whose scope takes a path changed in `base...HEAD`, sorted -
    worked out by the check CI runs, so the two cannot disagree."""
    check = _load("check_kdbx_attestation", "scripts/gates/check_kdbx_attestation.py")
    changed = git("diff", "--name-only", f"{base}...HEAD").split()
    return check.needed_hooks(changed)


def run_hook(hook: str, env: dict[str, str] | None = None) -> bool:
    """Run one pre-commit hook over the whole tree, with `env` added to this
    process's environment; whether it passed."""
    cmd = ["uv", "tool", "run", "--from", PRE_COMMIT, "pre-commit", "run", hook, "--all-files"]
    merged = {**os.environ, **(env or {})}
    return subprocess.run(cmd, cwd=REPO, env=merged, check=False).returncode == 0


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
    plan = None
    hook_env: dict[str, str] = {}
    if "q-tests" in hooks:
        plan = q_tests_plan(args.base, args.all)
        impact = _test_impact()
        hook_env = {impact.DECIDED_ENV: plan.line}
        print(f"attest_kdbx: q-tests: {plan.why}", flush=True)
    for hook in hooks:
        print(f"attest_kdbx: {hook} ...", flush=True)
        if not run_hook(hook, hook_env if hook == "q-tests" else None):
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
    ran = ""
    if plan is not None:
        trailer += f" q-tests={plan.record}"
        ran = f"\n\nq-tests: {plan.why}" + (
            f"\nq-tests ran: {', '.join(plan.files) or 'none'}" if plan.files is not None else ""
        )
    try:
        git(
            "commit",
            "--allow-empty",
            "-m",
            f"KDB-X gates pass on tree {tree[:12]}",
            "-m",
            f"{', '.join(hooks)} - {why_these} - run on KDB-X over the whole tree."
            f"{ran}\n\n{trailer}",
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
