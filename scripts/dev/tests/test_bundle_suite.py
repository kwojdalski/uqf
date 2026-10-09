"""scripts/dev/bundle_suite.py: installing a bundle's tests into a throwaway tree.

The q run itself is the `bundles` lane's; these hold the part that decides
WHAT runs - a test the lane copied but never listed would load and run nothing.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from uqs.paths import CATALOG_FILE, RUN_TESTS_FILE, SOURCE_DIR, TABLES_FILE, TEST_DIR

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
    (root / RUN_TESTS_FILE).write_text("nsList:`.covtest;\n")
    return root


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    folder = tmp_path / "mockups"
    folder.mkdir()
    (folder / "bundle.json").write_text(json.dumps({"name": "mockups", "version": "0.1.0"}))
    (folder / "mock.q").write_text("source_name:`mock\n.qetl.source.define[source_name;()!()];\n")
    (folder / "test_mock.q").write_text("/ the mock source\n\\d .mocktest\ntest_x:{[t] }\n")
    return folder


def test_a_bundle_test_is_copied_and_listed(tree: Path, bundle: Path) -> None:
    assert suite.install(bundle, tree) == ["`.mocktest"]
    assert (tree / TEST_DIR / "test_mock.q").is_file()
    assert (tree / RUN_TESTS_FILE).read_text() == "nsList:`.covtest`.mocktest;\n"


def test_a_test_with_no_namespace_is_refused(tmp_path: Path) -> None:
    test = tmp_path / "test_x.q"
    test.write_text("/ no \\d line\n")
    with pytest.raises(SystemExit, match="no `\\\\d .namespace` line"):
        suite.namespace(test)


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
