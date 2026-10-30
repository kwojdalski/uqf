"""The plant schemas as q evaluates them (#1781): uqs.model.plant_metadata.

Python used to learn the plant's tables by matching `name:([]...)` in
plant_tables.q's text, so `name: ([]...)` - which q loads exactly the same -
lost the table from every reader while q still had it. These hold the
replacement: q exports what it evaluated, the JSON is checked current on every
read, and the readers answer from it.

Tests that need q take the `q_binary` fixture and skip without one; CI's
PeachQ lane runs them, since the exporter loads plant_tables.q alone.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from uqs.model import plant_metadata, plant_metadata_gen, schemas
from uqs.model.plant_metadata import (
    PLANT_SCHEMA_FILE,
    REGENERATE,
    VENDORED_DEFAULT,
    SchemaMetadataError,
)
from uqs.paths import CATALOG_FILE, SOURCE_DIR, STREAM_DIR, TABLES_FILE, TORQAPPHOME_ENV, WORKER_DIR

ROOT = Path(__file__).resolve().parents[3]
PACK = ROOT / VENDORED_DEFAULT.parent


def _spaced(text: str) -> str:
    """`name: ([]...)` for two tables - the spelling q accepts and the old
    text reader did not."""
    return re.sub(
        r"^\.qetl\.plant\.(fx_orderbook|trades):\(\[\]", r".qetl.plant.\1: ([] ", text, flags=re.M
    )


def _wrapped(text: str) -> str:
    """One definition broken across lines, as q reads an indented continuation."""
    return re.sub(
        r"^(\.qetl\.plant\.position_open:\(\[\])(time:`timestamp\$\(\); )",
        r"\1\n    \2\n    ",
        text,
        flags=re.M,
    )


def _tree(root: Path, text: str | None = None, *, metadata: bool = True) -> Path:
    """A tree holding plant_tables.q (`text`, default the real one) and, with
    `metadata`, this checkout's committed metadata."""
    (root / TABLES_FILE).parent.mkdir(parents=True, exist_ok=True)
    (root / TABLES_FILE).write_text(text if text is not None else (ROOT / TABLES_FILE).read_text())
    if metadata:
        (root / PLANT_SCHEMA_FILE).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / PLANT_SCHEMA_FILE, root / PLANT_SCHEMA_FILE)
    return root


@pytest.fixture
def pack(monkeypatch: pytest.MonkeyPatch) -> Path:
    """The real starter pack, named the way a tmp tree without lib/ finds it."""
    monkeypatch.setenv(TORQAPPHOME_ENV, str(PACK))
    return PACK


def _tables(root: Path) -> dict:
    return json.loads((root / PLANT_SCHEMA_FILE).read_text())["tables"]


# ------------------------------------------------- the demonstrated failure


def test_the_text_reader_loses_a_table_q_still_defines():
    """The failure #1781 starts from, kept as a fact about the TEXT reader that
    remains for a bundle's tables.q before install: it sees one spelling."""
    text = (ROOT / TABLES_FILE).read_text()
    lost = set(schemas.definitions_in(text)) - set(schemas.definitions_in(_spaced(text)))
    assert lost == {"fx_orderbook", "trades"}


def test_equivalent_formatting_produces_identical_metadata(tmp_path, q_binary, pack):
    plain = _tree(tmp_path / "plain", metadata=False)
    text = (ROOT / TABLES_FILE).read_text()
    other = _tree(tmp_path / "other", _wrapped(_spaced(text)), metadata=False)
    assert (other / TABLES_FILE).read_text() != text
    plant_metadata_gen.write(plain)
    plant_metadata_gen.write(other)
    assert _tables(plain) == _tables(other)
    # and every reader of the reformatted tree still has the tables
    meta = plant_metadata.load(other)
    assert {"fx_orderbook", "trades", "position_open"} <= set(meta.own())
    assert meta.table("trades").column_names[:2] == ["time", "sym"]


# ------------------------------------------------- what q evaluated, faithfully


