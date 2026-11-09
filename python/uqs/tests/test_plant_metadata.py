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


# ------------------------------------------------- the scaffold's schema, round trip

#: `--schema` specs at the boundaries the mini-language has: time omitted and
#: explicit, `sym` grouped by default and spelled `g#`, grouped columns of every
#: other type, no `sym`, a lone `time`, one and several list columns.
_SPECS = {
    "rt_every_type": "sym:symbol, f:float, j:long, i:int, h:short, b:boolean, c:char, "
    "d:date, t:time, n:timespan, p:timestamp",
    "rt_explicit_time": "time:timestamp, sym:g#symbol, px:float",
    "rt_grouped": "venue:g#symbol, f:g#float, j:g#long, c:g#char, d:g#date",
    "rt_no_sym": "venue:symbol, sym_count:long",
    "rt_time_only": "time:timestamp",
    "rt_one_list": "sym:symbol, levels:list",
    "rt_lists": "sym:symbol, bids:list, px:float, asks:list",
}


def _scaffold_tables(root: Path, shapes: dict) -> dict[str, str]:
    """Append each table as `uqs job new` writes it, regenerate the metadata
    the way the scaffold then does, and return {table: the block written}."""
    from uqs.scaffold import jobs

    written = {}
    for table, shape in shapes.items():
        plan = jobs.streaming_job(table, [], table, shape)
        (block,) = [a.body for a in plan.actions if a.path == TABLES_FILE]
        written[table] = block
        with (root / TABLES_FILE).open("a") as fh:
            fh.write(block)
    plant_metadata_gen.write(root)
    return written


def test_a_scaffolded_schema_survives_two_cycles_through_q(tmp_path, q_binary, pack):
    """spec0 -> plant_tables.q -> q -> --schema-from -> plant_tables.q -> q -> ...

    Each cycle copies the previous generation's tables by `--schema-from`,
    which reads what q evaluated (plant_definitions). Every generation must hold
    the same columns, types, attributes and nested columns as spec0 asked for,
    write the same block under its new name, and say the same `--schema`."""
    from uqs.scaffold.columns import columns_spec, parse_columns, resolve_shape, table_columns

    root = _tree(tmp_path, metadata=False)
    blocks = [_scaffold_tables(root, _SPECS)]
    for gen in (1, 2):
        own = plant_metadata.load(root).own()
        prev = {f"{t}_c{gen - 1}" if gen > 1 else t: t for t in _SPECS}
        shapes = {f"{t}_c{gen}": resolve_shape(None, name, own) for name, t in prev.items()}
        blocks.append(_scaffold_tables(root, shapes))

    meta = plant_metadata.load(root)
    for table, spec0 in _SPECS.items():
        cols0 = parse_columns(spec0)
        names = (table, f"{table}_c1", f"{table}_c2")
        evaluated = [meta.table(n) for n in names]
        shapes = [[(c.name, c.type, c.attribute) for c in t.columns] for t in evaluated]
        assert shapes[0] == shapes[1] == shapes[2], table
        assert evaluated[0].nested == evaluated[1].nested == evaluated[2].nested, table
        assert {c for c, lit in cols0 if lit == "()"} == set(evaluated[0].nested), table
        # what q evaluated is what spec0 asked for, and says spec0 again
        for t in evaluated:
            assert table_columns(t.definition()) == cols0, t.name
            assert columns_spec(table_columns(t.definition())) == columns_spec(cols0), t.name
        # the generator is stable: each copy writes the same block, and the
        # same as spec0's but for the marker on a list column spec0 had to
        # guess - a copy knows what it holds (#2450)
        assert blocks[2][names[2]].replace(names[2], table) == blocks[1][names[1]].replace(
            names[1], table
        ), table
        guessed = re.sub(r"  / SCAFFOLDED: [^\n]*", "", blocks[0][table])
        assert blocks[1][names[1]].replace(names[1], table) == guessed, table


#: Copied with `--schema-from` in the round trip below, each for what it holds
#: beyond plain columns: market_data a three-column sort rule and float
#: ladders, kafka_message string ("C") columns, superbook symbol and timestamp
#: ladders, config_change general (" ") columns, trades a one-column sort rule
#: (added for the test - written with enlist). Not the vendored packets: its
#: list columns declare no element type, so its copy is guessed and marked.
_COPIED = ("market_data", "kafka_message", "superbook", "config_change", "trades")


def _copy(root: Path, sources: dict[str, plant_metadata.Table], copies: dict[str, str]) -> str:
    """For each `source: copy` in `copies`, scaffold a streaming job publishing
    `copy` with `--schema-from source`; append the plant_tables.q block each
    plan writes to `root`'s, and return those blocks."""
    from uqs.scaffold import jobs
    from uqs.scaffold.columns import resolve_shape

    blocks = ""
    for source, copy in copies.items():
        shape = resolve_shape(None, source, sources)
        plan = jobs.streaming_job(copy, [], copy, shape, known_tables=set(sources))
        blocks += "".join(a.body for a in plan.actions if a.path == TABLES_FILE)
    with (root / TABLES_FILE).open("a") as f:
        f.write(blocks)
    return blocks


