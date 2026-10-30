"""Every reader of a tree's plant answers from what q evaluated (#1781, part 2).

The scaffold's readers, the attach planner's tree half, the bundle planner's
check against the tree and the plant runtime's reserved names used to match
`name:([]...)` in plant_tables.q's text. These hold each of them to q's answer:
a table spelled `name: ([]...)`, which q loads the same, is seen by all of them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from uqs.attach.schema import SchemaError, spec_of
from uqs.model import plant_metadata
from uqs.paths import BUNDLE_LEDGER, paths_for_root

#: One table in the spelling the text readers missed, one nested, one keyed-free
#: helper bound in .qetl.plant that is not a table.
TABLES = (
    ".qetl.plant.spaced: ([] time:`timestamp$(); sym:`g#`symbol$(); px:`float$())\n"
    "ladder:([]time:`timestamp$(); sym:`symbol$(); bids:())\n"
    '.qetl.plant.nested[`ladder;(enlist `bids)!enlist "F"];\n'
    ".qetl.plant.helper: {[x] x}\n"
)


@pytest.fixture
def tree(tmp_path: Path, plant_tree) -> Path:
    return plant_tree(tmp_path / "tree", TABLES)


def test_the_scaffold_readers_see_a_table_however_it_is_spelled(tree):
    from uqs.cli.create_backfill import defined_tables
    from uqs.cli.create_plant import plant_definitions, plant_tables

    paths = paths_for_root(tree)
    assert {"spaced", "ladder"} <= defined_tables(tree)
    assert {"spaced", "ladder"} <= plant_tables(paths, tree)
    assert plant_definitions(paths, tree)["spaced"] == (
        "spaced:([]time:`timestamp$(); sym:`g#`symbol$(); px:`float$())"
    )


def test_a_jobs_owned_table_is_one_q_says_the_tree_defines():
    """Ownership is resolved on use, from q's metadata - not while the
    registry is built, which every command does."""
    from uqs.model import schemas
    from uqs.model.pipeline import Pipeline, PipelineKind
    from uqs.model.registry import PIPELINES

    owned = [p.owned_table for p in PIPELINES if p.owned_table]
    assert owned and set(owned) <= schemas.table_names()
    job = Pipeline("x1", "x.q", PipelineKind.ETL, sole_publisher_of=("trades", "quote"))
    assert job.owned_table == "trades"  # quote is the starter pack's, not this tree's


def test_the_plant_runtimes_names_are_qs(tree):
    """Reserved for a table name (#1597): the API and registries q bound -
    including one written `helper: {...}` - and never a table."""
    bindings = plant_metadata.load(tree).bindings
    assert {"schema", "shape", "nested", "elements", "sort_rules", "helper"} <= bindings
    assert not bindings & {"spaced", "ladder"}


def test_this_trees_reserved_names_are_read_from_its_metadata():
    from uqs.scaffold.columns import plant_runtime_names

    assert plant_runtime_names() == plant_metadata.load().bindings
    assert "schema" in plant_runtime_names()


def test_a_bundle_is_checked_against_the_trees_tables_but_not_its_own(tree):
    from uqs.stack.bundle_shadowing import known_tables

    (tree / BUNDLE_LEDGER).write_text(json.dumps({"piggybank": {"tables": ["ladder"]}}))
    assert "spaced" in known_tables(tree, "piggybank")
    assert "ladder" not in known_tables(tree, "piggybank")  # its own, being reinstalled
    assert "ladder" in known_tables(tree, "another")


def test_an_attached_tree_table_is_specified_from_q(tree):
    meta = plant_metadata.load(tree)
    spec = spec_of(meta.table("ladder"))
    assert spec.columns == (("time", "p"), ("sym", "s"), ("bids", "F"))
    assert spec_of(meta.table("spaced")).columns[1] == ("sym", "s")


def test_an_untyped_tree_column_is_refused_as_the_text_reader_refused_it():
    meta = plant_metadata.load()
    with pytest.raises(SchemaError, match="#1451"):
        spec_of(meta.table("config_change"))  # old/new are declared " ": any value


def test_a_worker_dataset_in_the_tree_is_specified_from_q(tree):
    from types import SimpleNamespace
    from typing import cast

    from uqs.attach.backfill import worker_table
    from uqs.attach.config import Attachment

    att = cast(Attachment, SimpleNamespace(origin={}))  # only its origin is read
    spec = worker_table(paths_for_root(tree), att, "spaced")
    assert spec is not None and [c for c, _ in spec.columns] == ["time", "sym", "px"]
    assert worker_table(paths_for_root(tree), att, "nothing_here") is None


def test_a_stale_tree_is_refused_by_name_not_read_as_text(tree):
    from uqs.cli.create_backfill import defined_tables

    plant = tree / "src/etl/plant_tables.q"
    plant.write_text(plant.read_text() + ".qetl.plant.late:([]time:`timestamp$())\n")
    with pytest.raises(plant_metadata.SchemaMetadataError, match="is stale"):
        defined_tables(tree)


def test_an_uncaught_stale_refusal_is_one_line_not_a_traceback():
    """A refusal no command catches still says what to run, and nothing else."""
    import subprocess
    import sys

    code = (
        "import uqs\n"
        "from uqs.model.plant_metadata import SchemaMetadataError\n"
        "raise SchemaMetadataError('stale - regenerate it')\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 1
    assert r.stderr == "uqs: stale - regenerate it\n"


STALE = """\
import sys
from uqs.model import plant_metadata
plant_metadata.tree_fingerprint = lambda root: "stale"  # every read now finds it stale
sys.argv = ["uqs", *sys.argv[1:]]
from uqs.cli import main
main()
"""


def _uqs_on_a_stale_tree(tmp_path: Path, *argv: str):
    import os
    import subprocess
    import sys

    script = tmp_path / "stale_uqs.py"
    script.write_text(STALE)
    env = {k: v for k, v in os.environ.items() if not k.startswith("UQF_")}
    return subprocess.run(
        [sys.executable, str(script), *argv], capture_output=True, text=True, env=env, timeout=120
    )


@pytest.mark.parametrize("argv", [["--help"], ["status"], ["graph"]], ids=" ".join)
def test_a_stale_tree_still_runs_what_needs_no_schema(tmp_path, argv):
    """Building the registry needs no metadata - only ownership does, asked
    on use - so a stale tree stops only what reads schemas."""
    r = _uqs_on_a_stale_tree(tmp_path, *argv)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "stale" not in r.stdout + r.stderr


def test_a_stale_tree_refuses_a_schema_read_by_name(tmp_path):
    r = _uqs_on_a_stale_tree(tmp_path, "runtime", "diff", "uqf", "torq", "--json")
    assert r.returncode == 1
    out = r.stdout + r.stderr
    assert "plant_schema.json is stale" in out
    assert plant_metadata.REGENERATE in out