def test_the_metadata_matches_q_introspection(tmp_path, q_binary, monkeypatch):
    """Each table's definition, rendered from the metadata, evaluates in q to
    the same meta - names, order, types, attributes - as the plant's own."""
    qbin, env = q_binary
    monkeypatch.delenv(TORQAPPHOME_ENV, raising=False)
    meta = plant_metadata.load()
    tables = {**meta.own(), **meta.vendored()}
    lines = [f"\\l {TABLES_FILE}", "bad:();"]
    for name, t in tables.items():
        lines.append(f"tmp:0#{t.definition().split(':', 1)[1]};")
        lines.append(f"if[not (meta tmp)~meta .qetl.plant.schema`{name}; bad,:enlist `{name}];")
        nested = "".join(t.nested[c] for c in sorted(t.nested))
        lines.append(
            f'if[not ((),"{nested}")~(),exec element from `column xasc select from '
            f".qetl.plant.elements where table=`{name}; bad,:enlist `{name}];"
        )
    lines += ['-1 "BAD:",", " sv string bad;', "exit 0"]
    script = tmp_path / "introspect.q"
    script.write_text("\n".join(lines) + "\n")
    r = subprocess.run(
        [qbin, str(script), "-q"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60
    )
    assert "BAD:" in r.stdout, r.stdout + r.stderr
    assert r.stdout.strip().splitlines()[-1] == "BAD:", r.stdout


def test_the_committed_metadata_is_current_without_q():
    """CI's lanes without q still catch a plant_tables.q committed without
    its metadata: the fingerprint, not q, says so."""
    meta = plant_metadata.load()
    assert set(meta.own()) == schemas.table_names()
    assert meta.table("mkt_orderbook").nested == {"bid_prices": "F", "ask_prices": "F"}
    assert meta.table("mkt_orderbook").definition() == (
        "mkt_orderbook:([]time:`timestamp$(); sym:`g#`symbol$(); bid_prices:(); ask_prices:())"
    )


def test_the_committed_metadata_is_what_q_makes(q_binary, monkeypatch):
    monkeypatch.delenv(TORQAPPHOME_ENV, raising=False)
    assert plant_metadata_gen.generate(ROOT) == (ROOT / PLANT_SCHEMA_FILE).read_text(), (
        f"stale - run `{REGENERATE}`"
    )


def test_sorting_and_nested_declarations_are_carried(tmp_path, q_binary, pack):
    text = (ROOT / TABLES_FILE).read_text() + ".qetl.plant.sorting[`trades;`sym`time!`p`];\n"
    root = _tree(tmp_path, text, metadata=False)
    plant_metadata_gen.write(root)
    meta = plant_metadata.load(root)
    assert meta.table("trades").sorting == (("sym", "p"), ("time", ""))
    assert meta.table("mkt_orderbook").nested == {"bid_prices": "F", "ask_prices": "F"}


def test_a_keyed_plant_table_is_refused_not_dropped(tmp_path, q_binary, pack):
    text = (ROOT / TABLES_FILE).read_text() + ".qetl.plant.kt:([k:`symbol$()] v:`long$())\n"
    root = _tree(tmp_path, text, metadata=False)
    with pytest.raises(SchemaMetadataError, match=r"keyed table\(s\) kt"):
        plant_metadata_gen.write(root)


def test_a_column_type_the_metadata_cannot_carry_is_refused():
    found = {
        "tables": [
            {"name": "t", "columns": [{"name": "px", "type": "F", "attribute": ""}]},
        ]
    }
    with pytest.raises(SchemaMetadataError, match=r"t\.px has q type 'F'.*\.qetl\.plant\.nested"):
        plant_metadata_gen._check(found)


# ------------------------------------------------- freshness


def test_missing_metadata_names_the_command(tmp_path):
    root = _tree(tmp_path, metadata=False)
    with pytest.raises(SchemaMetadataError, match=f"no plant schema metadata.*{REGENERATE}"):
        plant_metadata.load(root)


def test_stale_metadata_is_refused_with_regeneration_guidance(tmp_path):
    root = _tree(tmp_path)
    assert plant_metadata.load(root).own()
    plant = root / TABLES_FILE
    plant.write_text(plant.read_text() + ".qetl.plant.extra:([]time:`timestamp$())\n")
    with pytest.raises(SchemaMetadataError, match=f"is stale.*{re.escape(REGENERATE)}"):
        plant_metadata.load(root)


@pytest.mark.parametrize(
    ("damage", "why"),
    [
        (lambda t: "{not json", "is not valid"),
        (
            lambda t: t.replace('"exporter_version": 1', '"exporter_version": 0'),
            "exporter version 0",
        ),
        (lambda t: t.replace('"tables"', '"tablez"'), "is not valid"),
    ],
)
def test_invalid_metadata_is_refused(tmp_path, damage, why):
    root = _tree(tmp_path)
    path = root / PLANT_SCHEMA_FILE
    path.write_text(damage(path.read_text()))
    with pytest.raises(SchemaMetadataError, match=why):
        plant_metadata.load(root)


def test_another_starter_pack_is_never_answered_from_this_ones_tables(tmp_path, monkeypatch):
    """A pack the metadata was not made from (a server's own $TORQAPPHOME)
    needs q to evaluate it; without q that is refused, naming the command."""
    other = tmp_path / "database.q"
    other.write_text("quote:([]time:`timestamp$(); sym:`symbol$())\n")
    meta = plant_metadata.load()
    assert "quote" in meta.vendored(ROOT / VENDORED_DEFAULT)

    def no_q(root, vendored=None):
        raise SchemaMetadataError("no q interpreter")

    monkeypatch.setattr(plant_metadata_gen, "export", no_q)
    with pytest.raises(SchemaMetadataError, match=f"was not generated from.*{REGENERATE}"):
        meta.vendored(other)
    # this tree's own tables do not depend on it
    assert meta.table("trades")


def test_another_starter_pack_is_evaluated_by_q(tmp_path, q_binary):
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "database.q").write_text("custom:([]time:`timestamp$(); sym:`g#`symbol$())\n")
    found = plant_metadata.load().vendored(pack / "database.q")
    assert list(found) == ["custom"]
    assert [(c.name, c.type, c.attribute) for c in found["custom"].columns] == [
        ("time", "p", ""),
        ("sym", "s", "g"),
    ]


