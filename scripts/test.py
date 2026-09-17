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
import csv
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


def lane_python() -> None:
    _banner("python: orchestrator and frontend")
    _run("python", ["uv", "run", "pytest", "-q"])


def lane_smoke() -> None:
    _banner("smoke: ETL-20 live external metadata check")
    _q("smoke", "tests/q/smoke_external_metadata.q")


# ---------------------------------------------------------------- coverage


def _q_function_names() -> list[str]:
    """Every function this tree declares, from the contract surface.

    The surface rather than a scan of the live namespaces, because the live
    set also holds `.qunit` (the vendored test framework), `.Q`, `.z` and the
    test namespaces, and excluding those by pattern is a guess that rots. The
    surface is exactly this tree's own declared names, and
    `contract_surface.py check` is a gate, so it cannot go stale silently.
    """
    surface = REPO / "docs" / "migrations" / "surfaces" / "uqf-local" / "functions.csv"
    with surface.open(newline="") as fh:
        return [
            f".{row['namespace']}.{row['name']}"
            for row in csv.DictReader(fh)
            if row["kind"] == "function"
        ]


def lane_coverage() -> None:
    """What the suites actually EXECUTE - not what they mention.

    Two different measurements, because the two languages afford different
    things:

      Python  real line coverage, via pytest-cov.
      q       real CALL coverage, by wrapping every declared function with a
              counter before the test files load. q has no coverage tool, and
              a grep for the name in tests/ would count a function named in a
              comment as tested.

    READ THE q NUMBER AS A LOWER BOUND. A function whose VALUE was captured
    into a registry at load time - `.qio.memory` holds `write_memory`,
    `.qsrc.register` holds a source's `query` - is called through that copy,
    which no wrapper installed afterwards can see. Such a function reports as
    uncalled while being thoroughly exercised. The lane says so rather than
    letting the reader assume otherwise.
    """
    _banner("coverage: q call coverage")
    names = _q_function_names()
    targets = ";".join(f'`$"{n}"' for n in names)
    instrument = (REPO / "tests" / "q" / "coverage_instrument.q").read_text()

    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "uncalled.txt"
        gen = Path(tmp) / "instrument.q"
        gen.write_text(
            instrument
            + f"\n.qqc.targets:({targets});\n"
            + f'.qqc.report_path:"{report}";\n'
            + ".qqc.install[];\n"
        )
        _q("coverage", "tests/run_tests.q", env={"UQF_COVERAGE": str(gen)})
        uncalled = report.read_text().split() if report.exists() else []

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

    if uncalled:
        print()
        print(f"q functions never called by the q-unit lane ({len(uncalled)}):")
        for name in sorted(uncalled):
            print(f"  {name}")
        print(
            "\nBefore treating any of these as untested, check whether it is\n"
            "reached through a captured value (see this lane's docstring), run\n"
            "in a child process by q-backfill-process, or excluded from the\n"
            "default lanes on purpose - .qodbc needs a driver this tree does\n"
            "not require, and .qsrc.validate_live is the smoke lane's."
        )


# ------------------------------------------------------------------ dispatch

LANES: dict[str, Callable[[], None]] = {
    "q-unit": lane_q_unit,
    "q-metatables-hdb": lane_q_metatables_hdb,
    "q-backfill-process": lane_q_backfill_process,
    "python": lane_python,
    "coverage": lane_coverage,
    "smoke": lane_smoke,
}

#: `all` is every lane except smoke (ETL-20) and coverage - coverage runs the
#: q suite a second time under instrumentation, which is worth asking for and
#: not worth paying for on every release run.
ALL = ["q-unit", "q-backfill-process", "q-metatables-hdb", "python"]

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
