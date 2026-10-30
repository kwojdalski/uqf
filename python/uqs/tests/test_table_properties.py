"""What a desk may browse has a getdata row, and the row is derived (#1363).

The browsable set is the plant tables plus the datasets bounded workers write.
scripts/torqconfig/dataaccess/tableproperties.csv used to be five hand-written
rows; TorQ's checktablename refuses a table the file does not list, so the
catalog could offer what getdata would not serve.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from uqs import paths as stack_paths
from uqs.generated import q_facts
from uqs.model import schemas
from uqs.model.registry import PIPELINES
from uqs.paths import UqsPaths
from uqs.stack import gateway_access


def _tree_rows() -> list[str]:
    return (
        (stack_paths.repo_root() / "scripts" / gateway_access.TABLE_PROPERTIES)
        .read_text()
        .splitlines()
    )


def _paths(root: Path) -> UqsPaths:
    return UqsPaths(
        repo_root=root,
        torqhome=root / "lib" / "torq",
        torqapphome=root / "lib" / "torq-finance-starter-pack",
        torqdata=root / "output" / "data",
        scripts_dir=root / "scripts",
        orchestrator_dir=root / "python" / "uqs",
        runtime="uqf",
    )


def test_the_committed_file_is_the_derived_one() -> None:
    assert _tree_rows() == gateway_access.table_properties_rows(), (
        "run `uv run python scripts/generate/derive.py table-properties`"
    )


def test_every_browsable_table_has_a_row() -> None:
    """The browsable set is derived here from the two declarations, not retyped."""
    declared = {p.dataset for p in PIPELINES if p.dataset}
    listed = {row.split(",")[1] for row in _tree_rows()[1:]}
    assert (schemas.table_names() | declared) - listed == set()


def test_the_workers_datasets_q_exports_are_the_registrys() -> None:
    """q_facts (read from q) and the registry (read from the q text) name the same datasets."""
    declared = {p.dataset for p in PIPELINES if p.dataset}
    assert set(q_facts.BOUNDED_DATASET_TIME_COLUMNS) == declared
    assert set(q_facts.BOUNDED_DATASET_INSTRUMENT_COLUMNS) == declared


def test_a_non_plant_dataset_is_listed_by_its_transforms_output() -> None:
    rows = {r.split(",")[1]: r.split(",") for r in _tree_rows()[1:]}
    assert rows["imported_trades"][2:5] == ["time", "sym", "sym"]
    assert rows["demo_deals"][2] == "deal_time", (
        "its output has no `time`; it is windowed on deal_time"
    )


def test_the_one_override_names_a_column_the_plant_gives_it(monkeypatch) -> None:
    for table, column in gateway_access.INSTRUMENT_COLUMN_OVERRIDES.items():
        assert column in gateway_access._plant_columns(table)
    monkeypatch.setitem(gateway_access.INSTRUMENT_COLUMN_OVERRIDES, "order_quality", "no_such_col")
    with pytest.raises(ValueError, match="no_such_col.*order_quality"):
        gateway_access.derived_row("order_quality")


def _runtime_file(tmp_path: Path) -> UqsPaths:
    paths = _paths(tmp_path)
    props = paths.scripts_dir / gateway_access.TABLE_PROPERTIES
    props.parent.mkdir(parents=True)
    props.write_text("\n".join(gateway_access.table_properties_rows()) + "\n")
    return paths


def _listed(paths: UqsPaths) -> set[str]:
    return {ln.split(",")[1] for ln in gateway_access.table_properties_lines(paths)[1:]}


def test_a_worker_dataset_is_passed_on_only_while_the_hdb_holds_it(tmp_path, monkeypatch) -> None:
    """TorQ exits at start when its list names a table the HDB lacks."""
    monkeypatch.setattr(gateway_access, "plant_tables", lambda r: None)
    paths = _runtime_file(tmp_path)
    assert "imported_trades" not in _listed(paths), "no HDB yet"
    assert "mkt_orderbook" in _listed(paths)

    (paths.hdb_dir / "2026.01.01" / "imported_trades").mkdir(parents=True)
    (paths.hdb_dir / "2026.01.02" / "trades").mkdir(parents=True)
    assert "imported_trades" not in _listed(paths), "the newest partition lacks it"

    (paths.hdb_dir / "2026.01.02" / "imported_trades").mkdir()
    assert "imported_trades" in _listed(paths)
    assert "demo_deals" not in _listed(paths), "only what the HDB holds"