def test_a_failed_generation_leaves_the_previous_file(tmp_path, q_binary, pack):
    root = _tree(tmp_path)
    before = (root / PLANT_SCHEMA_FILE).read_bytes()
    plant = root / TABLES_FILE
    plant.write_text(plant.read_text() + ".qetl.plant.bad:([]time:`nosuchtype$())\n")
    with pytest.raises(SchemaMetadataError, match="q could not load"):
        plant_metadata_gen.write(root)
    assert (root / PLANT_SCHEMA_FILE).read_bytes() == before
    assert sorted(p.name for p in (root / PLANT_SCHEMA_FILE).parent.iterdir()) == [
        PLANT_SCHEMA_FILE.name
    ]
    with pytest.raises(SchemaMetadataError, match="is stale"):
        plant_metadata.load(root)


def test_inputs_changing_while_q_runs_are_refused(tmp_path, monkeypatch):
    root = _tree(tmp_path)
    found = {"tables": []}

    def export(where: Path) -> dict:
        (where / TABLES_FILE).write_text((where / TABLES_FILE).read_text() + "/ edited\n")
        return found

    monkeypatch.setattr(plant_metadata_gen, "export", export)
    before = (root / PLANT_SCHEMA_FILE).read_bytes()
    with pytest.raises(SchemaMetadataError, match="changed while its schemas were exported"):
        plant_metadata_gen.write(root)
    assert (root / PLANT_SCHEMA_FILE).read_bytes() == before


def test_ensure_runs_no_q_when_the_metadata_is_current(tmp_path, monkeypatch):
    root = _tree(tmp_path)
    monkeypatch.setattr(plant_metadata_gen, "export", lambda where: pytest.fail("ran q"))
    assert plant_metadata_gen.ensure(root) is False
    # and a stand-in tree with no metadata is left as it is
    bare = _tree(tmp_path / "bare", metadata=False)
    assert plant_metadata_gen.ensure(bare, create=False) is False


def test_offline_reads_need_no_q(tmp_path):
    """A release's ordinary commands read the shipped JSON: no q, no IPC."""
    env = {k: v for k, v in os.environ.items() if k not in ("QCMD", TORQAPPHOME_ENV)}
    env["PATH"] = str(tmp_path)  # nothing named q on it
    code = (
        "from uqs.model import schemas, plant_schema\n"
        "from uqs.paths import paths_for_root, repo_root\n"
        "from uqs.stack import hdb_shape\n"
        "assert 'trades' in schemas.table_names()\n"
        "assert schemas.columns('trades')[0] == 'time'\n"
        "assert 'quote' in hdb_shape.declared_tables(paths_for_root(repo_root(), 'uqf'))\n"
        "print('ok')\n"
    )
    r = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60
    )
    assert r.returncode == 0 and r.stdout.strip() == "ok", r.stderr


# ------------------------------------------------- bundle changes


def _repo(root: Path) -> Path:
    from uqs.stack.bundles import OVERRIDES_FILE

    for directory in (SOURCE_DIR, WORKER_DIR, STREAM_DIR):
        (root / directory).mkdir(parents=True)
    _tree(root)
    (root / CATALOG_FILE).parent.mkdir(parents=True)
    (root / CATALOG_FILE).write_text('.qcat.describe[`quote]:"a quote";\n')
    (root / OVERRIDES_FILE).parent.mkdir(parents=True, exist_ok=True)
    return root


def test_a_bundle_install_and_its_removal_regenerate_the_metadata(
    tmp_path, q_binary, pack, monkeypatch
):
    """The staged release's path (stack/bundle_build): install, then q
    re-evaluates; an upgrade that drops the table drops it from the metadata."""
    from test_bundles import make_bundle

    from uqs.stack import bundle_build

    root = _repo(tmp_path / "repo")
    plant_metadata_gen.write(root)
    monkeypatch.setattr(bundle_build, "repo_root", lambda: root)
    folder = make_bundle(tmp_path / "piggybank")
    bundle_build._install([str(folder)])
    meta = plant_metadata.load(root)
    assert meta.table("piggy_tape").column_names == ["time", "sym", "px"]
    assert meta.table("piggy_tape").nested == {"px": "F"}

    (folder / "tables.q").unlink()
    (folder / "catalog.q").unlink()
    (folder / "bundle.json").write_text('{"name": "piggybank", "version": "2.0.0"}')
    bundle_build._install([str(folder)])
    assert "piggy_tape" not in plant_metadata.load(root).own()


def test_every_scaffold_and_install_command_regenerates_it_first():
    """`uqs job new|remove|install` and `uqs runtime prepare` run _DERIVED in
    order; the metadata comes first, since the docs generator reads it."""
    from uqs.cli.regenerate import _DERIVED
    from uqs.model.plant_metadata import PLANT_SCHEMA_SCRIPT

    assert _DERIVED[0] == (str(PLANT_SCHEMA_SCRIPT),)
    assert (ROOT / PLANT_SCHEMA_SCRIPT).is_file()
