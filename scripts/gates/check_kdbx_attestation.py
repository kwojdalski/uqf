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

import re
import subprocess
import sys

TRAILER = "KDB-X-gates"

#: The paths the KDB-X gates judge that no other CI lane does: q source and
#: tests, the generators and what they generate. A change to none of them
#: needs no attestation.
JUDGED = re.compile(
    r"\.q$"
    r"|^tests/q/"
    r"|^scripts/generate/"
    r"|^docs/reference/surfaces/"
    r"|^python/uqs/src/uqs/generated/"
)

ATTEST = "uv run python scripts/dev/attest_kdbx.py"


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def judged_paths(base: str, head: str) -> list[str]:
    changed = git("diff", "--name-only", f"{base}...{head}").split()
    return [p for p in changed if JUDGED.search(p)]


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
