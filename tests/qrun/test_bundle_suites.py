"""Each sidecar bundle's own tests, with the bundle installed into a copy (#1808).

scripts/dev/bundle_suite.py is the backend, unchanged: it copies the checkout,
installs the bundle with the real installer, runs the bundle's Python tests and
then the WHOLE q suite in the copy - which also proves the installed bundle
leaves every other test passing. A bundle's tests never enter the tree, so
nothing else runs them on KDB-X (CI's PeachQ q job installs every bundle into
the tree it runs, scripts/portable/full_suite.py --bundles).

scripts/test.py's bundles lane until #1808: part of its release run `all`, and
so `-m release` here - a whole q suite per bundle is minutes each. One test per
bundle, so a failure names the bundle.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from qrun_support import assert_passed, q_env, run_process

from uqs.stack.bundles import MANIFEST

REPO = Path(__file__).resolve().parents[2]

#: A bundle installs and runs the whole q suite; past this it is stuck.
BUNDLE_TIMEOUT = 2400

#: Every bundle under sidecars/, read at collection: names only.
BUNDLES = sorted(p.parent for p in (REPO / "sidecars").glob(f"*/{MANIFEST}"))


@pytest.mark.release
@pytest.mark.kdbx
@pytest.mark.parametrize("bundle", BUNDLES, ids=lambda p: p.name)
def test_the_bundle_installs_and_its_tests_pass(bundle: Path, kdbx: Path) -> None:
    run = run_process(
        [sys.executable, "scripts/dev/bundle_suite.py", str(bundle), "--q", str(kdbx)],
        cwd=REPO, env=q_env(kdbx), timeout=BUNDLE_TIMEOUT, q_errors=False,
    )  # fmt: skip
    assert_passed(run, f"scripts/dev/bundle_suite.py {bundle.name}")


def test_every_bundle_is_collected() -> None:
    """Discovery parity: bundle_suite.py's own list, by its own rule."""
    assert BUNDLES, "sidecars/ holds no bundle"
    assert {p.name for p in BUNDLES} == {
        p.name for p in (REPO / "sidecars").iterdir() if (p / MANIFEST).is_file()
    }
