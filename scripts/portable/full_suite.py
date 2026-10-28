#!/usr/bin/env python3
"""The whole q suite on PeachQ, flattened, held to a list of known gaps (#863).

PeachQ rejects nested working contexts (`\\d .a.b`), so it cannot load this tree
as written. flatten_contexts.py rewrites a copy that needs none; this runs the
full suite - tests/run_tests.q, every suite - on that copy:

  1. flatten what still needs it (CONVERTED, now nothing): src/, scripts/,
     sidecars/ and tests/ open no nested context (check_nested_contexts.py), so
     every file loads on PeachQ exactly as written. That is also the nearest
     executable evidence, without a 4.0 binary, that the tests run on 4.0
     (#1412).
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
    python3 scripts/portable/full_suite.py --q "$PEACHQ_BIN" --shards 4   # in parallel
    python3 scripts/test.py q-unit-peachq      # the same, as a lane

With `--shards N` (#1694) the suite runs as N PeachQ processes at once, each
in its own copy of the tree, running a share of the tests/q/test_*.q files
through the runner's `-files` option. Every process still LOADS every suite
file - suites lean on each other's definitions (job_output_contracts drives
each job through the driver its own test file defines) - and runs only its
share. Every test file is in exactly one shard, and the shards' failures are
merged before the comparison below, which is unchanged.

The known-gaps file is one test per line, its full name, then `#` and the
reason it cannot pass on PeachQ, which starts with its kind (#986):

    .qt.qetl.io.test_hdb_appends_a_second_window  # peachq-lacks: appending a partitioned HDB

A reason that repeats one of the test's own messages says what the test
expects, not why PeachQ cannot do it, and is refused. The file also records
the platform its gaps were seen on - CI's - and on any other the comparison
is reported as not comparable rather than as a verdict (#968).

Standard library only.
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GAPS = REPO / "tests" / "q" / "peachq_known_gaps.txt"
FLATTENER = REPO / "scripts" / "portable" / "flatten_contexts.py"
BUNDLE_SUITE = REPO / "scripts" / "dev" / "bundle_suite.py"
#: What is flattened: nothing. src/, scripts/ and sidecars/ open no nested context
#: (#1411, check_nested_contexts.py) and neither does tests/ (#1412); every file loads
#: on PeachQ as it stands. lib/ is never flattened.
CONVERTED: tuple[str, ...] = ()
#: How a reason starts (#986): PeachQ lacks something, this tree relies on
#: something it should not, or the flattened copy fails on KDB-X too.
KINDS = ("peachq-lacks: ", "tree-bug: #", "flattening: ")
#: A test's message this long or longer, found in its gap's reason, is a
#: reason that restates the assertion. Shorter ones are words, not messages.
MIN_MESSAGE = 16


def read_gaps(path: Path = GAPS) -> dict[str, str]:
    """The known gaps: test name -> reason. A line without a reason is refused,
    because an entry nobody can explain is an entry nobody can remove."""
    gaps: dict[str, str] = {}
    for number, raw in enumerate(path.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith(("#", "platform:")):
            continue
        name, _, reason = line.partition("#")
        name, reason = name.strip(), reason.strip()
        if not name.startswith(".") or not reason:
            raise SystemExit(f"{path.name}:{number}: expected `.suite.test_name  # reason`")
        if not reason.startswith(KINDS):
            raise SystemExit(f"{path.name}:{number}: {name}'s reason must start with "
                             f"{', '.join(repr(k.strip()) for k in KINDS)} (#986)")  # fmt: skip
        if name in gaps:
            raise SystemExit(f"{path.name}:{number}: {name} is listed twice")
        gaps[name] = reason
    return gaps


def read_platform(path: Path = GAPS) -> str:
    """The platform the gaps were recorded on, from its one `platform:` line."""
    found = [ln.partition(":")[2].strip() for ln in path.read_text().splitlines()
             if ln.startswith("platform:")]  # fmt: skip
    if len(found) != 1 or not found[0]:
        raise SystemExit(f"{path.name}: expected one `platform: <system>-<machine>` line (#968)")
    return found[0]


def this_platform() -> str:
    return f"{platform.system()}-{platform.machine()}"


def messages_of(name: str, tests: Path = REPO / "tests" / "q") -> list[str]:
    """The string literals in the body of test `name` (`.suite.test_x`), read
    from its suite file: its assertions' messages among them.

    A suite defines `.qt.<owner>.<suite>.test_x:` from the root, so the test is
    found by its whole name at the start of a line.
    """
    for path in sorted(tests.glob("test_*.q")):
        body = None
        for line in path.read_text(errors="replace").splitlines():
            if body is not None:
                if line[:1] not in ("", " ", "\t", "/"):
                    break
                body.append(line)
            elif line.startswith(f"{name}:"):
                body = [line]
        if body is not None:
            found = re.findall(r'"((?:[^"\\]|\\.)*)"', "\n".join(body))
            return [m.replace('\\"', '"').replace("\\\\", "\\") for m in found]
    return []


def restated(gaps: dict[str, str], tests: Path = REPO / "tests" / "q") -> list[str]:
    """The gaps whose reason repeats one of their test's own messages (#986):
    what the test expects, recorded as if it were why PeachQ cannot pass it."""
    return sorted(
        name for name, reason in gaps.items()
        if any(len(m) >= MIN_MESSAGE and m in reason for m in messages_of(name, tests))
    )  # fmt: skip


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


def build_tree(into: Path, bundles: bool = False) -> None:
    """A runnable copy of the checkout, with CONVERTED flattened.
    With `bundles`, every bundle under sidecars/ is installed into the copy,
    its tests included, BEFORE flattening, so its jobs are converted and run
    like the tree's own (#925). Raises SystemExit when the flattener refuses."""
    tree = into / "tree"
    files = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z"], capture_output=True, check=True
    ).stdout.split(b"\0")
    for rel in (f.decode() for f in files if f):
        src = REPO / rel
        if src.is_file():
            (tree / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, tree / rel)
    if bundles:
        # uv, not this interpreter: the installer is uqs's, and CI's python3
        # is not the workspace's environment.
        r = subprocess.run(
            ["uv", "run", "--project", str(REPO), "python", str(BUNDLE_SUITE),
             "--install-into", str(tree)],
            cwd=REPO, capture_output=True, text=True, check=False,
        )  # fmt: skip
        if r.returncode:
            raise SystemExit(f"a bundle would not install:\n{(r.stdout + r.stderr).strip()}")
        print(r.stdout.strip(), flush=True)
    if not CONVERTED:
        return
    flat = into / "flat"
    r = subprocess.run(
        [sys.executable, str(FLATTENER), *CONVERTED, "--root", str(tree), "--out", str(flat),
         "--quiet"],
        cwd=tree, capture_output=True, text=True, check=False,
    )  # fmt: skip
    if r.returncode:
        tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-30:])
        raise SystemExit(f"the flattener refused - the tree must stay convertible:\n{tail}")
    shutil.copytree(flat, tree, dirs_exist_ok=True)


