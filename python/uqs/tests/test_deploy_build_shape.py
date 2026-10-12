"""The manifest's `hdb_shape` describes the release being built (#1158).

`uqs deploy build` composes the schema from a STAGED tree - the checkout
with its bundles installed, its q converted - and that tree never carries
lib/. So the base schema comes from the configured starter pack (the
builder's vendored one by default), and uqf's and the bundles' tables from
the staged tree, not from whatever checkout uqs was imported from.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from uqs.deploy.build import declared_shape
from uqs.paths import TABLES_FILE, TORQAPPHOME_ENV, repo_root

#: A table only the staged tree declares, as an installed bundle's would be.
STAGED_ONLY = "staged_only_tbl"
STAGED_DEF = f"{STAGED_ONLY}:([]time:`timestamp$(); sym:`symbol$(); px:`float$())"


@pytest.fixture
def staged(tmp_path: Path) -> Path:
    """A staged release tree: plant_tables.q plus one extra table, no lib/."""
    tree = tmp_path / "tree"
    target = tree / TABLES_FILE
    target.parent.mkdir(parents=True)
    target.write_text((repo_root() / TABLES_FILE).read_text() + STAGED_DEF + "\n")
    assert not (tree / "lib").exists()
    return tree


def test_a_table_only_the_staged_tree_declares_is_in_the_shape(staged, monkeypatch):
    monkeypatch.delenv(TORQAPPHOME_ENV, raising=False)
    shape = declared_shape(staged, "uqf", repo_root())
    assert shape is not None, "no hdb_shape for a staged tree without lib/"
    assert shape[STAGED_ONLY] == ["time", "sym", "px"]


def test_the_starter_pack_comes_from_the_builder_checkout(staged, monkeypatch):
    """No TORQAPPHOME: the vendored starter pack's tables are there anyway."""
    monkeypatch.delenv(TORQAPPHOME_ENV, raising=False)
    shape = declared_shape(staged, "uqf", repo_root())
    assert shape is not None
    assert {"quote", "trade"} <= set(shape)


def test_an_explicit_starter_pack_is_still_respected(staged, tmp_path, monkeypatch):
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "database.q").write_text("custom_base:([]time:`timestamp$(); sym:`symbol$())\n")
    monkeypatch.setenv(TORQAPPHOME_ENV, str(pack))
    shape = declared_shape(staged, "uqf", repo_root())
    assert shape is not None
    assert shape["custom_base"] == ["time", "sym"]
    assert "quote" not in shape
    assert STAGED_ONLY in shape


def test_the_vendored_only_runtime_reads_the_builders_starter_pack(staged, monkeypatch):
    """torq declares no overlays: its schema is the starter pack whole."""
    monkeypatch.delenv(TORQAPPHOME_ENV, raising=False)
    shape = declared_shape(staged, "torq", repo_root())
    assert shape is not None
    assert {"quote", "trade"} <= set(shape)
    assert STAGED_ONLY not in shape
