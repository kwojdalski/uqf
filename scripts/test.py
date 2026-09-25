#!/usr/bin/env python3
"""test.py - the test lane dispatcher.

The rule: "Use the suite that matches the changed layer: q-unit for q
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
  q-order              the q suite again, suites in the opposite order, so a
                       test that depends on running after another one fails
  q-examples           every documented @eg runs, in its own process, since
                       many examples change state.
  q-scripts            every worked example under scripts/examples/, each
                       run as a script in its own process. Nothing ran these
                       before; the first commit to add one found a false
                       claim in uqs_tables.q that had stood untested.
  q-two-instances      a second kdb+ process is started on the starter
                       pack's HDB and a bounded worker moves trades out of
                       it - the only lane in which a worker's LIVE path runs.
  q-backfill-process   the bounded lifecycle end to end - locks, resumption
                       and coverage - which needs a real filesystem and real
                       child processes to be worth anything.
  python               orchestration and the BFF.
  q-coverage           the q half of `coverage`: fails when a function no
                       test enters is not in tests/q/coverage_baseline.txt,
                       or when a baseline entry is covered after all
  coverage             what the suites actually execute, q and Python both.
  smoke                the live external check. Explicitly NOT part of
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
from collections.abc import Callable, Sequence
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: KDB-X. This tree targets it alone and there is deliberately no fallback
#: interpreter - a suite that passed on something the code is not verified
#: against is worse than one that does not run. `QCMD` and `QHOME` let an
#: operator point it elsewhere DELIBERATELY; see README.md#requirements.
#: $QCMD, else `q` on PATH - TorQ's rule, and uqs.paths.q_command's,
#: restated because this file runs under a bare python3 that cannot import uqs.
Q_CMD = os.environ.get("QCMD") or "q"
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
    # QCMD passed on, so a lane that starts its own q processes
    # (q-backfill-process, q-two-instances) starts the same interpreter.
    merged = {**os.environ, "QHOME": QHOME, "QCMD": Q_CMD, **(env or {})}
    result = subprocess.run(argv, cwd=REPO, env=merged, check=False)
    if result.returncode != 0:
        raise LaneFailed(lane, result.returncode)


def _q(lane: str, script: str, *args: str, env: dict[str, str] | None = None) -> None:
    q = shutil.which(Q_CMD)
    if q is None:
        raise LaneFailed(lane, 127)
    _run(lane, [q, script, *args], env=env)


def _banner(text: str) -> None:
    print(f"== {text} ==", flush=True)


# ------------------------------------------------------------------- lanes


def lane_q_unit() -> None:
    _banner("q-unit: deterministic qUnit suite")
    _q("q-unit", "tests/run_tests.q")


def lane_q_order() -> None:
    _banner("q-order: the same suite, in the opposite order")
    # Four tests here once passed on run_tests.q's hand-written namespace
    # order alone: each scanned live global state that other suites mutate,
    # so whichever ran first decided the answer. They were fixed to ask about
    # the tree rather than about the process, and this is what keeps them
    # fixed - a new dependency on running after some other suite fails here
    # rather than the next time anyone touches that list.
    _q("q-order", "tests/run_tests.q", env={"UQF_TEST_ORDER": "reverse"})


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


def lane_q_scripts() -> None:
    """Every worked example under scripts/examples/, each in its own process.

    These are scripts a reader runs by hand to see the library work end to
    end, and until this lane existed NOTHING ran them - five of them, none
    executed by any suite, gate or hook. `find src scripts` does scan them
    for `@eg` blocks, but none of these files has one: an example script is
    a top-level narrative, not a documented function.

    That gap was not theoretical. The first commit to add a scenario here
    found `uqs_tables.q` claiming its `quotes` table was usable by
    `cross_book_at` "with no reshaping", when `cross_book_at` refuses it
    outright for a missing `ts`. A prose claim about two halves of this tree
    fitting together, false for as long as it stood, because nothing ever
    called one half with the other's table.

    Each runs in its own process: they load src/init.q, define top-level
    tables and insert into them, and a shared process would let one
    example's rows reach the next. A non-zero exit fails the lane, which is
    the whole point - the scripts already narrate through .qlog, so the
    output is the report.
    """
    _banner("q-scripts: every worked example under scripts/examples/")
    # From REPO, not the current directory: `python3 scripts/test.py` run
    # from anywhere else would otherwise find no examples and stop here.
    scripts = sorted(p.relative_to(REPO) for p in (REPO / "scripts" / "examples").glob("*.q"))
    if not scripts:  # pragma: no cover - the directory is not empty
        raise SystemExit("q-scripts: no examples found under scripts/examples/")
    for script in scripts:
        with tempfile.TemporaryDirectory() as statusdir:
            _q(f"q-scripts:{script.stem}", str(script), env={"UQFSTATUSDIR": statusdir})


def lane_q_two_instances() -> None:
    _banner("q-two-instances: data moved between two kdb+ processes")
    # The upstream is a second q process on a port; its own status directory
    # keeps this run's locks and checkpoints away from every other lane's.
    with tempfile.TemporaryDirectory() as statusdir:
        _q("q-two-instances", "tests/q/run_two_instances.q", env={"UQFSTATUSDIR": statusdir})


def lane_python() -> None:
    _banner("python: orchestrator and frontend")
    _run("python", ["uv", "run", "pytest", "-q"])


def lane_smoke(flags: Sequence[str] = ()) -> None:
    """`flags` are the script's own: -targets, -tables, -timeout_ms. With
    none, it reports SKIP and exits 0 - an unconfigured checkout is not a
    failure."""
    _banner("smoke: live external metadata check")
    _q("smoke", "tests/q/smoke_external_metadata.q", *flags)


def smoke_flags(targets: Sequence[str], tables: Sequence[str], timeout_ms: int | None) -> list[str]:
    """The smoke lane's options as the q script's own flags."""
    flags: list[str] = []
    if targets:
        flags += ["-targets", *targets]
    if tables:
        flags += ["-tables", *tables]
    if timeout_ms is not None:
        flags += ["-timeout_ms", str(timeout_ms)]
    return flags


def lane_stack_smoke() -> None:
    """Start the real stack and check it is actually doing something.

    The only lane that runs PROCESSES rather than functions, and the only
    one that can catch a job whose computation is correct and whose wiring
    is not - which is every ETL bug this tree has had (#298). Restarts the
    stack, watches it, and fails if a running pipeline's declared output
    has no rows or a running process wrote to its error log meanwhile.
    """
    _banner("stack-smoke: the running stack publishes and stays quiet")
    # uv run, not sys.executable: the gate imports uqs, which
    # needs the workspace environment, the same way lane_python does.
    _run("stack-smoke", ["uv", "run", "python", "scripts/gates/stack_smoke.py"])


# ---------------------------------------------------------------- coverage


def lane_q_coverage() -> None:
    r"""The q half of `coverage`, and the half a q change needs.

    Split out so the pre-commit hook - which fires on `\.q$` - does not also
    run pytest over the Python packages. The gate itself lives in
    `run_coverage.q`: it compares the functions nothing entered against
    `tests/q/coverage_baseline.txt` and fails when they disagree either way.
    """
    _banner("q-coverage: q statement and branch coverage")
    _q("q-coverage", "tests/q/run_coverage.q")


def lane_coverage() -> None:
    """What the suites actually EXECUTE - not what they mention.

    Python is pytest-cov. q is the `.cov` library
    (`scripts/dev/coverage.q`), driven over the whole suite by
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
    lane_q_coverage()

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
    "q-order": lane_q_order,
    "q-metatables-hdb": lane_q_metatables_hdb,
    "q-backfill-process": lane_q_backfill_process,
    "q-examples": lane_q_examples,
    "q-scripts": lane_q_scripts,
    "q-two-instances": lane_q_two_instances,
    "python": lane_python,
    "q-coverage": lane_q_coverage,
    "coverage": lane_coverage,
    "smoke": lane_smoke,
    "stack-smoke": lane_stack_smoke,
}