def assign(files: list[str], weights: dict[str, float], count: int) -> list[list[str]]:
    """`files` in `count` shards of near-equal weight: the heaviest first,
    each onto the least loaded shard (the lowest-numbered on a tie), so the
    split is the same on every run. Every file is in exactly one shard."""
    load = [0.0] * count
    shards: list[list[str]] = [[] for _ in range(count)]
    for name in sorted(set(files), key=lambda f: (-weights.get(f, 1.0), f)):
        target = min(range(count), key=lambda i: (load[i], i))
        shards[target].append(name)
        load[target] += weights.get(name, 1.0)
    return [sorted(shard) for shard in shards]


def shard_trees(work: Path, count: int) -> list[tuple[Path, list[str]]]:
    """`count` copies of work/tree, each with the test files it runs: a share
    of tests/q/test_*.q weighed by size, a stand-in for their run time. A copy
    each, so suites that write beside the tree never meet another shard's."""
    suites = work / "tree" / "tests" / "q"
    files = sorted(p.name for p in suites.glob("test_*.q"))
    weights = {name: float((suites / name).stat().st_size) for name in files}
    shards = []
    for index, mine in enumerate(assign(files, weights, count), start=1):
        tree = work / f"shard-{index}"
        shutil.copytree(work / "tree", tree, symlinks=True)
        shards.append((tree, mine))
    return shards


