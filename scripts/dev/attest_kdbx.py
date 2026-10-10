#!/usr/bin/env python3
"""Run the KDB-X gates on this branch's tree and record that they passed (#1014).

Hosted CI cannot run KDB-X, so whether a pull request passed the q-tests,
q-coverage, contract-surface and q-facts hooks is known only on the machine
that ran them. This runs those four hooks - by their ids in
.pre-commit-config.yaml, so their definitions stay the one source - over the
WHOLE tree, not just the files a commit staged, and when all pass records
the tree as an empty commit carrying

    KDB-X-gates: <the tree's sha>

which scripts/gates/check_kdbx_attestation.py reads in CI. Run it last, after
the final rebase: a rebase changes the tree and the attestation with it.

Refuses a dirty tree (what is tested must be what is committed) and any
interpreter scripts/test.py does not identify as KDB-X.

    uv run python scripts/dev/attest_kdbx.py
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

#: The hooks that need KDB-X, which hosted CI skips.
HOOKS = ("q-tests", "q-coverage", "contract-surface", "q-facts")

#: The pre-commit CI runs the hooks with.
PRE_COMMIT = "pre-commit==4.6.2"

TRAILER = "KDB-X-gates"


def _test_py():
    spec = importlib.util.spec_from_file_location("uqf_test_lanes", REPO / "scripts" / "test.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout


def run_hook(hook: str) -> bool:
    """Run one pre-commit hook over the whole tree; whether it passed."""
    cmd = ["uv", "tool", "run", "--from", PRE_COMMIT, "pre-commit", "run", hook, "--all-files"]
    return subprocess.run(cmd, cwd=REPO, check=False).returncode == 0


def refusal() -> str | None:
    """Why this tree cannot be attested here, or None."""
    if git("status", "--porcelain", "--untracked-files=no").strip():
        return "uncommitted changes - commit them, so what is tested is what is pushed"
    impl = _test_py().check_interpreter()
    if impl is None:
        return "no q interpreter ($QCMD, else q on PATH)"
    if impl != "kdbx":
        return f"the interpreter is {impl}, not KDB-X - only a KDB-X run attests"
    return None


def main() -> int:
    why = refusal()
    if why:
        print(f"attest_kdbx: refused: {why}", file=sys.stderr)
        return 1
    tree = git("rev-parse", "HEAD^{tree}").strip()
    for hook in HOOKS:
        print(f"attest_kdbx: {hook} ...", flush=True)
        if not run_hook(hook):
            print(f"attest_kdbx: {hook} failed - nothing recorded", file=sys.stderr)
            return 1
    if (
        git("rev-parse", "HEAD^{tree}").strip() != tree
        or git("status", "--porcelain", "--untracked-files=no").strip()
    ):
        print(
            "attest_kdbx: a hook changed the tree - commit that, then run this again",
            file=sys.stderr,
        )
        return 1
    try:
        git(
            "commit",
            "--allow-empty",
            "-m",
            f"KDB-X gates pass on tree {tree[:12]}",
            "-m",
            f"{', '.join(HOOKS)}, run on KDB-X over the whole tree.\n\n{TRAILER}: {tree}",
        )
    except subprocess.CalledProcessError as exc:
        # The commit runs the repository's hooks too; one that refuses (a
        # qlinter off its pin, say) is named here rather than as a traceback.
        print(
            f"attest_kdbx: the gates passed but the commit was refused:\n{exc.stdout}{exc.stderr}",
            file=sys.stderr,
        )
        return 1
    print(f"attest_kdbx: recorded {TRAILER}: {tree} - push to show it on the PR")
    return 0


if __name__ == "__main__":
    sys.exit(main())
