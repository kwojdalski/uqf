#!/usr/bin/env python3
"""Run the whole q suite under coverage on master's tip, on KDB-X (#1719).

An attestation runs q-coverage over a SUBSET when the change reaches one
(scripts/dev/test_impact.py's coverage_for): q-tests' suites and a suite that
enters each judged function, judging only the production files the change
reaches. What that gives up - a function in a module the change does not
reach that went uncovered, a baseline entry only an outside suite now enters,
a test outside the selection that fails - is what a whole run catches. Hosted
CI has no KDB-X, so nothing runs it on master by itself: this is that run.

WHERE IT RUNS. scripts/dev/ship.sh starts it in the background after every
merge it makes (`--if-new`, so a tip already judged is not run again, and a
lock so two merges do not run it twice at once). It can also be scheduled
nightly on a machine with KDB-X:

    0 3 * * * cd ~/github_projects/uqf && python3 scripts/dev/master_coverage.py --if-new

and every attestation that runs a subset prints its last verdict
(status_line), so a failing or missing run is seen where subsets are made.

It checks `--ref` (default origin/master, fetched first) out into a
throwaway detached worktree, so it never touches a checkout in use, and runs
there what an attestation runs when the selector says ALL:

    pytest -m coverage tests/qrun/test_q_coverage.py --q-coverage-map ...
                                     every function judged against
                                     tests/q/coverage_baseline.txt
    and every test's outcome from that same run, as q-tests' q-unit verdict

The same run writes the coverage map - which suite files enter each function
- and compares it with the tree's tests/q/coverage_map.txt. A stale map is
not a failure (a subset then hands more verdicts to a whole run, which is
slow, never wrong); `--write-map PATH` saves the fresh one to commit.

The verdict is kept in the git directory (uqf-master-coverage/last.json),
shared by every worktree. Exit 0 when both pass, 1 when either fails - the
output says which - and 2 when the ref cannot be checked out.

    python3 scripts/dev/master_coverage.py [--ref REF] [--no-fetch] [--if-new]
                                           [--write-map PATH]
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MAP = "tests/q/coverage_map.txt"
RUN_HINT = "python3 scripts/dev/master_coverage.py"


def git(*args: str, cwd: Path = REPO) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def common_dir(root: Path = REPO) -> Path:
    """The git directory every worktree shares."""
    common = git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=root)
    return Path(common.stdout.strip()) if common.returncode == 0 else root / ".git"


def state_dir(root: Path = REPO) -> Path:
    """Where the verdict is kept: in the git directory every worktree shares,
    so it outlives the worktree that started the run."""
    return common_dir(root) / "uqf-master-coverage"


def read_last(root: Path = REPO) -> dict | None:
    try:
        last = json.loads((state_dir(root) / "last.json").read_text())
    except OSError, ValueError:
        return None
    return last if isinstance(last, dict) else None


def status_line(root: Path = REPO, now: dt.datetime | None = None) -> str:
    """One line on the last whole-suite coverage run on master's tip."""
    last = read_last(root)
    if not last or "sha" not in last:
        return (
            "the whole-suite coverage run on master has not run on this machine - "
            f"`{RUN_HINT}` runs it (ship.sh starts it after each merge)"
        )
    when = ""
    try:
        finished = dt.datetime.fromisoformat(last["finished"])
        hours = ((now or dt.datetime.now(dt.UTC)) - finished).total_seconds() / 3600
        when = f" {hours:.0f}h ago" if hours >= 1 else " under an hour ago"
    except KeyError, TypeError, ValueError:
        pass
    verdict = last.get("verdict", "?")
    line = f"the whole-suite coverage run on master {str(last['sha'])[:12]}{when}: {verdict}"
    if verdict != "passed":
        line += f" - see {state_dir(root) / 'log'}, and fix master"
    drift = last.get("map_drift")
    if drift:
        line += f"; {MAP} differs from it in {drift} line(s) - `{RUN_HINT} --write-map {MAP}`"
    return line