def test_a_copied_table_keeps_its_whole_shape_through_two_cycles(tmp_path, q_binary, pack):
    """#2450: `--schema-from` copies a table's whole shape - columns and
    attributes, what each list column holds and its sort rule - so the copy
    q evaluates is the source again, and a copy of that copy is too. It kept
    only the columns: market_data's copy lost `sym p#, source, source_time`
    and kafka_message's "CC" came back "FF", marked for the user to fix."""
    one_column_sort = ".qetl.plant.sorting[`trades;(enlist `sym)!enlist `p];\n"
    root = _tree(tmp_path, (ROOT / TABLES_FILE).read_text() + one_column_sort, metadata=False)
    plant_metadata_gen.write(root)
    meta = plant_metadata.load(root)
    sources = {**meta.vendored(), **meta.own()}

    def shape(t: plant_metadata.Table) -> tuple:
        return t.columns, t.nested, t.sorting

    first = _copy(root, sources, {t: f"c1_{t}" for t in _COPIED})
    plant_metadata_gen.write(root)
    once = plant_metadata.load(root).own()
    second = _copy(root, once, {f"c1_{t}": f"c2_{t}" for t in _COPIED})
    plant_metadata_gen.write(root)
    twice = plant_metadata.load(root).own()
    for table in _COPIED:
        want = shape(sources[table])
        assert shape(once[f"c1_{table}"]) == want, table
        assert shape(twice[f"c2_{table}"]) == want, table
    # What the sources hold, so the comparison above is not of two empties.
    assert sources["market_data"].sorting == (("sym", "p"), ("source", ""), ("source_time", ""))
    assert sources["trades"].sorting == (("sym", "p"),)
    assert sources["kafka_message"].nested == {"msg_key": "C", "payload": "C"}
    assert sources["config_change"].nested == {"old": " ", "new": " "}
    # Nothing was guessed, so nothing is left for the user to fill in.
    assert "SCAFFOLDED" not in first + second, first + second


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
    "edit",
    [
        lambda t: t.replace("/ plant_tables.q", "/ plant_tables.q (reworded)", 1),
        lambda t: t + "/ a new comment at the end\n\n",
        lambda t: t.replace(
            ".qetl.plant.trades:([]", "/ a line comment before trades\n.qetl.plant.trades:([]", 1
        ),
        lambda t: re.sub(r"(\.qetl\.plant\.trades:\(\[\].*)$", r"\1  / trailing", t, flags=re.M),
        lambda t: t + "/\na block comment\n.qetl.plant.not_a_table:([]time:`timestamp$())\n\\\n",
    ],
    ids=["reworded", "appended", "line-before", "trailing", "block"],
)
def test_a_comment_only_edit_keeps_the_metadata_fresh(tmp_path, edit):
    root = _tree(tmp_path)
    plant = root / TABLES_FILE
    text = plant.read_text()
    plant.write_text(edit(text))
    assert plant.read_text() != text
    assert "trades" in plant_metadata.load(root).own()


@pytest.mark.parametrize(
    "edit",
    [
        lambda t: t.replace("fill_id:`symbol$())", "fill_id:`symbol$(); venue:`symbol$())", 1),
        lambda t: t.replace('"plant: no table "', '"plant: no such table "', 1),  # in a string
    ],
    ids=["column", "string"],
)
def test_a_code_edit_makes_the_metadata_stale(tmp_path, edit):
    root = _tree(tmp_path)
    plant = root / TABLES_FILE
    text = plant.read_text()
    plant.write_text(edit(text))
    assert plant.read_text() != text
    with pytest.raises(SchemaMetadataError, match="is stale"):
        plant_metadata.load(root)


@pytest.mark.parametrize(
    ("damage", "why"),
    [
        (lambda t: "{not json", "is not valid"),
        (
            lambda t: t.replace(
                f'"exporter_version": {plant_metadata.EXPORTER_VERSION}', '"exporter_version": 0'
            ),
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
        plant = where / TABLES_FILE
        plant.write_text(plant.read_text() + ".qetl.plant.late:([]time:`timestamp$())\n")
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
    from uqs.paths import DERIVE_SCRIPT

    assert _DERIVED[0] == (str(DERIVE_SCRIPT), "plant-schema")
    assert (ROOT / DERIVE_SCRIPT).is_file()


def test_a_rewrite_keeps_the_files_mode(tmp_path, monkeypatch):
    """#2632: mkstemp's 0600 replaced the shared 0644 file, locking out the
    frontend and a service account's uqs."""
    path = tmp_path / PLANT_SCHEMA_FILE
    path.parent.mkdir(parents=True)
    path.write_text("old\n")
    path.chmod(0o644)
    monkeypatch.setattr(plant_metadata_gen, "generate", lambda _root: "new\n")
    assert plant_metadata_gen.write(tmp_path)
    assert (path.read_text(), path.stat().st_mode & 0o777) == ("new\n", 0o644)