#: `all` is every lane except smoke, stack-smoke and coverage.
#: coverage runs the q suite a second time under instrumentation;
#: stack-smoke needs a licence, free ports and a couple of minutes because
#: it starts the actual stack. Both are worth asking for and neither is
#: worth paying for on every release run.
ALL = [
    "q-unit",
    "q-order",
    "q-examples",
    "q-scripts",
    "q-backfill-process",
    "q-two-instances",
    "q-metatables-hdb",
    "python",
]

EPILOG = """\
Run the lane matching the layer you changed. `all` is for a release,
not for an edit.

The interpreter comes from $QCMD (default `q` on PATH) and $QHOME (default
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
    smoke = parser.add_argument_group("smoke lane only")
    smoke.add_argument(
        "--targets", nargs="+", default=[], metavar="HOST:PORT", help="sources to check"
    )
    smoke.add_argument(
        "--tables",
        nargs="+",
        default=[],
        metavar="TABLE:COL,COL",
        help="each table and the columns it must still have",
    )
    smoke.add_argument(
        "--timeout-ms", type=int, default=None, help="per-connection timeout (default 5000)"
    )
    args = parser.parse_args(argv)
    flags = smoke_flags(args.targets, args.tables, args.timeout_ms)
    if flags and args.lane != "smoke":
        parser.error("--targets, --tables and --timeout-ms apply to the smoke lane only")

    lanes = ALL if args.lane == "all" else [args.lane]
    try:
        for name in lanes:
            if name == "smoke":
                lane_smoke(flags)
            else:
                LANES[name]()
    except LaneFailed as failure:
        if failure.code == 127:
            print(
                f"\nno q interpreter: {Q_CMD!r} is not runnable. Set $QCMD to point\n"
                "at one, put q on PATH, or see README.md#requirements.",
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