def map_drift(fresh: Path, committed: Path) -> int:
    """Lines in one map and not the other, comments aside."""

    def rows(p: Path) -> set[str]:
        try:
            text = p.read_text()
        except OSError:
            return set()
        return {ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")}

    return len(rows(fresh) ^ rows(committed))


def _attest(tree: Path):
    """The tree's own attest_kdbx.py - outcome_env and unit_verdict."""
    spec = importlib.util.spec_from_file_location(
        "attest_kdbx_for_master", tree / "scripts/dev/attest_kdbx.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(tree: Path, scratch: Path) -> tuple[int, Path]:
    """The whole-suite coverage gate in `tree`, and q-unit's verdict from it:
    (0 or 1, where the run wrote the coverage map)."""
    attest = _attest(tree)
    outcomes = scratch / "outcomes"
    outcomes.mkdir()
    fresh = scratch / "coverage_map.txt"
    env = {**os.environ, **attest.outcome_env(outcomes)}
    cov = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-m",
            "coverage",
            "--require-q",
            "kdbx",
            "tests/qrun/test_q_coverage.py",
            "--q-coverage-map",
            str(fresh),
        ],
        cwd=tree,
        env=env,
        check=False,
    )
    why = attest.unit_verdict(outcomes)
    if why:
        print(f"master_coverage: q-unit failed: {why}", file=sys.stderr)
    if cov.returncode:
        print("master_coverage: q-coverage failed - see above", file=sys.stderr)
    return (1 if cov.returncode or why else 0), fresh


@contextlib.contextmanager
def _lock(state: Path):
    """Held for the run; None when another run holds it."""
    state.mkdir(parents=True, exist_ok=True)
    with open(state / "lock", "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield None
            return
        yield fh


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument(
        "--ref", default="origin/master", help="what to run (default origin/master)"
    )
    parser.add_argument("--no-fetch", action="store_true", help="do not `git fetch origin` first")
    parser.add_argument(
        "--if-new", action="store_true", help="skip a commit the last run already judged"
    )
    parser.add_argument("--write-map", metavar="PATH", help="save the coverage map the run wrote")
    args = parser.parse_args(argv)
    state = state_dir()
    with _lock(state) as held:
        if held is None:
            print("master_coverage: another run holds the lock - nothing to do")
            return 0
        # From the git directory, not this checkout: ship.sh starts this from
        # a worktree that may be removed while it runs.
        common = common_dir()
        if not args.no_fetch:
            git("fetch", "-q", "origin", cwd=common)
        sha = git("rev-parse", "--verify", f"{args.ref}^{{commit}}", cwd=common)
        if sha.returncode:
            print(f"master_coverage: no commit {args.ref}: {sha.stderr.strip()}", file=sys.stderr)
            return 2
        commit = sha.stdout.strip()
        last = read_last()
        if args.if_new and last and last.get("sha") == commit:
            print(f"master_coverage: {commit[:12]} already judged: {last.get('verdict')}")
            return 0 if last.get("verdict") == "passed" else 1
        with tempfile.TemporaryDirectory(prefix="master_coverage.") as tmp:
            tree = Path(tmp) / "tree"
            added = git("worktree", "add", "--detach", str(tree), commit, cwd=common)
            if added.returncode:
                print(
                    f"master_coverage: cannot check out {commit}: {added.stderr}", file=sys.stderr
                )
                return 2
            try:
                print(f"master_coverage: {args.ref} = {commit[:12]}, the whole suite", flush=True)
                scratch = Path(tmp) / "scratch"
                scratch.mkdir()
                code, fresh = run(tree, scratch)
                drift = map_drift(fresh, tree / MAP) if fresh.exists() else None
                if args.write_map and fresh.exists():
                    shutil.copyfile(fresh, args.write_map)
                    print(f"master_coverage: wrote the coverage map to {args.write_map}")
            finally:
                git("worktree", "remove", "--force", str(tree), cwd=common)
        verdict = "passed" if code == 0 else "FAILED"
        record = {
            "sha": commit,
            "verdict": verdict,
            "finished": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            "map_drift": drift,
        }
        (state / "last.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"master_coverage: {commit[:12]}: whole-suite coverage and q-unit {verdict}")
    if drift:
        print(f"master_coverage: {MAP} differs from this run's map in {drift} line(s)")
    return code


if __name__ == "__main__":
    sys.exit(main())
