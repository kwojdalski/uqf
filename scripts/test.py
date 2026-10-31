#!/usr/bin/env python3
"""test.py - what is left of the test lane dispatcher (#1808).

pytest is becoming the one interface for running tests, with qUnit and q
coverage as the backends it drives. Each lane moves once - its callers
switched and its code here removed in the same change, so no test has two
runners (#891). Moved so far, with the selector that runs each now:

  q-unit         uv run pytest tests/qrun/test_q_suite.py::test_the_q_suite_passes
                 (--q-files a.q,b.q for the suites a change reaches)
  q-order        uv run pytest tests/qrun/test_q_suite.py -m release
  q-unit-peachq  uv run pytest tests/qrun/test_q_suite.py -m peachq
  q-scripts      uv run pytest tests/qrun/test_worked_examples.py
  q-docs         uv run pytest tests/qrun/test_doc_blocks.py -m kdbx
  q-docs-peachq  uv run pytest tests/qrun/test_doc_blocks.py -m peachq
  q-examples     uv run pytest tests/qrun/test_documented_examples.py
  q-backfill-process, q-two-instances, q-remote-writer, q-metatables-hdb
                 uv run pytest tests/qrun/test_process_runners.py
                 (-k two-instances etc. for one of them)
  bundles        uv run pytest tests/qrun/test_bundle_suites.py -m release
  smoke          uv run pytest tests/qrun/test_external_smoke.py -m external
                 --smoke-targets HOST:PORT --smoke-tables TABLE:COL,COL
  attach-stack   uv run pytest -m live_stack
  remote-stack   uv run pytest tests/remote -m docker

What is still here:

  python               orchestration and the BFF: `uv run pytest`, now.
  q-coverage           the q half of `coverage`: fails when a function no
                       test enters is not in tests/q/coverage_baseline.txt,
                       or when a baseline entry is covered after all
  coverage             what the suites actually execute, q and Python both.

stack-smoke was never a test: it checks a RUNNING stack, and is
`uv run python scripts/gates/stack_smoke.py` (#1808).

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
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: KDB-X. This tree targets it alone and there is deliberately no fallback
#: interpreter - a suite that passed on something the code is not verified
#: against is worse than one that does not run. `QCMD` and `QHOME` let an
#: operator point it elsewhere DELIBERATELY; see README.md#requirements.
#: $QCMD, else `q` on PATH - TorQ's rule, and uqs.interpreter.q_command's,
#: restated because this file runs under a bare python3 that cannot import uqs.
Q_CMD = os.environ.get("QCMD") or "q"
QHOME = os.environ.get("QHOME", str(Path.home() / ".kx"))

Q_IMPLS = ("kdbx", "peachq")

#: The PeachQ resolver: the pinned build from the cache, built on first use
#: (scripts/peachq.py). Loaded by path, as this file is run from anywhere and
#: scripts/ is no package. What still uses it here is the interpreter check:
#: the PeachQ lanes now run under pytest, through its `peachq` fixture.
_spec = importlib.util.spec_from_file_location(
    "uqf_peachq", Path(__file__).resolve().with_name("peachq.py")
)
assert _spec and _spec.loader
peachq = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(peachq)
IDENTIFY_SCRIPT = peachq.IDENTIFY_SCRIPT
#: The variables that chose an interpreter before #1753, refused (#1753).
REMOVED_ENV = peachq.REMOVED_ENV


def identify(q: str, env: Mapping[str, str] | None = None) -> str:
    """Which q `q` is, as it answers: `kdbx` or `peachq`. Raises SystemExit,
    with what it printed, when it answers neither."""
    source = os.environ if env is None else env
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "identify.q"
        script.write_text(IDENTIFY_SCRIPT)
        result = subprocess.run(
            [q, str(script), "-q"],
            capture_output=True,
            text=True,
            timeout=30,
            env={**source, "QHOME": source.get("QHOME", QHOME)},
            stdin=subprocess.DEVNULL,
            check=False,
        )
    actual = (result.stdout.strip().splitlines()[-1:] or [""])[0]
    if actual not in Q_IMPLS:
        raise SystemExit(
            f"could not tell which q {q} is (exit {result.returncode}): "
            f"{(result.stdout + result.stderr).strip()[:200]!r}"
        )
    return actual


#: The binaries already warned about, so each is named once per run.
_WARNED: set[Path] = set()


def warn_if_unpinned(q: str) -> None:
    """One warning naming `q` and the pin, when `q` is a PeachQ other than the
    pinned build - uqs.interpreter.warn_if_unpinned's, in peachq.py's words."""
    resolved = Path(q).resolve()
    if resolved in _WARNED:
        return
    _WARNED.add(resolved)
    message = peachq.unpinned_warning(resolved)
    if message:
        print(f"warning: {message}", file=sys.stderr, flush=True)


def refuse_removed(env: Mapping[str, str] | None = None) -> None:
    """SystemExit naming each variable REMOVED_ENV names that is set."""
    problem = peachq.removed_variables_problem(os.environ if env is None else env)
    if problem:
        raise SystemExit(problem)


def check_interpreter(env: Mapping[str, str] | None = None) -> str | None:
    """Which q $QCMD is, as the binary answers; None when there is no
    runnable q (the lanes report that themselves). Refuses a removed variable
    first, and a binary that answers neither. A PeachQ that is not the
    pinned build is named in a warning."""
    source = os.environ if env is None else env
    refuse_removed(source)
    q = shutil.which(source.get("QCMD") or "q", path=source.get("PATH"))
    if q is None:
        return None
    actual = identify(q, source)
    if actual == "peachq":
        warn_if_unpinned(q)
    return actual


class LaneFailed(Exception):
    """A lane exited non-zero. Carries the lane name, which a bare exit code
    does not - the shell version made the caller guess."""

    def __init__(self, lane: str, code: int) -> None:
        super().__init__(f"lane {lane!r} failed with exit code {code}")
        self.lane = lane
        self.code = code


def _run(
    lane: str,
    argv: list[str],
    *,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    stdin_open: bool = False,
) -> None:
    """Run a command, streaming its output as it happens.

    stdin is /dev/null for every lane: a q script that ends without `exit`
    would otherwise stop at the q prompt and wait for a terminal - forever,
    when run from one (#891). With it, q exits at the end of the script.
    `timeout` bounds a lane whose process might wait on something else.
    `stdin_open` keeps stdin a pipe that never reaches EOF, for the one
    script that must end by its own `exit` (see STDIN_OPEN_EXAMPLES)."""
    # QCMD passed on, so a lane that starts its own q processes
    # (q-backfill-process, q-two-instances) starts the same interpreter.
    merged = {**os.environ, "QHOME": QHOME, "QCMD": Q_CMD, **(env or {})}
    read_fd = write_fd = -1
    if stdin_open:
        read_fd, write_fd = os.pipe()
    try:
        result = subprocess.run(
            argv,
            cwd=REPO,
            env=merged,
            stdin=read_fd if stdin_open else subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print(f"{lane}: still running after {timeout:g}s - stopped", flush=True)
        raise LaneFailed(lane, 124) from None
    finally:
        if stdin_open:
            os.close(read_fd)
            os.close(write_fd)
    if result.returncode != 0:
        raise LaneFailed(lane, result.returncode)


def _q(
    lane: str,
    script: str,
    *args: str,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    stdin_open: bool = False,
) -> None:
    q = shutil.which(Q_CMD)
    if q is None:
        raise LaneFailed(lane, 127)
    _run(lane, [q, script, *args], env=env, timeout=timeout, stdin_open=stdin_open)


def _banner(text: str) -> None:
    print(f"== {text} ==", flush=True)


# ------------------------------------------------------------------- lanes


def lane_python() -> None:
    _banner("python: orchestrator and frontend")
    # In parallel (pytest-xdist); --dist loadgroup keeps each xdist_group -
    # the tests that start a whole stack - on one worker.
    _run("python", ["uv", "run", "pytest", "-q", "-n", "auto", "--dist", "loadgroup"])


# ---------------------------------------------------------------- coverage


#: tests/q/run_coverage.q's exit when a SUBSET found a function never
#: entered: not a verdict, so the whole suite runs and decides (#1719).
COVERAGE_SUBSET_UNSURE = 3

#: When set, q-coverage writes there what decided it: `subset` or `all` -
#: which scripts/dev/attest_kdbx.py records in its trailer.
COVERAGE_RAN_ENV = "UQF_Q_COVERAGE_RAN"


def lane_q_coverage(qargs: Sequence[str] = ()) -> None:
    r"""The q half of `coverage`, and the half a q change needs.

    Split out so the pre-commit hook - which fires on `\.q$` - does not also
    run pytest over the Python packages. The gate itself lives in
    `run_coverage.q`: it compares the functions nothing entered against
    `tests/q/coverage_baseline.txt` and fails when they disagree either way.

    `qargs` (`-files a.q,... -modules src/x.q,...`, what
    scripts/dev/test_impact.py's coverage_for picks) run a SUBSET: those
    suites, judging only the functions those modules define (#1719). A subset
    that finds a judged function never entered exits 3, and the whole suite
    runs and its verdict is the gate's: a function only a suite outside the
    subset enters must not fail a change (the coverage map adds such suites,
    but may be stale). One the whole suite leaves never entered is never
    entered in any subset of it, so a subset that passes has missed no newly
    uncovered function it judges. A baseline entry a subset enters, the whole
    suite enters too, so that fails here. `-map PATH` on a whole run writes
    tests/q/coverage_map.txt's content to PATH.
    """
    ran = os.environ.get(COVERAGE_RAN_ENV)
    subset = "-files" in qargs
    if subset:
        _banner(f"q-coverage: a subset ({' '.join(qargs)})")
        try:
            _q("q-coverage", "tests/q/run_coverage.q", *qargs)
        except LaneFailed as failure:
            if failure.code != COVERAGE_SUBSET_UNSURE:
                raise
            print(
                "\nq-coverage: the subset is not a verdict - running the whole suite, "
                "whose verdict stands",
                flush=True,
            )
        else:
            if ran:
                Path(ran).write_text("subset\n")
            return
    _banner("q-coverage: q statement and branch coverage")
    _q("q-coverage", "tests/q/run_coverage.q", *(() if subset else qargs))
    if ran:
        Path(ran).write_text("all\n")


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
    called through that copy and never counted - `.qetl.io.memory` holds
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
    # pytest-cov combines the xdist workers' data into one report.
    xdist = ["-n", "auto", "--dist", "loadgroup"]
    _run("coverage", ["uv", "run", "pytest", "-q", *xdist, *cov, "--cov-report=term"])


# ------------------------------------------------------------------ dispatch

LANES: dict[str, Callable[[], None]] = {
    "python": lane_python,
    "q-coverage": lane_q_coverage,
    "coverage": lane_coverage,
}

#: The lanes that pass arguments argparse does not know on to their q script.
QARGS_LANES = ("q-coverage",)

#: The lanes `all` leaves out, each with its reason. Every other lane is in
#: the release run: a lane added to LANES is in `all` unless it is named here,
#: never left out by being forgotten (#1794).
EXCLUDED_FROM_ALL: dict[str, str] = {
    "q-coverage": "runs the q suite a second time, under instrumentation",
    "coverage": "runs the q suite a second time, under instrumentation, and pytest again",
}

#: `all`, in LANES' order.
ALL = [lane for lane in LANES if lane not in EXCLUDED_FROM_ALL]

ALL_HELP = "which suite to run; 'all' is every lane except " + ", ".join(EXCLUDED_FROM_ALL)

EPILOG = """\
Run the lane matching the layer you changed. `all` is for a release,
not for an edit.

The interpreter comes from $QCMD (default `q` on PATH) and $QHOME (default
~/.kx), and nothing else. There is no fallback: see README.md#requirements.
Which q it is - KDB-X or PeachQ - is asked of the binary before any lane
starts. A PeachQ that is not the pinned build is used, and named in a
warning. The tests that run on PeachQ whatever $QCMD is are pytest's now
(the `peachq` fixture, --peachq PATH).
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
        help=ALL_HELP,
    )
    # q-coverage only: anything argparse does not know goes on to its q
    # script as it is, e.g. `q-coverage -files test_a.q -modules src/a.q`.
    args, qargs = parser.parse_known_args(argv)
    args.qargs = qargs
    if args.qargs and args.lane not in QARGS_LANES:
        parser.error(
            f"unrecognized arguments: {' '.join(qargs)} "
            f"(only {' and '.join(QARGS_LANES)} pass their own on)"
        )

    lanes = ALL if args.lane == "all" else [args.lane]
    impl = check_interpreter()
    if impl is not None:
        _banner(f"interpreter: {impl} ({Q_CMD})")
    try:
        for name in lanes:
            if name == "q-coverage":
                lane_q_coverage(args.qargs)
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
