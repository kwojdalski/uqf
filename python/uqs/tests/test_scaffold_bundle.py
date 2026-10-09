"""`uqs job new ... --bundle DIR` (scaffold/bundle.py, cli/create_bundle.py).

The planners read this tree, through a scratch copy with the bundle installed;
every test writes only into a bundle under tmp_path, and checks that the tree's
own files are left as they were.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from uqs import cli
from uqs.cli import create, create_reaction
from uqs.paths import CATALOG_FILE, RUN_TESTS_FILE, TABLES_FILE, UqsError
from uqs.scaffold import bundle as bundle_mod
from uqs.scaffold.plan import FileAction, ScaffoldPlan, WriteMode
from uqs.stack import bundles

UQF_ROOT = Path(__file__).resolve().parents[3]
TRADES = [
    "mock_trades", "--kind", "backfill", "--dataset", "mock_trades",
    "--columns", "trade_time:timestamp, sym:symbol, px:float",
]  # fmt: skip


@pytest.fixture
def tree_files():
    """The tree files a tree scaffold appends to; each test asserts they are unchanged."""
    paths = [UQF_ROOT / p for p in (TABLES_FILE, CATALOG_FILE, RUN_TESTS_FILE)]
    before = {p: p.read_text() for p in paths}
    yield
    assert {p: p.read_text() for p in paths} == before, "--bundle wrote into the tree"


def _job_new(argv: list[str], monkeypatch) -> tuple[str, list[str]]:
    """Run `uqs job new`; return its output and any refusal it raised."""
    refused: list[str] = []
    for module in (create, create_reaction):
        monkeypatch.setattr(module, "_die", lambda exc: refused.append(str(exc)))
    result = CliRunner().invoke(cli.app, ["job", "new", *argv])
    assert result.exception is None, result.exception
    return result.stdout, refused


def test_a_backfill_lands_in_a_new_bundle(tmp_path, monkeypatch, tree_files):
    folder = tmp_path / "mockups"
    _out, refused = _job_new([*TRADES, "--bundle", str(folder)], monkeypatch)
    assert refused == []
    assert json.loads((folder / "bundle.json").read_text()) == {
        "name": "mockups",
        "version": bundle_mod.FIRST_VERSION,
    }
    names = sorted(p.name for p in folder.iterdir())
    assert names == [
        "bundle.json", "catalog.q", "mock_trades.q", "mock_trades_backfill.q",
        "tables.q", "test_mock_trades_backfill.q",
    ]  # fmt: skip
    # The first table makes tables.q, with no leading blank line.
    assert (folder / "tables.q").read_text().startswith("/ ")
    # And what was written is a bundle the installer reads.
    bundles.read_bundle(folder)


def test_a_second_job_sees_the_first(tmp_path, monkeypatch, tree_files):
    """A reaction on the bundle's own dataset: no worker in the TREE fills
    mock_trades, so planning against the tree alone would refuse it."""
    folder = tmp_path / "mockups"
    _job_new([*TRADES, "--bundle", str(folder)], monkeypatch)
    argv = ["mock_rollup", "--triggered-by", "mock_trades", "--bundle", str(folder)]
    _out, refused = _job_new(argv, monkeypatch)
    assert refused == []
    assert (folder / "mock_rollup.q").is_file()


def test_a_streaming_job_may_subscribe_to_a_bundle_table(tmp_path, monkeypatch, tree_files):
    folder = tmp_path / "mockups"
    feed = ["mock_ticks", "--publishes", "mock_ticks", "--columns", "sym:symbol, bid:float"]
    _job_new([*feed, "--bundle", str(folder)], monkeypatch)
    etl = ["mock_mids", "--subscribe-to", "mock_ticks", "--publishes", "mock_mids",
           "--columns", "sym:symbol, mid:float", "--bundle", str(folder)]  # fmt: skip
    _out, refused = _job_new(etl, monkeypatch)
    assert refused == []
    assert "mock_mids:([]" in (folder / "tables.q").read_text()


def test_a_dry_run_writes_nothing(tmp_path, monkeypatch, tree_files):
    folder = tmp_path / "mockups"
    out, refused = _job_new([*TRADES, "--bundle", str(folder), "--dry-run"], monkeypatch)
    assert refused == [] and not folder.exists()
    assert "tests/run_tests.q" in out  # named among what a bundle does not write


@pytest.mark.parametrize("option", [["--profile", "essential"], ["--unprofiled", "why"]])
def test_a_profile_option_is_refused(tmp_path, monkeypatch, option):
    argv = ["mock_ticks", "--publishes", "mock_ticks", "--columns", "sym:symbol", *option]
    _out, refused = _job_new([*argv, "--bundle", str(tmp_path / "mockups")], monkeypatch)
    assert refused and "does not apply with --bundle" in refused[0]


def test_a_folder_that_cannot_name_a_bundle_is_refused(tmp_path):
    with pytest.raises(UqsError, match="not lower_snake_case"):
        bundle_mod.check_folder(tmp_path / "Mock-Ups")


def test_tree_registrations_are_dropped_and_named():
    plan = ScaffoldPlan(
        "x",
        [
            FileAction(Path("src/etl/streaming/x.q"), "job\n"),
            FileAction(TABLES_FILE, "\n/ x\nx:([]a:())\n", WriteMode.APPEND),
            FileAction(RUN_TESTS_FILE, "`.xtest", WriteMode.APPEND),
        ],
        [f"describe x: replace its SCAFFOLDED line in {CATALOG_FILE} - or, if not, hide it"],
    )
    out = bundle_mod.into_bundle(plan, Path("b"))
    assert [a.path for a in out.actions] == [
        Path("b/bundle.json"), Path("b/x.q"), Path("b/tables.q"),
    ]  # fmt: skip
    assert out.actions[2].mode is WriteMode.CREATE and out.actions[2].body == "/ x\nx:([]a:())\n"
    assert any(str(RUN_TESTS_FILE) in n and "not written" in n for n in out.notes)
