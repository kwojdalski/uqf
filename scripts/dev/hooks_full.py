#!/usr/bin/env python3
"""The one switch back to the full commit hooks: UQF_HOOKS_FULL=1 (#1879).

A commit's hooks are targeted by default, and CI runs everything in full on a
clean Linux checkout. Each check runs in one place:

* python-tests runs only the Python tests the commit reaches
  (scripts/dev/python_tests_hook.py);
* q-tests and q-coverage run only the q suites the branch reaches
  (scripts/dev/test_impact.py);
* doc-links (the Quarto render) and q-docs-peachq (the docs' q blocks on
  PeachQ) do not run at all: CI runs both on every pull request that can
  change them.

UQF_HOOKS_FULL=1 turns all of that back into the full set the hooks ran
before: the whole Python suite, the whole q suite and its whole coverage
gate (it implies UQF_Q_TESTS_FULL=1, which still works on its own for the
q hooks alone), the docs render, and the docs' q blocks on PeachQ.

    UQF_HOOKS_FULL=1 git commit ...     # this commit, every hook in full
    python3 scripts/dev/hooks_full.py --all
                                        # every hook in full over the whole tree,
                                        # without a commit: the whole local suite
    python3 scripts/dev/hooks_full.py --hook doc-links -- CMD ...
                                        # run CMD only in full mode (what a hook
                                        # that runs only then is entered as)

Standard library only, and Python 3.10-compatible: hooks run it with the
system python3.
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
    pass. `--all`: every hook, in full mode, over every file."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--all"]:
        cmd = ["uv", "tool", "run", "--from", PRE_COMMIT, "pre-commit", "run", "--all-files"]
        return subprocess.run(cmd, env={**os.environ, ENV: "1"}, check=False).returncode
    if len(args) < 4 or args[0] != "--hook" or args[2] != "--":
        print("usage: hooks_full.py --all | --hook NAME -- CMD [ARG ...]", file=sys.stderr)
        return 2
    name, cmd = args[1], args[3:]
    if not full():
        print(f"{name}: not run at commit - CI runs it on every pull request; {ENV}=1 runs it here")
        return 0
    return subprocess.run(cmd, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
