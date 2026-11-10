"""The git calls scripts/dev's commands share: one runner, one changed-paths read.

ci_scope.py, peachq_push.py, python_tests_hook.py, attest_kdbx.py and
master_coverage.py each ran `git` through subprocess themselves, and three of
them parsed `git diff`'s list of changed paths, each its own way. They load
this file by path, as scripts/dev's modules load each other (scripts/ is not
a package).

Standard library only, and Python 3.10-compatible, like hooks_full.py: some of
its callers run under a bare python3.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

#: This checkout: scripts/dev/<this file>.
REPO = Path(__file__).resolve().parents[2]


def git(*args: str, cwd: Path = REPO, check: bool = True) -> subprocess.CompletedProcess:
    """`git *args` in `cwd`, its output captured as text. With `check`, a
    failing git raises CalledProcessError; without, the caller reads the
    return code."""
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=check)


def changed(
    *revs: str,
    cwd: Path = REPO,
    status: bool = True,
    renames: bool = False,
    diff_filter: str | None = None,
) -> list[tuple[str, str]] | list[str]:
    """What `git diff *revs` changes, in `cwd`.

    `revs` is what git diff takes: `base...head` (a pull request's three-dot
    range), `--cached` (the index), or two refs.

    With `status`, (status letter, path) pairs, read NUL-separated (`-z`); else
    the paths alone, one per line. Without `renames` a rename is its old path
    deleted and its new one added (`--no-renames`); with it, git's rename
    detection reports the new path. `diff_filter` is git's `--diff-filter`
    (e.g. "AMR": added, modified or renamed into - no deletions).
    """
    args = ["diff", *revs, "--name-status" if status else "--name-only"]
    if not renames:
        args.append("--no-renames")
    if diff_filter:
        args.append(f"--diff-filter={diff_filter}")
    if not status:
        return [p for p in git(*args, cwd=cwd).stdout.splitlines() if p]
    words = [w for w in git(*args, "-z", cwd=cwd).stdout.split("\0") if w]
    return [(words[i][0], words[i + 1]) for i in range(0, len(words) - 1, 2)]
