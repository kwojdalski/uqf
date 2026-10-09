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
reason it cannot pass on PeachQ, which starts with its kind (#986):

    .iotest.test_hdb_appends_a_second_window  # peachq-lacks: appending a partitioned HDB

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
#: What is flattened. lib/ is not: the flattener leaves vendored code alone.
CONVERTED = ("src", "scripts", "tests")
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
    from its suite file: its assertions' messages among them."""
    suite, _, test = name.rpartition(".")
    for path in sorted(tests.glob("test_*.q")):
        ns, body = None, None
        for line in path.read_text(errors="replace").splitlines():
            if body is not None:
                if line[:1] not in ("", " ", "\t", "/"):
                    break
                body.append(line)
            elif line.startswith("\\d "):
                ns = line[3:].strip()
            elif ns == suite and line.startswith(f"{test}:"):
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
    """A runnable copy of the checkout with src/, scripts/ and tests/ flattened.
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


def run(q: str, timeout: float, keep: Path | None = None, bundles: bool = False) -> int:
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
        print(f"flattening {', '.join(CONVERTED)} into {work}", flush=True)
        build_tree(work, bundles)
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
    a = p.parse_args(argv)
    return run(a.q, a.timeout, a.keep, a.bundles)


if __name__ == "__main__":
    raise SystemExit(main())
