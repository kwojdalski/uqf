#!/usr/bin/env python3
"""The one switch back to the full commit hooks: UQF_HOOKS_FULL=1 (#1879).

A commit's and a push's hooks are targeted by default, and CI runs everything
in full on a clean Linux checkout. Each check runs in one place:

* python-tests, at push (#1965), runs only the Python tests the push reaches
  (scripts/dev/python_tests_hook.py);
* q-tests and q-coverage, at push, run only the q suites the branch reaches
  (scripts/dev/q_test_impact.py);
* doc-links (the Quarto render) and q-docs-peachq (the docs' q blocks on
  PeachQ) do not run at all: CI runs both on master and nightly (#2167).

UQF_HOOKS_FULL=1 turns all of that back into the full set the hooks ran
before: the whole Python suite, the whole q suite and its whole coverage
gate (it implies UQF_Q_TESTS_FULL=1, which still works on its own for the
q hooks alone), the docs render, and the docs' q blocks on PeachQ.

    UQF_HOOKS_FULL=1 git commit ...     # this commit's hooks in full
    UQF_HOOKS_FULL=1 git push ...       # this push's: the whole Python and q suites
    python3 scripts/dev/hooks_full.py --all
                                        # every commit and push hook in full over
                                        # the whole tree: the whole local suite
    python3 scripts/dev/hooks_full.py --hook doc-links -- CMD ...
                                        # run CMD only in full mode (what a hook
                                        # that runs only then is entered as)

Standard library only, and Python 3.10-compatible, so a bare python3 can run
it by hand; the hooks enter it as `uv run python` (#2449).
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping

#: `1` runs every hook in full.
ENV = "UQF_HOOKS_FULL"

#: The pre-commit `--all` runs the hooks with: CI's, held equal to it by
#: `scripts/gates/check.py tool-pins`.
PRE_COMMIT = "pre-commit==4.6.2"


def full(environ: Mapping[str, str] = os.environ) -> bool:
    """Whether the hooks run in full mode."""
    return environ.get(ENV) == "1"


def main(argv: list[str] | None = None) -> int:
    """`--hook NAME -- CMD ...`: run CMD in full mode, else say why not and
    pass. `--all`: every commit hook, then every push hook, in full mode, over
    every file."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--all"]:
        cmd = ["uv", "tool", "run", "--from", PRE_COMMIT, "pre-commit", "run", "--all-files"]
        env = {**os.environ, ENV: "1"}
        codes = [subprocess.run([*cmd, "--hook-stage", stage], env=env, check=False).returncode
                 for stage in ("pre-commit", "pre-push")]  # fmt: skip
        return next((c for c in codes if c), 0)
    if len(args) < 4 or args[0] != "--hook" or args[2] != "--":
        print("usage: hooks_full.py --all | --hook NAME -- CMD [ARG ...]", file=sys.stderr)
        return 2
    name, cmd = args[1], args[3:]
    if not full():
        print(f"{name}: not run at commit - CI runs it on master and nightly; {ENV}=1 runs it here")
        return 0
    return subprocess.run(cmd, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
