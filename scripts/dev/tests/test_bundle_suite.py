"""scripts/dev/bundle_suite.py: installing a bundle's tests into a throwaway tree.

The q run itself is the `bundles` lane's; these hold the part that decides
WHAT runs - a test the lane copied must be one the runner can find.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from uqs.paths import CATALOG_FILE, SOURCE_DIR, TABLES_FILE, TEST_DIR

_SPEC = importlib.util.spec_from_file_location(
    "bundle_suite", Path(__file__).resolve().parents[1] / "bundle_suite.py"
)
assert _SPEC and _SPEC.loader
suite = importlib.util.module_from_spec(_SPEC)
sys.modules["bundle_suite"] = suite
_SPEC.loader.exec_module(suite)


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    (root / SOURCE_DIR).mkdir(parents=True)
    (root / TABLES_FILE).write_text("\\d .qetl.plant\n")
    (root / CATALOG_FILE).parent.mkdir(parents=True)
    (root / CATALOG_FILE).write_text("\\d .qcat\n")
    (root / "python/uqs").mkdir(parents=True)
    (root / TEST_DIR).mkdir(parents=True)
    return root


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    folder = tmp_path / "mockups"
    folder.mkdir()
    (folder / "bundle.json").write_text(json.dumps({"name": "mockups", "version": "0.1.0"}))
    (folder / "mock.q").write_text("source_name:`mock\n.qetl.source.define[source_name;()!()];\n")
    (folder / "test_mock.q").write_text("/ the mock source\n.qt.qpipe.job.mock.test_x:{[t] }\n")
    return folder


def test_a_bundle_test_is_copied_and_nothing_is_listed(tree: Path, bundle: Path) -> None:
    assert suite.install(bundle, tree) == ["test_mock.q"]
    assert (tree / TEST_DIR / "test_mock.q").is_file()
    assert not (tree / "tests/run_tests.q").exists(), "no runner file is edited: suites are found"


def test_a_test_that_enters_a_namespace_is_refused(tmp_path: Path) -> None:
    test = tmp_path / "test_x.q"
    test.write_text("/ old style\n\\d .xtest\ntest_a:{[t] }\n\\d .\n")
    with pytest.raises(SystemExit, match="enters a namespace"):
        suite.check_test(test)


def test_a_test_that_defines_from_the_root_is_accepted(tmp_path: Path) -> None:
    test = tmp_path / "test_x.q"
    test.write_text(".qt.qpipe.job.x.test_a:{[t] }\n")
    suite.check_test(test)


def test_a_folder_without_a_manifest_is_not_a_bundle(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="not a bundle"):
        suite.find_bundles([str(tmp_path)])


def test_every_sidecar_bundle_carries_its_own_tests() -> None:
    """#925: CI runs each bundle's tests (full_suite.py --bundles), so a bundle
    without any would be installed, converted and checked by nothing."""
    found = suite.find_bundles([])
    assert found, "sidecars/ holds no bundle - the lane would test nothing"
    bare = [f.name for f in found if not list(f.glob("test_*.q"))]
    assert not bare, f"bundle(s) with no test_*.q: {bare}"


def test_a_bundles_python_tests_go_beside_uqs_own(tree: Path, bundle: Path) -> None:
    """#1776: a bundle's test_*.py run with pytest in the throwaway tree, beside
    uqs's tests so its conftest applies - and its connectors are not tests."""
    (bundle / "mock_feed.py").write_text("FEED = None\n")
    (bundle / "test_mock_feed.py").write_text("def test_x():\n    pass\n")
    copied = suite.python_tests(bundle, "mockups", tree)
    assert copied == [tree / suite.PY_TEST_DIR / "bundle_mockups" / "test_mock_feed.py"]
    assert copied[0].is_file()


def test_every_moved_connector_test_is_a_bundles() -> None:
    """The databento and flink connectors' tests moved with them (#1776): the
    lane runs them, so they must be where it looks."""
    for name, test in (("databento", "test_databento_feed.py"),
                       ("flink", "test_flink_vwap_streamer.py")):  # fmt: skip
        assert (suite.SIDECARS / name / test).is_file(), f"{name} lost {test}"
