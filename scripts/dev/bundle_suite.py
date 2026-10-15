"""Run each sidecar bundle's own q tests, installed into a throwaway tree.

A bundle (docs/guides/sidecar-bundles.md) carries its tests beside its jobs,
and installing it never copies them: a q test runs only from tests/q/, where
the tree's runner finds a suite by the tests it holds. So nothing ran a
bundle's tests. This does, without touching the checkout:

  1. copies the checkout's files (tracked, plus untracked ones git does not
     ignore) into a temporary directory;
  2. installs the bundle there with the real installer, as
     `uqs job install` would;
  3. copies the bundle's test_*.q into tests/q/ - there is no list to add them
     to, and a test that enters a namespace with `\\d` is refused (below);
  4. runs the WHOLE q suite there - which also proves the installed bundle
     leaves every other test passing.

    uv run python scripts/dev/bundle_suite.py                    # every bundle under sidecars/
    uv run python scripts/dev/bundle_suite.py sidecars/mockups   # one
    uv run python scripts/dev/bundle_suite.py --install-into DIR # install only, run nothing

Run through `python3 scripts/test.py bundles`. --install-into is how CI's
PeachQ lane (scripts/portable/full_suite.py --bundles) puts every bundle and
its tests into the tree it flattens, so the bundles are tested on every PR
on the q CI has (#925).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from uqs.paths import TEST_DIR
from uqs.scaffold import write
from uqs.scaffold.plan import FileAction, ScaffoldPlan
from uqs.stack import bundles

REPO = Path(__file__).resolve().parents[2]
SIDECARS = REPO / "sidecars"


def find_bundles(paths: list[str]) -> list[Path]:
    """The bundles named, or every bundle folder under sidecars/."""
    if paths:
        found = [Path(p).resolve() for p in paths]
    else:
        found = sorted(p.parent for p in SIDECARS.glob("*/" + bundles.MANIFEST))
    for folder in found:
        if not bundles.is_bundle(folder):
            raise SystemExit(f"{folder} holds no {bundles.MANIFEST} - it is not a bundle")
    return found


def copy_checkout(into: Path) -> None:
    """The checkout's files at `into`: tracked, and untracked but not ignored,
    so a bundle being written right now is tested as it stands."""
    listed = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        check=True,
    ).stdout.split(b"\0")
    for rel in (f.decode() for f in listed if f):
        src = REPO / rel
        if src.is_file():
            (into / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, into / rel)


def check_test(test: Path) -> None:
    """Refuse a bundle test that enters a namespace.

    Suites define everything from the root as `.qt.<owner>.<suite>.<name>`
    (tests/lib/discovery.q, #1412): q 4.0 rejects a nested `\\d`, and a suite
    declared that way would be found only by the transitional root-level rule.
    """
    for number, line in enumerate(test.read_text().splitlines(), start=1):
        if line.startswith("\\d .") and line.strip() != "\\d .":
            raise SystemExit(
                f"{test}:{number}: `{line.strip()}` enters a namespace - define "
                ".qt.<owner>.<suite>.<name> from the root instead"
            )


def install(folder: Path, tree: Path) -> list[str]:
    """Install the bundle at `folder` into `tree`, tests included; return the
    test files added to tests/q/."""
    bundle = bundles.read_bundle(folder)
    plan = bundles.plan(bundle, tree)
    bundles.install(plan, tree)
    actions = []
    for test in sorted(plan.tests):
        check_test(test)
        actions.append(FileAction(TEST_DIR / test.name, test.read_text()))
    write.apply_plan(ScaffoldPlan(bundle.name, actions), tree)
    return [a.path.name for a in actions]


def run(folder: Path, q: str, timeout: float) -> int:
    with tempfile.TemporaryDirectory(prefix="uqf-bundle-suite-") as tmp:
        tree = Path(tmp)
        copy_checkout(tree)
        try:
            added = install(folder, tree)
        except bundles.BundleError as exc:
            print(f"{folder.name}: does not install - {exc}", file=sys.stderr)
            return 1
        print(f"== {folder.name}: installed; its tests: {' '.join(added) or 'none'} ==", flush=True)
        if not added:
            print(f"{folder.name}: carries no test_*.q - nothing of its own was run", flush=True)
        r = subprocess.run(
            [q, "tests/run_tests.q", "-q"],
            cwd=tree,
            env=os.environ,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
        return r.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("bundles", nargs="*", help="bundle folders (default: sidecars/*)")
    parser.add_argument("--q", default=os.environ.get("QCMD") or "q", help="the q binary")
    parser.add_argument("--timeout", type=float, default=1200, help="seconds per bundle")
    parser.add_argument(
        "--install-into",
        type=Path,
        help="install every bundle, tests included, into this tree and run nothing",
    )
    args = parser.parse_args(argv)
    if args.install_into is not None:
        for folder in find_bundles(args.bundles):
            added = install(folder, args.install_into)
            print(f"{folder.name}: installed into {args.install_into}; tests {' '.join(added)}")
        return 0
    q = shutil.which(args.q)
    if q is None:
        raise SystemExit(f"no q at {args.q!r}: set QCMD or put q on PATH")
    found = find_bundles(args.bundles)
    if not found:
        print(f"no bundles under {SIDECARS.relative_to(REPO)}/ - nothing to test")
        return 0
    failed = [f.name for f in found if run(f, q, args.timeout) != 0]
    if failed:
        print(f"bundle suites failed: {', '.join(failed)}", file=sys.stderr)
        return 1
    print(f"{len(found)} bundle suite(s) passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
