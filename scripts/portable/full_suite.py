#!/usr/bin/env python3
"""The whole q suite on PeachQ, flattened, held to a list of known gaps (#863).

PeachQ rejects nested working contexts (`\\d .a.b`), so it cannot load this tree
as written. flatten_contexts.py rewrites a copy that needs none; this runs the
full suite - tests/run_tests.q, every suite - on that copy:

  1. flatten src/, scripts/ and tests/ into a scratch directory. A refusal
     fails the lane: the tree must stay convertible, and a new construct the
     flattener cannot rewrite is caught here rather than on a 4.0 server.
  2. lay the flattened files over a copy of every tracked file - lib/ included
     as it is, never converted or edited - so the suite runs as in a checkout.
  3. run the suite there with UQF_TEST_FAILURES set, so it writes the tests
     that did not pass, with why, as data.
  4. compare them with tests/q/peachq_known_gaps.txt, both ways:
       - a failure not on the list fails the lane: a regression, or a new
         test that does not run on PeachQ and has not said why;
       - a listed test that passed fails the lane too: PeachQ or the code
         improved, and the entry must go. The list only ever shrinks.

    python3 scripts/portable/full_suite.py --q "$PEACHQ_BIN"
    python3 scripts/test.py q-unit-peachq      # the same, as a lane

The known-gaps file is one test per line, its full name, then `#` and the
reason it cannot pass on PeachQ:

    .iotest.test_hdb_appends_a_second_window  # 'nyi: appending a partitioned HDB

Standard library only.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GAPS = REPO / "tests" / "q" / "peachq_known_gaps.txt"
FLATTENER = REPO / "scripts" / "portable" / "flatten_contexts.py"
#: What is flattened. lib/ is not: the flattener leaves vendored code alone.
CONVERTED = ("src", "scripts", "tests")


def read_gaps(path: Path = GAPS) -> dict[str, str]:
    """The known gaps: test name -> reason. A line without a reason is refused,
    because an entry nobody can explain is an entry nobody can remove."""
    gaps: dict[str, str] = {}
    for number, raw in enumerate(path.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, _, reason = line.partition("#")
        name, reason = name.strip(), reason.strip()
        if not name.startswith(".") or not reason:
            raise SystemExit(f"{path.name}:{number}: expected `.suite.test_name  # reason`")
        if name in gaps:
            raise SystemExit(f"{path.name}:{number}: {name} is listed twice")
        gaps[name] = reason
    return gaps


def read_failures(path: Path) -> dict[str, str]:
    """The suite's failures file: test name -> `status: detail`."""
    failed: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        name, status, detail = (line.split("\t") + ["", ""])[:3]
        failed[name] = f"{status}: {detail}"
    return failed


def compare(failed: dict[str, str], gaps: dict[str, str]) -> tuple[list[str], list[str]]:
    """(failures not on the list, listed tests that passed)."""
    return sorted(set(failed) - set(gaps)), sorted(set(gaps) - set(failed))


def build_tree(into: Path) -> None:
    """A runnable copy of the checkout with src/, scripts/ and tests/ flattened.
    Raises SystemExit when the flattener refuses."""
    flat = into / "flat"
    r = subprocess.run(
        [sys.executable, str(FLATTENER), *CONVERTED, "--root", str(REPO), "--out", str(flat),
         "--quiet"],
        cwd=REPO, capture_output=True, text=True, check=False,
    )  # fmt: skip
    if r.returncode:
        tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-30:])
        raise SystemExit(f"the flattener refused - the tree must stay convertible:\n{tail}")
    tree = into / "tree"
    files = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z"], capture_output=True, check=True
    ).stdout.split(b"\0")
    for rel in (f.decode() for f in files if f):
        src = REPO / rel
        if src.is_file():
            (tree / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, tree / rel)
    shutil.copytree(flat, tree, dirs_exist_ok=True)


def run(q: str, timeout: float, keep: Path | None = None) -> int:
    gaps = read_gaps()
    with tempfile.TemporaryDirectory() as tmp:
        work = keep or Path(tmp)
        work.mkdir(parents=True, exist_ok=True)
        print(f"flattening {', '.join(CONVERTED)} into {work}", flush=True)
        build_tree(work)
        failures = work / "failures.txt"
        env = {**os.environ, "UQF_Q_IMPL": "peachq", "QCMD": q, "UQF_TEST_FAILURES": str(failures)}
        r = subprocess.run(
            [q, "tests/run_tests.q", "-q"], cwd=work / "tree", env=env, timeout=timeout, check=False
        )
        if not failures.exists():
            print(f"the suite exited {r.returncode} without writing its failures - it did not "
                  "finish loading", file=sys.stderr)  # fmt: skip
            return 1
        failed = read_failures(failures)
    new, fixed = compare(failed, gaps)
    for name in new:
        print(f"NEW FAILURE  {name}  {failed[name]}", file=sys.stderr)
    for name in fixed:
        print(f"NOW PASSES   {name}  - remove it from {GAPS.name} ({gaps[name]})", file=sys.stderr)
    print(f"{len(failed)} not passing, {len(gaps)} known gaps: {len(new)} new, {len(fixed)} fixed",
          flush=True)  # fmt: skip
    return 1 if new or fixed else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--q", default=os.environ.get("QCMD", "q"), help="the PeachQ binary")
    p.add_argument("--timeout", type=float, default=1800, help="seconds for the suite")
    p.add_argument("--keep", type=Path, help="build the tree here and leave it, to debug")
    a = p.parse_args(argv)
    return run(a.q, a.timeout, a.keep)


if __name__ == "__main__":
    raise SystemExit(main())
