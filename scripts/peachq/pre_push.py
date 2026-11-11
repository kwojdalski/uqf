#!/usr/bin/env python3
"""The q test files a branch adds or changes, run on PeachQ before it is pushed (#1965).

CI's "PeachQ q tests" job runs the whole suite on PeachQ, and is where that
check lives (#1879). This is only an early, targeted catch of the commonest
way it fails: a NEW test that KDB-X passes and PeachQ does not. On 2026-11-03
five pull requests (#1941, #1944, #1948, #1959, #1961) bounced off that job
for exactly this, each costing an hour or more and a merge-queue cycle, and
each reproduced on PeachQ locally in about a minute.

What runs: the tests/q/test_*.q files `origin/master...HEAD` adds or changes,
plus, for each production .q it changes, the suite named after it
(src/pricing/forwards.q -> tests/q/test_forwards.q) when one exists, plus
the documented-examples suite, tests/q/test_docstring_examples.q, when the
diff of any .q adds or removes an `@eg` tag line (#1978) - a failing `@eg`
was the commonest PeachQ bounce of all (#1961, #1974), and its module's
namesake suite does not run it. That suite is all-or-nothing: it scans every
`@eg` under src/ and scripts/ and adds about 2 s on PeachQ. Not the
full reach scripts/dev/q_test_impact.py computes: building its graph takes
about 45 s of CPU before any test runs, and the q-tests hook beside this one
already pays that on KDB-X. One PeachQ process runs them, through
`tests/run_tests.q -files` - every file is loaded, only those suites run.

What fails the push: a test that did not pass and is not listed in
tests/q/peachq_known_gaps.txt. A listed test that passes here is reported but
does not fail: the list records CI's platform, and CI enforces that it
shrinks (scripts/peachq/full_suite.py).

PeachQ is found as the q-docs-peachq hook finds it: $QCMD when it answers as
PeachQ, else the pinned build from scripts/peachq/pin.json (scripts/peachq/resolve.py,
built into the cache on first use).

    uv run python scripts/peachq/pre_push.py              # what the pre-push hook runs
    uv run python scripts/peachq/pre_push.py --base REF   # against another base
    SKIP=peachq-push git push                       # push without it

Standard library only. The hook runs it as `uv run python`, the project's
interpreter, never whichever python3 is first on PATH (#2449).
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import platform
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SUITES = "tests/q"
#: The suite that runs every `@eg` stating a value (.qt.man.examples).
EXAMPLES = "test_docstring_examples.q"
#: An `@eg` tag line, as that suite's own is_tag reads one: "/ @eg <expr>".
EG_TAG = re.compile(r"^\s*/\s*@eg\b")
BASE = "origin/master"
#: Seconds for the one PeachQ process; the selected suites usually take a few.
TIMEOUT = 900


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_git = _load("_git", "scripts/dev/_git.py")


def changed(base: str) -> list[str]:
    """Paths `base...HEAD` adds, modifies or renames into (not deletes)."""
    return _git.changed(f"{base}...HEAD", cwd=REPO, status=False, renames=True, diff_filter="AMR")


def q_diff(base: str) -> str:
    """The zero-context diff of the .q files `base...HEAD` adds or changes."""
    return subprocess.run(
        ["git", "diff", "-U0", "--diff-filter=AMR", f"{base}...HEAD", "--", "*.q"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def touches_eg(diff: str) -> bool:
    """Does this diff add or remove an `@eg` tag line? Prose that only
    mentions @eg, and the file headers, do not count."""
    return any(
        line[:1] in "+-" and not line.startswith(("+++", "---")) and EG_TAG.match(line[1:])
        for line in diff.splitlines()
        if line
    )


def select(paths: list[str], root: Path = REPO, examples: bool = False) -> list[str]:
    """The suite files to run, as bare names: every changed tests/q/test_*.q,
    the namesake suite of every changed production .q that has one, and the
    documented-examples suite when `examples` (an `@eg` line changed)."""
    picked: set[str] = {EXAMPLES} if examples else set()
    for path in paths:
        p = Path(path)
        if p.suffix != ".q":
            continue
        if p.parent.as_posix() == SUITES and p.name.startswith("test_"):
            picked.add(p.name)
        elif p.parts[0] in ("src", "scripts"):
            namesake = root / SUITES / f"test_{p.stem}.q"
            if namesake.is_file():
                picked.add(namesake.name)
    return sorted(n for n in picked if (root / SUITES / n).is_file())


def peachq() -> str:
    """$QCMD when it is PeachQ, else the pinned build - the q-docs-peachq rule."""
    pq = _load("peachq", "scripts/peachq/resolve.py")
    qcmd = os.environ.get("QCMD")
    if qcmd and pq.identify(qcmd) == "peachq":
        return qcmd
    return str(pq.resolve())


def read_results(path: Path) -> set[str]:
    """The full names of the tests that ran, from UQF_TEST_RESULTS."""
    if not path.exists():
        return set()
    names = set()
    for line in path.read_text().splitlines():
        suite, _, name = line.partition("\t")
        if name:
            names.add(name if name.startswith(".") else f"{suite}.{name}")
    return names


def run(base: str) -> int:
    try:
        paths = changed(base)
    except subprocess.CalledProcessError as e:
        print(f"peachq-push: cannot diff against {base}: {e.stderr.strip()} - not run")
        return 0
    try:
        examples = touches_eg(q_diff(base))
    except subprocess.CalledProcessError:
        examples = False
    files = select(paths, examples=examples)
    if not files:
        print(f"peachq-push: no q test file reached in {base}...HEAD - nothing to run on PeachQ")
        return 0
    suite = _load("full_suite", "scripts/peachq/full_suite.py")
    try:
        q = peachq()
    except Exception as e:  # PeachQError, or a build that could not start
        print(f"peachq-push: no PeachQ to run on: {e}", file=sys.stderr)
        print("  SKIP=peachq-push git push  skips it; CI still runs PeachQ", file=sys.stderr)
        return 1
    gaps = suite.read_gaps()
    joined = ",".join(files)
    print(f"peachq-push: {len(files)} suite file(s) on PeachQ: {joined}", flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        failures, results = Path(tmp) / "failures.txt", Path(tmp) / "results.txt"
        env = {
            **os.environ,
            "QCMD": q,
            "UQF_TEST_FAILURES": str(failures),
            "UQF_TEST_RESULTS": str(results),
        }
        cmd = [q, "tests/run_tests.q", "-q", "-files", joined]
        try:
            r = subprocess.run(
                cmd, cwd=REPO, env=env, capture_output=True, text=True, timeout=TIMEOUT, check=False
            )
            output = r.stdout + r.stderr
        except subprocess.TimeoutExpired:
            r, output = None, f"timed out after {TIMEOUT}s"
        repro = f"  reproduce: QCMD={q} {q} tests/run_tests.q -q -files {joined}"
        if not failures.exists():
            tail = "\n".join(output.strip().splitlines()[-25:])
            print(
                f"peachq-push: the suite did not finish loading on PeachQ:\n{tail}\n{repro}",
                file=sys.stderr,
            )
            return 1
        failed = suite.read_failures(failures)
        ran = read_results(results)
    new = sorted(set(failed) - set(gaps))
    fixed = sorted(n for n in set(gaps) & ran if n not in failed)
    here, recorded = f"{platform.system()}-{platform.machine()}", suite.read_platform()
    for name in fixed:
        print(
            f"peachq-push: {name} is a known gap but PASSES here ({here}; the list "
            f"records {recorded}) - if CI agrees, remove it from {suite.GAPS.name}"
        )
    if not new:
        print(
            f"peachq-push: passed - {len(ran)} tests ran, "
            f"{len(set(failed) & set(gaps))} known gap(s) failed as expected"
        )
        return 0
    print(
        f"peachq-push: {len(new)} test(s) FAILED on PeachQ that CI's PeachQ job will fail:",
        file=sys.stderr,
    )
    for name in new:
        print(f"  FAILED {name}  {failed[name]}", file=sys.stderr)
    print(repro, file=sys.stderr)
    print(
        "  Fix the code or the test. A genuine interpreter gap goes in "
        f"tests/q/{suite.GAPS.name} with its reason (`peachq-lacks: ...`).",
        file=sys.stderr,
    )
    print(
        "  SKIP=peachq-push git push  pushes anyway; CI's PeachQ job still decides.",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--base",
        default=BASE,
        help="the ref to diff against, three-dot (default origin/master)",
    )
    return run(p.parse_args(argv).base)


if __name__ == "__main__":
    sys.exit(main())
