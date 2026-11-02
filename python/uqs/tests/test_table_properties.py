"""What a desk may browse has a getdata row, and the row is derived (#1363).

The browsable set is the plant tables plus the datasets bounded workers write.
scripts/torqconfig/dataaccess/tableproperties.csv used to be five hand-written
rows; TorQ's checktablename refuses a table the file does not list, so the
catalog could offer what getdata would not serve.
"""

from __future__ import annotations

from pathlib import Path

from uqs import paths as stack_paths
from uqs.generated import q_facts
from uqs.model import plant_metadata, schemas
from uqs.model.registry import PIPELINES
from uqs.paths import UqsPaths
from uqs.stack import gateway_access

TABLE_PROPERTIES_COLUMNS = gateway_access.TABLE_PROPERTIES_HEADER.split(",")


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


def test_a_non_plant_dataset_is_read_by_the_time_the_hdb_writer_stamps() -> None:
    rows = {r.split(",")[1]: r.split(",") for r in _tree_rows()[1:]}
    assert rows["imported_trades"][2:5] == ["time", "sym", "sym"]
    # demo_deals is a plant table since #1851, so it is read by the plant's own
    # columns, whatever its worker's output (deal_time) is.
    assert rows["demo_deals"][2:5] == ["time", "sym", "sym"], (
        "a plant table is listed by the plant's own columns, whatever its worker's output"
    )


def _plant_read_columns() -> tuple[dict[str, str], dict[str, str]]:
    """Each plant table's time and instrument column, as q evaluated the plant
    (uqs.model.plant_metadata, from export_plant_schema.q)."""
    own = plant_metadata.load().own()
    return (
        {n: t.time_column for n, t in own.items()},
        {n: t.instrument_column for n, t in own.items()},
    )


def test_every_plant_table_carries_the_columns_q_reads_it_by() -> None:
    plant_time, plant_instrument = _plant_read_columns()
    assert set(plant_time) == set(plant_instrument) == schemas.table_names()
    assert all(plant_time.values()), "every plant table is read by some time column"


def test_every_row_of_the_committed_file_is_what_q_declares() -> None:
    """tableproperties.csv against q_facts, read from the file: a table is
    listed by the columns q declares for it - a plant table by its own, a
    dataset that is not one by its worker's."""
    rows = [
        dict(zip(TABLE_PROPERTIES_COLUMNS, r.split(","), strict=True)) for r in _tree_rows()[1:]
    ]
    plant_time, plant_instrument = _plant_read_columns()
    for row in rows:
        table = row["tablename"]
        if table in plant_time:
            want = (plant_time[table], plant_instrument[table])
        else:
            want = (
                q_facts.BOUNDED_DATASET_TIME_COLUMNS[table],
                q_facts.BOUNDED_DATASET_INSTRUMENT_COLUMNS[table],
            )
        assert (row["primarytimecolumn"], row["instrumentcolumn"]) == want, table
        assert row["attributecolumn"] == row["instrumentcolumn"], table


def test_a_dataset_that_is_a_plant_table_is_read_as_the_plant_says() -> None:
    """q derives a worker's dataset from the plant when it is one, so the two
    facts cannot contradict each other (order_quality, duckdb_deals)."""
    plant_time, plant_instrument = _plant_read_columns()
    for dataset, time_column in q_facts.BOUNDED_DATASET_TIME_COLUMNS.items():
        if dataset in plant_time:
            assert time_column == plant_time[dataset], dataset
            assert (
                q_facts.BOUNDED_DATASET_INSTRUMENT_COLUMNS[dataset] == plant_instrument[dataset]
            ), dataset
    assert plant_instrument["order_quality"] == "book"
    assert q_facts.BOUNDED_DATASET_TIME_COLUMNS["duckdb_deals"] == "time"


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
    assert "demo_deals" in _listed(paths), "a plant table is listed whether or not the HDB holds it"