def run_shards(
    q: str, shards: list[tuple[Path, list[str]]], timeout: float, env: dict[str, str]
) -> dict[str, str] | None:
    """Run every shard at once; the merged failures, or None when a shard did
    not finish loading (its failures file was never written)."""
    procs = []
    for tree, mine in shards:
        failures = tree.parent / f"{tree.name}.failures.txt"
        failures.unlink(missing_ok=True)  # only this run's may count
        procs.append((tree, failures, subprocess.Popen(
            [q, "tests/run_tests.q", "-q", "-files", ",".join(mine)], cwd=tree,
            env={**env, "UQF_TEST_FAILURES": str(failures)},
        )))  # fmt: skip
    merged: dict[str, str] = {}
    missing = []
    for tree, failures, proc in procs:
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            code = "timed out"
        if not failures.exists():
            missing.append(f"{tree.name} (exit {code})")
            continue
        merged.update(read_failures(failures))
    if missing:
        print(f"these shards did not finish loading: {', '.join(missing)}", file=sys.stderr)
        return None
    return merged


def run(
    q: str, timeout: float, keep: Path | None = None, bundles: bool = False, shards: int = 1
) -> int:
    gaps = read_gaps()
    if bad := restated(gaps):
        raise SystemExit(f"{GAPS.name}: these reasons repeat their test's own message - say "
                         f"why PeachQ cannot pass it instead (#986): {', '.join(bad)}")  # fmt: skip
    recorded, here = read_platform(), this_platform()
    if recorded != here:
        print(f"NOT COMPARABLE: {GAPS.name} records {recorded} (CI), this is {here} - "
              "PeachQ behaves differently here, so no result below is CI's (#968)",
              flush=True)  # fmt: skip
    with tempfile.TemporaryDirectory() as tmp:
        work = keep or Path(tmp)
        work.mkdir(parents=True, exist_ok=True)
        print(f"flattening {', '.join(CONVERTED) or 'nothing'} into {work}", flush=True)
        build_tree(work, bundles)
        if shards > 1:
            env = {**os.environ, "QCMD": q}
            merged = run_shards(q, shard_trees(work, shards), timeout, env)
            return 1 if merged is None else report(merged, gaps, recorded, here)
        failures = work / "failures.txt"
        env = {**os.environ, "QCMD": q, "UQF_TEST_FAILURES": str(failures)}
        r = subprocess.run(
            [q, "tests/run_tests.q", "-q"], cwd=work / "tree", env=env, timeout=timeout, check=False
        )
        if not failures.exists():
            print(f"the suite exited {r.returncode} without writing its failures - it did not "
                  "finish loading", file=sys.stderr)  # fmt: skip
            return 1
        failed = read_failures(failures)
    return report(failed, gaps, recorded, here)


def report(failed: dict[str, str], gaps: dict[str, str], recorded: str, here: str) -> int:
    """Print the comparison and give the lane's exit code. Off CI's platform
    there is no verdict: a difference there may not be one on CI (#968)."""
    comparable = recorded == here
    new, fixed = compare(failed, gaps)
    for name in new:
        tag = "NEW FAILURE " if comparable else "FAILS HERE  "
        print(f"{tag} {name}  {failed[name]}", file=sys.stderr)
    for name in fixed:
        tag = "NOW PASSES   " if comparable else "PASSES HERE  "
        print(f"{tag}{name}  - remove it from {GAPS.name} ({gaps[name]})", file=sys.stderr)
    print(f"{len(failed)} not passing, {len(gaps)} known gaps: {len(new)} new, {len(fixed)} fixed",
          flush=True)  # fmt: skip
    if not comparable:
        print(f"NOT COMPARABLE on {here}: only CI's {recorded} lane decides - exiting 0 (#968)",
              flush=True)  # fmt: skip
        return 0
    return 1 if new or fixed else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--q", default=os.environ.get("QCMD", "q"), help="the PeachQ binary")
    p.add_argument("--timeout", type=float, default=1800, help="seconds for the suite")
    p.add_argument("--keep", type=Path, help="build the tree here and leave it, to debug")
    p.add_argument("--bundles", action="store_true", help="install sidecars/* bundles first (#925)")
    p.add_argument("--shards", type=int, default=1,
                   help="run the suite as this many processes at once (#1694)")  # fmt: skip
    a = p.parse_args(argv)
    if a.shards < 1:
        p.error("--shards must be at least 1")
    return run(a.q, a.timeout, a.keep, a.bundles, a.shards)


if __name__ == "__main__":
    raise SystemExit(main())
