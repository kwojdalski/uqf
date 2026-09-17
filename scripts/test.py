#!/usr/bin/env python3
"""test.py - the lane dispatcher ETL-21 names.

ETL-21: "Use the suite that matches the changed layer: q-unit for q
behaviour, q-backfill-process for bounded process behaviour, and the
focused Python suites for orchestration."

D5 deliberately did NOT build a dispatcher when the only lane was q-unit: a
dispatcher with one option is worse than no dispatcher. There are several
genuinely different lanes now, so it earns its place.

The lanes differ in WHAT THEY PROVE, which is the whole point of choosing
between them:

  q-unit               deterministic q behaviour, in one process, no I/O
                       beyond a temp status directory. Fast, hermetic, and
                       the only lane the commit hook runs.
  q-metatables-hdb     metatable queries against a temporary partitioned HDB.
  q-examples           every documented @eg runs, in its own process, since
                       many examples change state.
  q-backfill-process   the bounded lifecycle end to end - locks, resumption
                       and coverage - which needs a real filesystem and real
                       child processes to be worth anything.
  python               orchestration and the BFF.
  coverage             what the suites actually execute, q and Python both.
  smoke                ETL-20's live external check. Explicitly NOT part of
                       any other lane: the deterministic suite proves local
                       behaviour, not that a configured external service is
                       reachable or compatible, and folding it in would make
                       every local run depend on a remote host being up.

Exits non-zero on the first failing lane.

WHY PYTHON RATHER THAN SHELL. The shell version had three bugs waiting to
happen that are simply absent here: a lane's temp directory was cleaned up
by a `trap` in one lane and leaked in another; `set -euo pipefail` had to be
repeated inside the one lane that ran in a subshell; and every lane's
failure surfaced as a bare non-zero exit with no indication of which lane
produced it. Python also lets the coverage lane exist at all - it has to
read a CSV, template a q file and diff two lists, which is a page of q-in-
shell quoting nobody should have to review.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: KDB-X. This tree targets it alone and there is deliberately no fallback
#: interpreter - a suite that passed on something the code is not verified
#: against is worse than one that does not run. `Q` and `QHOME` let an
#: operator point it elsewhere DELIBERATELY; see README.md#requirements.
Q = Path(os.environ.get("Q", Path.home() / ".kx" / "bin" / "q"))
QHOME = os.environ.get("QHOME", str(Path.home() / ".kx"))


class LaneFailed(Exception):
    """A lane exited non-zero. Carries the lane name, which a bare exit code
    does not - the shell version made the caller guess."""

    def __init__(self, lane: str, code: int) -> None:
        super().__init__(f"lane {lane!r} failed with exit code {code}")
        self.lane = lane
        self.code = code


def _run(lane: str, argv: list[str], *, env: dict[str, str] | None = None) -> None:
    """Run a command, inheriting stdio so test output streams as it happens."""
    merged = {**os.environ, "QHOME": QHOME, **(env or {})}
    result = subprocess.run(argv, cwd=REPO, env=merged, check=False)
    if result.returncode != 0:
        raise LaneFailed(lane, result.returncode)


def _q(lane: str, script: str, *args: str, env: dict[str, str] | None = None) -> None:
    if not Q.exists():
        raise LaneFailed(lane, 127)
    _run(lane, [str(Q), script, *args], env=env)


def _banner(text: str) -> None:
    print(f"== {text} ==", flush=True)


# ------------------------------------------------------------------- lanes


def lane_q_unit() -> None:
    _banner("q-unit: deterministic qUnit suite")
    _q("q-unit", "tests/run_tests.q")


def lane_q_backfill_process() -> None:
    _banner("q-backfill-process: bounded lifecycle on a real filesystem")
    # A fresh status directory per run, so this lane never reads state a
    # previous run left behind - a stale lock would make the single-instance
    # test pass for the wrong reason.
    with tempfile.TemporaryDirectory() as statusdir:
        _q(
            "q-backfill-process",
            "tests/q/run_backfill_process.q",
            env={"UQFSTATUSDIR": statusdir},
        )


def lane_q_metatables_hdb() -> None:
    _banner("q-metatables-hdb: metatable queries against a temporary HDB")
    with tempfile.TemporaryDirectory() as hdb:
        _q("q-metatables-hdb", "tests/q/run_metatables_hdb.q", hdb)


def lane_q_examples() -> None:
    _banner("q-examples: every documented @eg runs")
    # Its own process and its own status directory: examples stage coverage,
    # take locks and open runs, and none of that may leak into another lane.
    with tempfile.TemporaryDirectory() as statusdir:
        _q("q-examples", "tests/q/run_examples.q", env={"UQFSTATUSDIR": statusdir})


def lane_python() -> None:
    _banner("python: orchestrator and frontend")
    _run("python", ["uv", "run", "pytest", "-q"])


def lane_smoke() -> None:
    _banner("smoke: ETL-20 live external metadata check")
    _q("smoke", "tests/q/smoke_external_metadata.q")


# ---------------------------------------------------------------- coverage


def lane_coverage() -> None:
    """What the suites actually EXECUTE - not what they mention.

    Python is pytest-cov. q is the `.cov` library
    (`scripts/coverage.q`), driven over the whole suite by
    `tests/q/run_coverage.q`.

    ONE INSTRUMENTER, not two. This lane briefly had a second - a Python
    tool that rewrote the source files before they loaded - and two
    instrumenters for one language is the duplication this repository has an
    auditor for. `.cov` is the one that stays, because it is also the API a
    person uses interactively: `run this call, show me what it missed`.

    What made that possible was closing in-memory instrumentation's one real
    hole. A function whose VALUE was captured into a registry beforehand is
    called through that copy and never counted - `.qio.memory` holds
    `write_memory`, so every bounded worker wrote through a captured copy
    and it reported as never called while being exercised on every window.
    `.cov.reseed` swaps those copies too.
    """
    _banner("coverage: q statement and branch coverage")
    _q("coverage", "tests/q/run_coverage.q")

    print()
    _banner("coverage: python line coverage")
    # Each member's src/ rather than `--cov=python`, which also measures the
    # TEST files and reports them at ~100% - dragging the headline up by
    # thirteen points while saying nothing about the code under test.
    cov = [
        f"--cov={d}" for d in sorted(str(p.relative_to(REPO)) for p in REPO.glob("python/*/src"))
    ]
    if not cov:
        raise LaneFailed("coverage", 1)
    _run("coverage", ["uv", "run", "pytest", "-q", *cov, "--cov-report=term"])


# ------------------------------------------------------------------ dispatch

LANES: dict[str, Callable[[], None]] = {
    "q-unit": lane_q_unit,
    "q-metatables-hdb": lane_q_metatables_hdb,
    "q-backfill-process": lane_q_backfill_process,
    "q-examples": lane_q_examples,
    "python": lane_python,
    "coverage": lane_coverage,
    "smoke": lane_smoke,
}

#: `all` is every lane except smoke (ETL-20) and coverage - coverage runs the
#: q suite a second time under instrumentation, which is worth asking for and
#: not worth paying for on every release run.
ALL = ["q-unit", "q-examples", "q-backfill-process", "q-metatables-hdb", "python"]

EPILOG = """\
ETL-21: run the lane matching the layer you changed. `all` is for a release,
not for an edit.

The interpreter comes from $Q (default ~/.kx/bin/q) and $QHOME (default
~/.kx). There is no fallback: see README.md#requirements.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scripts/test.py",
        description=__doc__.split("\n\n")[1],
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "lane",
        choices=[*LANES, "all"],
        help="which suite to run; 'all' is every lane except smoke and coverage",
    )
    args = parser.parse_args(argv)

    lanes = ALL if args.lane == "all" else [args.lane]
    try:
        for name in lanes:
            LANES[name]()
    except LaneFailed as failure:
        if failure.code == 127:
            print(
                f"\nno q interpreter at {Q}. Set $Q to point at one, or see\n"
                "README.md#requirements.",
                file=sys.stderr,
            )
        else:
            print(f"\nFAILED: {failure}", file=sys.stderr)
        return failure.code
    return 0


if __name__ == "__main__":
    if shutil.which("uv") is None:
        print("uv is not on PATH; see README.md#requirements", file=sys.stderr)
    raise SystemExit(main())
