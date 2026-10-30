"""The `python-tests` pre-commit hook: which Python tests a commit runs.

Two modes, chosen from what is staged:

* **Only .py files** - `pytest --testmon`: the tests whose recorded coverage
  touches a changed file, and nothing else. pytest-testmon keeps that record in
  `.testmondata` at the repository root (gitignored; it is this checkout's, not
  the project's).
* **Anything else** - the whole suite, in parallel (`-n auto --dist loadgroup`).
  testmon sees only the Python a test executes in-process. Many tests start q,
  or read a CSV, a config or a document, and a change to any of those is
  invisible to it: a .q edit selected by testmon would select nothing.

So testmon is used only where it can see the change, and even then it falls
back to the whole suite when it cannot vouch for a file (below). CI and master
always run the whole suite; this only makes a commit's feedback faster.

testmon and xdist are not combined: testmon's record is written by one process
and does not survive being split across workers.

    uv run python scripts/dev/python_tests_hook.py            # what the hook runs
    uv run python scripts/dev/python_tests_hook.py --dry-run  # say which mode, run nothing
"""

from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

#: pytest-testmon's database, at the rootdir it records against.
TESTMON_DATA = REPO / ".testmondata"

#: The tests a q hook runs at commit (the q_backend marker, conftest.py) are
#: left out of both modes: q-tests runs the worked examples and q-docs and
#: q-docs-peachq the docs' q blocks, each on the interpreter it requires, and
#: a commit that fired both hooks would otherwise run them twice (#1808). A
#: plain `uv run pytest`, and CI, run them with everything else.
NOT_Q_HOOKS = ["-m", "not q_backend"]

FULL = ["uv", "run", "pytest", "-q", "-n", "auto", "--dist", "loadgroup", *NOT_Q_HOOKS]
TESTMON = ["uv", "run", "pytest", "-q", "--testmon", *NOT_Q_HOOKS]


def staged_files() -> list[str]:
    """Every path the commit adds, changes, renames or deletes."""
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACDMRT", "-z"],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO,
    ).stdout
    return [p for p in out.split("\0") if p]


def testmon_files(data: Path = TESTMON_DATA) -> set[str] | None:
    """The source files testmon's record says some test executed, or None
    when there is no record it can be asked about."""
    if not data.is_file():
        return None
    try:
        with sqlite3.connect(f"file:{data}?mode=ro", uri=True) as db:
            rows = db.execute("SELECT DISTINCT filename FROM file_fp").fetchall()
    except sqlite3.Error:
        return None
    return {row[0] for row in rows}


def _is_test_module(path: str) -> bool:
    """A test file pytest collects: testmon runs a new or changed one itself."""
    return Path(path).name.startswith("test_") and "/tests/" in f"/{path}"


def choose(staged: list[str], known: set[str] | None) -> tuple[str, str]:
    """("testmon" | "full", why) for a commit staging `staged`.

    The whole suite unless every staged file is Python testmon can see:
    a test module (testmon runs new and changed tests), or a file its record
    shows a test executing. A conftest.py shapes every test under it, and a
    .py file no test imports in-process - a gate a test runs as a subprocess,
    say - is a change testmon would select nothing for.
    """
    if not staged:
        return "full", "nothing staged (a manual or --all-files run)"
    other = [p for p in staged if not p.endswith(".py")]
    if other:
        return "full", f"{len(other)} staged non-Python file(s), e.g. {other[0]}"
    conftests = [p for p in staged if Path(p).name == "conftest.py"]
    if conftests:
        return "full", f"a conftest changed ({conftests[0]})"
    if known is None:
        return "full", f"no testmon record yet ({TESTMON_DATA.name})"
    unseen = [p for p in staged if not _is_test_module(p) and p not in known]
    if unseen:
        return "full", f"testmon has no test that runs {unseen[0]} in-process"
    return "testmon", f"{len(staged)} Python file(s), all known to testmon"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="say which mode, run nothing")
    args = ap.parse_args()

    staged = staged_files()
    mode, why = choose(staged, testmon_files())
    cmd = TESTMON if mode == "testmon" else FULL
    print(f"python-tests: {mode} - {why}", flush=True)
    if args.dry_run:
        print(" ".join(cmd))
        return 0
    code = subprocess.run(cmd, cwd=REPO, check=False).returncode
    if mode == "full" and code == 0 and not TESTMON_DATA.is_file():
        # Said once, where it helps: the faster mode needs a record to start.
        print(
            "python-tests: run `uv run pytest --testmon` once to let commits of "
            "Python alone run only the tests they affect",
            flush=True,
        )
    return code


if __name__ == "__main__":
    sys.exit(main())
