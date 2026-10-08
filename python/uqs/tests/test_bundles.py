"""Tests for sidecar bundles (stack/bundles.py, cli/install_bundle.py, #800).

Each test builds a fake repository root and a bundle under tmp_path; nothing
here writes into this tree.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from uqs import cli
from uqs import paths as stack_paths
from uqs.cli import install as cli_install
from uqs.cli import install_bundle as cli_bundle
from uqs.cli.regenerate import _DERIVED
from uqs.paths import CATALOG_FILE, SOURCE_DIR, STREAM_DIR, TABLES_FILE, WORKER_DIR
from uqs.stack import bundle_blocks, bundles
from uqs.stack.bundles import LEDGER, OVERRIDES_FILE, BundleError

SOURCE = "source_name:`piggy\n.qetl.source.define[source_name;`columns!enlist `time];\n"
WORKER = (
    ".qetl.job.bounded.define[`piggy_backfill;`source`dataset`width!(`piggy;`piggy_tape;1D)];\n"
)
STREAM = (
    ".qetl.job.stream.define[`piggy_spread;"
    "`procname`subscribe_to`publishes!(`piggy_spread1;enlist `quote;enlist `piggy_tape)];\n"
)
TABLES = "/ piggybank's tape\npiggy_tape:([]time:`timestamp$();sym:`symbol$();px:())\n"
NESTED = 'nested[`piggy_tape;(enlist `px)!enlist "F"];\n'
CATALOG = '.qcat.describe[`piggy_tape]:\n    "one piggybank print";\n'
TREE_TABLES = "\\d .qetl.plant\nquote:([]time:`timestamp$();sym:`symbol$())\n"
TREE_CATALOG = (
    '\\d .qcat\ndescribe[`quote]:"a quote";\n\\d .\n.qcat.describe[`trade]:\n    "a trade";\n'
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    for directory in (SOURCE_DIR, WORKER_DIR, STREAM_DIR):
        (root / directory).mkdir(parents=True)
    (root / TABLES_FILE).write_text(TREE_TABLES)
    (root / CATALOG_FILE).parent.mkdir(parents=True)
    (root / CATALOG_FILE).write_text(TREE_CATALOG)
    (root / OVERRIDES_FILE).parent.mkdir(parents=True)
    return root


def make_bundle(where: Path, name: str = "piggybank", version: str = "1.0.0") -> Path:
    where.mkdir(parents=True, exist_ok=True)
    (where / "bundle.json").write_text(json.dumps({"name": name, "version": version}))
    (where / "piggy.q").write_text(SOURCE)
    (where / "piggy_backfill.q").write_text(WORKER)
    (where / "piggy_spread.q").write_text(STREAM)
    (where / "tables.q").write_text(TABLES + NESTED)
    (where / "catalog.q").write_text(CATALOG)
    (where / "process_overrides.csv").write_text(
        "procname,field,value\npiggy_spread1,startwithall,0\n"
    )
    (where / "test_piggy.q").write_text("/ a test\n")
    (where / ".env").write_text("PIGGY_PASSWORD=hunter2\n")
    return where


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    return make_bundle(tmp_path / "piggybank")


def _install(folder: Path, root: Path) -> dict:
    return bundles.install(bundles.plan(bundles.read_bundle(folder), root), root)


# --------------------------------------------------------------- manifest


@pytest.mark.parametrize(
    ("manifest", "why"),
    [
        ("not json", "not a readable JSON"),
        ('{"name": "Piggy", "version": "1"}', "lower_snake_case"),
        ('{"name": "piggy", "version": ""}', "version"),
        ('{"name": "piggy", "version": "1", "secret": "x"}', "unknown key"),
    ],
)
def test_a_bad_manifest_is_refused(tmp_path: Path, manifest: str, why: str) -> None:
    (tmp_path / "bundle.json").write_text(manifest)
    with pytest.raises(BundleError, match=why):
        bundles.read_bundle(tmp_path)


# ---------------------------------------------------------------- install


def test_install_places_jobs_and_writes_every_addition(bundle: Path, repo: Path) -> None:
    entry = _install(bundle, repo)
    assert (repo / SOURCE_DIR / "piggy.q").read_text() == SOURCE
    assert (repo / WORKER_DIR / "piggy_backfill.q").read_text() == WORKER
    assert not (repo / STREAM_DIR / "piggy_spread.q").is_symlink()
    tables = (repo / TABLES_FILE).read_text()
    assert "/ BEGIN bundle piggybank\n/ piggybank's tape\npiggy_tape:([]" in tables
    assert "nested[`piggy_tape;" in tables and tables.endswith("/ END bundle piggybank\n")
    assert ".qcat.describe[`piggy_tape]:" in (repo / CATALOG_FILE).read_text()
    assert (repo / OVERRIDES_FILE).read_text() == (
        "procname,field,value\npiggy_spread1,startwithall,0\n"
    )
    assert entry["version"] == "1.0.0" and entry["tables"] == ["piggy_tape"]
    assert entry["jobs"] == [
        {"kind": "source", "file": "piggy.q"},
        {"name": "piggy_backfill", "kind": "worker", "procname": "piggy_backfill1",
         "file": "piggy_backfill.q"},
        {"name": "piggy_spread", "kind": "streaming", "procname": "piggy_spread1",
         "file": "piggy_spread.q"},
    ]  # fmt: skip
    assert json.loads((repo / LEDGER).read_text())["piggybank"] == entry


def test_secrets_and_tests_never_enter_the_tree(bundle: Path, repo: Path) -> None:
    entry = _install(bundle, repo)
    assert not list(repo.rglob(".env"))
    assert not list(repo.rglob("test_piggy.q"))
    assert all("test_" not in f and ".env" not in f for f in entry["files"])


def test_reinstalling_appends_nothing(bundle: Path, repo: Path) -> None:
    _install(bundle, repo)
    before = {p: p.read_text() for p in (repo / TABLES_FILE, repo / CATALOG_FILE)}
    before[repo / OVERRIDES_FILE] = (repo / OVERRIDES_FILE).read_text()
    _install(bundle, repo)
    for path, text in before.items():
        assert path.read_text() == text
    assert (repo / TABLES_FILE).read_text().count("piggy_tape:([]") == 1


def test_an_upgrade_replaces_its_own_files_and_drops_what_it_no_longer_ships(
    bundle: Path, repo: Path
) -> None:
    _install(bundle, repo)
    (bundle / "bundle.json").write_text('{"name": "piggybank", "version": "1.1.0"}')
    (bundle / "piggy_spread.q").write_text(STREAM + "/ v1.1\n")
    (bundle / "piggy_backfill.q").unlink()
    (bundle / "process_overrides.csv").write_text("procname,field,value\n")
    entry = _install(bundle, repo)
    assert entry["version"] == "1.1.0"
    assert (repo / STREAM_DIR / "piggy_spread.q").read_text().endswith("/ v1.1\n")
    assert not (repo / WORKER_DIR / "piggy_backfill.q").exists()
    assert "piggy_spread1" not in (repo / OVERRIDES_FILE).read_text()


def test_a_file_the_bundle_did_not_install_is_a_conflict(bundle: Path, repo: Path) -> None:
    (repo / STREAM_DIR / "piggy_spread.q").write_text("/ someone else's\n")
    with pytest.raises(BundleError, match="was not installed by bundle piggybank"):
        bundles.plan(bundles.read_bundle(bundle), repo)


def test_two_bundles_may_not_ship_one_job(bundle: Path, repo: Path, tmp_path: Path) -> None:
    _install(bundle, repo)
    other = make_bundle(tmp_path / "copycat", name="copycat")
    (other / "tables.q").unlink()
    (other / "catalog.q").unlink()
    (other / "process_overrides.csv").unlink()
    with pytest.raises(BundleError, match="belongs to another bundle"):
        bundles.plan(bundles.read_bundle(other), repo)


def test_a_table_the_tree_defines_is_refused(bundle: Path, repo: Path) -> None:
    (bundle / "tables.q").write_text("quote:([]time:`timestamp$())\n")
    with pytest.raises(BundleError, match="quote is already defined"):
        bundles.plan(bundles.read_bundle(bundle), repo)


def test_a_described_table_is_refused(bundle: Path, repo: Path) -> None:
    (bundle / "catalog.q").write_text(CATALOG + '.qcat.describe[`trade]:"again";\n')
    with pytest.raises(BundleError, match="trade is already described"):
        bundles.plan(bundles.read_bundle(bundle), repo)


def test_an_undescribed_table_is_refused(bundle: Path, repo: Path) -> None:
    (bundle / "catalog.q").unlink()
    with pytest.raises(BundleError, match="not described"):
        bundles.plan(bundles.read_bundle(bundle), repo)


@pytest.mark.parametrize(
    ("tables", "why"),
    [
        ("/\n", "block comment"),
        ("piggy_tape:([]time:`timestamp$())\npiggy_tape:([]time:`timestamp$())\n", "twice"),
        ('nested[`elsewhere;(enlist `px)!enlist "F"];\n', "does not define"),
        ("show 1\n", "expected"),
    ],
)
def test_tables_q_is_held_to_the_plant_convention(
    bundle: Path, repo: Path, tables: str, why: str
) -> None:
    (bundle / "tables.q").write_text(tables)
    with pytest.raises(BundleError, match=why):
        bundles.plan(bundles.read_bundle(bundle), repo)


@pytest.mark.parametrize(
    ("rows", "why"),
    [
        ("quotes1,startwithall,0\n", "not one of this bundle's processes"),
        ("piggy_spread1,port,9999\n", "may set"),
        ("piggy_spread1,startwithall,0\npiggy_spread1,startwithall,1\n", "set twice"),
        ('piggy_spread1,load,"a,b"\n', "no comma"),
    ],
)
def test_overrides_are_the_bundles_own_and_well_formed(
    bundle: Path, repo: Path, rows: str, why: str
) -> None:
    (bundle / "process_overrides.csv").write_text("procname,field,value\n" + rows)
    with pytest.raises(BundleError, match=why):
        bundles.plan(bundles.read_bundle(bundle), repo)


def test_an_operator_override_is_not_silently_replaced(bundle: Path, repo: Path) -> None:
    (repo / OVERRIDES_FILE).write_text("procname,field,value\npiggy_spread1,startwithall,1\n")
    with pytest.raises(BundleError, match="already '1'"):
        _install(bundle, repo)
    assert not (repo / STREAM_DIR / "piggy_spread.q").exists()


def test_damaged_markers_are_refused() -> None:
    text = "a\n/ BEGIN bundle piggybank\nb\n"
    with pytest.raises(BundleError, match="damaged"):
        bundle_blocks.with_block(text, "piggybank", ["c"], "f.q")


def test_an_empty_body_removes_the_block() -> None:
    text = bundle_blocks.with_block("a\n", "p", ["x"], "f.q")
    assert bundle_blocks.with_block(text, "p", [], "f.q") == "a\n\n"


# -------------------------------------------------------------------- CLI


@pytest.fixture
def cli_repo(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(cli_install, "_paths", lambda: stack_paths.paths_for_root(repo))
    monkeypatch.setattr(
        cli_bundle,
        "_regenerate_derived",
        lambda _root: [subprocess.CompletedProcess([], 0, "", "")] * len(_DERIVED),
    )
    return repo


def _run(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(cli.app, ["job", "install", *args])
    return result.exit_code, result.output


def test_cli_installs_a_bundle(bundle: Path, cli_repo: Path) -> None:
    code, out = _run(str(bundle), "--yes")
    assert code == 0, out
    assert "bundle piggybank 1.0.0" in out
    assert "uqs start piggy_spread1" in out
    assert "Installed, not run" in out
    assert (cli_repo / LEDGER).is_file()


def test_cli_dry_run_writes_nothing(bundle: Path, cli_repo: Path) -> None:
    code, out = _run(str(bundle), "--dry-run")
    assert code == 0, out
    assert not (cli_repo / LEDGER).exists()
    assert not (cli_repo / STREAM_DIR / "piggy_spread.q").exists()


def test_cli_refuses_symlink_for_a_bundle(bundle: Path, cli_repo: Path) -> None:
    code, _out = _run(str(bundle), "--mode", "symlink", "--yes")
    assert code == 1
    assert not (cli_repo / LEDGER).exists()
