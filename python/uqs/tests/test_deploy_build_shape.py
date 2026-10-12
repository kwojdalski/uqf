"""The manifest's `hdb_shape` describes the release being built (#1158,
#1162, #1163).

`uqs deploy build` composes the schema of a STAGED tree - the checkout with
its bundles installed, its q converted - and that tree never carries lib/.
So the base schema comes from the configured starter pack (the builder's
vendored one by default), and everything else from the staged tree: uqf's
and the bundles' tables, and which of them a runtime holds - its job graph
and its bundle ledger. That is why the shape is composed by the staged
tree's own uqs, in a subprocess, and never by the builder's.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from uqs.deploy import payload
from uqs.deploy.build import declared_shape, stage_bundles
from uqs.paths import BUNDLE_LEDGER, TABLES_FILE, TORQAPPHOME_ENV, repo_root

ROOT = repo_root()

#: A table only the staged tree declares, as an installed bundle's would be.
STAGED_ONLY = "staged_only_tbl"
STAGED_DEF = f"{STAGED_ONLY}:([]time:`timestamp$(); sym:`symbol$(); px:`float$())"


def _copy_tree(into: Path) -> Path:
    """A staged copy of this checkout's release files - no lib/, as a
    release never has one."""
    for rel in payload.tracked_files(ROOT):
        (into / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, into / rel)
    assert not (into / "lib").exists()
    return into


@pytest.fixture
def staged(tmp_path: Path) -> Path:
    """A staged release tree: this checkout plus one extra table, no lib/."""
    tree = _copy_tree(tmp_path / "tree")
    target = tree / TABLES_FILE
    target.write_text(target.read_text() + STAGED_DEF + "\n")
    return tree


def test_a_table_only_the_staged_tree_declares_is_in_the_shape(staged, monkeypatch):
    monkeypatch.delenv(TORQAPPHOME_ENV, raising=False)
    shape = declared_shape(staged, "uqf", ROOT)
    assert shape is not None, "no hdb_shape for a staged tree without lib/"
    assert shape[STAGED_ONLY] == ["time", "sym", "px"]


def test_the_starter_pack_comes_from_the_builder_checkout(staged, monkeypatch):
    """No TORQAPPHOME: the vendored starter pack's tables are there anyway."""
    monkeypatch.delenv(TORQAPPHOME_ENV, raising=False)
    shape = declared_shape(staged, "uqf", ROOT)
    assert shape is not None
    assert {"quote", "trade"} <= set(shape)


def test_an_explicit_starter_pack_is_still_respected(staged, tmp_path, monkeypatch):
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "database.q").write_text("custom_base:([]time:`timestamp$(); sym:`symbol$())\n")
    monkeypatch.setenv(TORQAPPHOME_ENV, str(pack))
    shape = declared_shape(staged, "uqf", ROOT)
    assert shape is not None
    assert shape["custom_base"] == ["time", "sym"]
    assert "quote" not in shape
    assert STAGED_ONLY in shape


def test_the_vendored_only_runtime_reads_the_builders_starter_pack(staged, monkeypatch):
    """torq declares no overlays: its schema is the starter pack whole."""
    monkeypatch.delenv(TORQAPPHOME_ENV, raising=False)
    shape = declared_shape(staged, "torq", ROOT)
    assert shape is not None
    assert {"quote", "trade"} <= set(shape)
    assert STAGED_ONLY not in shape


def test_q_4_0_converted_declarations_are_read(staged, monkeypatch):
    """--q-target 4.0 qualifies plant_tables.q's names under `.qetl.plant`
    (#861); the shape still has every table, under its own name."""
    from uqs.deploy import portable
    from uqs.model import schemas

    portable.convert(staged, [str(TABLES_FILE)], "4.0", [], False)
    text = (staged / TABLES_FILE).read_text()
    assert f".qetl.plant.{STAGED_ONLY}:([]" in text, "the converter no longer qualifies names"
    monkeypatch.delenv(TORQAPPHOME_ENV, raising=False)
    shape = declared_shape(staged, "uqf", ROOT)
    assert shape is not None
    assert shape[STAGED_ONLY] == ["time", "sym", "px"]
    assert schemas.table_names() | {STAGED_ONLY} <= set(shape)


def test_a_shape_that_cannot_be_composed_is_none_not_a_failed_build(staged, monkeypatch):
    """A broken staged tree costs the manifest its hdb_shape, with a
    warning - push then judges the HDB in prepare only."""
    monkeypatch.delenv(TORQAPPHOME_ENV, raising=False)
    (staged / BUNDLE_LEDGER).write_text("not json\n")
    assert declared_shape(staged, "uqf", ROOT) is None


# ------------------------------------------- runtime membership (#1162)

STREAM = """\
/ piggy_spread.q - a sidecar bundle's streaming job, for the shape test.
.qetl.job.stream.define[`piggy_spread;
    `procname`subscribe_to`publishes`note!(`piggy_spread1;enlist `quote;enlist `piggy_quote;
        "copies quotes into piggy_quote")];
\\d .qpipe.job.piggy_spread
upd:{[t;x] .qpipe.job.piggy_spread.publish[`piggy_quote;x]}
\\d .
"""


def _staged_with_bundle(tmp_path: Path, runtime: str) -> Path:
    """This checkout staged with one bundle installed for `runtime` by the
    real installer - its ledger entry names that runtime and its table."""
    b = tmp_path / "piggybank"
    b.mkdir()
    (b / "bundle.json").write_text('{"name": "piggybank", "version": "1.0.0"}')
    (b / "piggy_spread.q").write_text(STREAM)
    (b / "tables.q").write_text("piggy_quote:([]time:`timestamp$();sym:`symbol$())\n")
    (b / "catalog.q").write_text('.qcat.describe[`piggy_quote]:"quotes, for the piggybank";\n')
    staged = tmp_path / "staged"
    _, record = stage_bundles(ROOT, payload.tracked_files(ROOT), [str(b)], staged, runtime=runtime)
    assert record["piggybank"]["runtimes"] == [runtime]
    assert record["piggybank"]["tables"] == ["piggy_quote"]
    return staged


def test_a_bundle_in_a_profile_runtime_has_its_table_in_that_runtimes_shape(tmp_path, monkeypatch):
    """The builder's own ledger does not list the bundle: membership must be
    read from the staged tree's, or crypto's shape omits piggy_quote."""
    monkeypatch.delenv(TORQAPPHOME_ENV, raising=False)
    staged = _staged_with_bundle(tmp_path, "crypto")
    shape = declared_shape(staged, "crypto", ROOT)
    assert shape is not None
    assert shape["piggy_quote"] == ["time", "sym"]


def test_a_bundle_named_only_for_fx_is_not_in_the_uqf_shape(tmp_path, monkeypatch):
    """uqf has every pipeline but the bundles that do not name it."""
    monkeypatch.delenv(TORQAPPHOME_ENV, raising=False)
    staged = _staged_with_bundle(tmp_path, "fx")
    uqf = declared_shape(staged, "uqf", ROOT)
    assert uqf is not None
    assert "piggy_quote" not in uqf
    assert {"quote", "trade"} <= set(uqf)
    fx = declared_shape(staged, "fx", ROOT)
    assert fx is not None and "piggy_quote" in fx
