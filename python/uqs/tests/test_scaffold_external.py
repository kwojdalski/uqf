"""`uqs job new NAME --kind external` (#715): a Python publisher outside q, its
raw table, and the q job that reshapes it - written, found, and removed."""

from __future__ import annotations

import shutil
import sys
import types
from pathlib import Path

import pytest
from typer.testing import CliRunner

from uqs import cli
from uqs.external import feeds
from uqs.paths import RUN_TESTS_FILE, STACK_TABLES_TEST, TABLES_FILE, UqsError
from uqs.scaffold import write
from uqs.scaffold.external import external_feed, external_files
from uqs.scaffold.profile import PROFILES_FILE
from uqs.scaffold.remove import plan_removal

UQF_ROOT = Path(__file__).resolve().parents[3]
runner = CliRunner()


def _plan():
    return external_feed(
        "ws",
        "ws_raw",
        "ws_quotes",
        "sym:symbol, px:float",
        known_tables={"quote"},
        unprofiled="a test feed",
    )


def _body(plan, suffix: str) -> str:
    return next(a.body for a in plan.actions if str(a.path).endswith(suffix))


def test_the_plan_writes_both_halves_and_both_tables():
    plan = _plan()
    paths = {str(a.path) for a in plan.actions}
    assert set(external_files("ws")) <= paths, "streamer, feed module, and their test"
    assert "src/etl/streaming/ws.q" in paths, "the q job reshaping the raw table"
    tables = "".join(a.body for a in plan.actions if a.path == TABLES_FILE)
    assert "ws_raw:([]" in tables and "ws_quotes:([]" in tables


def test_the_q_job_subscribes_to_the_raw_table_and_publishes_the_other():
    job = _body(_plan(), "streaming/ws.q")
    assert "enlist `ws_raw" in job and "enlist `ws_quotes" in job


def test_the_generated_python_compiles_and_never_sends_time():
    plan = _plan()
    streamer = _body(plan, "ws_streamer.py")
    compile(streamer, "ws_streamer.py", "exec")
    compile(_body(plan, "ws_feed.py"), "ws_feed.py", "exec")
    assert "FIELDS = ['sym', 'px']" in streamer, ".u.upd stamps time; the publisher must not"
    assert "SCAFFOLDED" in streamer and "NotImplementedError" in streamer


def test_the_feed_module_declares_itself_for_the_cli():
    feed = _body(_plan(), "ws_feed.py")
    assert 'FEED = ExternalFeed("ws", start, stop, status)' in feed


@pytest.mark.parametrize(
    ("raw", "message"),
    [("quote", "already a plant table"), ("ws_quotes", "must differ")],
)
def test_a_raw_table_that_exists_or_doubles_the_output_is_refused(raw, message):
    with pytest.raises(UqsError, match=message):
        external_feed("ws", raw, "ws_quotes", "sym:symbol", known_tables={"quote"})


def test_discover_finds_a_module_that_declares_a_feed(monkeypatch):
    feed = feeds.ExternalFeed("fake", lambda p: 1, lambda p: None, lambda p: {})
    module = types.ModuleType("uqs.external.fake_feed")
    module.FEED = feed  # ty: ignore[unresolved-attribute]
    monkeypatch.setitem(sys.modules, "uqs.external.fake_feed", module)
    real = feeds.pkgutil.iter_modules
    monkeypatch.setattr(
        feeds.pkgutil,
        "iter_modules",
        lambda path: [*real(path), types.SimpleNamespace(name="fake_feed")],
    )
    assert feeds.discover()["fake"] is feed


def test_the_hand_listed_feeds_declare_nothing_and_collide_with_nothing():
    assert set(feeds.discover()).isdisjoint({"databento", "kafka", "crypto", "crypto-fills"})


def test_raw_table_is_refused_on_another_kind_and_required_on_external():
    other = runner.invoke(cli.app, ["job", "new", "x", "--raw-table", "r", "--dry-run"])
    assert other.exit_code != 0
    missing = runner.invoke(cli.app, ["job", "new", "x", "--kind", "external", "--dry-run"])
    assert missing.exit_code != 0


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    shutil.copytree(UQF_ROOT / "src", tmp_path / "src")
    shutil.copytree(UQF_ROOT / "scripts" / "processes", tmp_path / "scripts" / "processes")
    for rel in (RUN_TESTS_FILE, STACK_TABLES_TEST, PROFILES_FILE):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(UQF_ROOT / rel, tmp_path / rel)
    for rel in ("python/uqs/src/uqs/external", "python/uqs/tests"):
        (tmp_path / rel).mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_scaffold_then_remove_leaves_the_tree_as_it_was(tree):
    watched = [TABLES_FILE, RUN_TESTS_FILE, STACK_TABLES_TEST]
    before = {p: (tree / p).read_text() for p in watched}
    write.apply_plan(_plan(), tree)
    assert all((tree / f).is_file() for f in external_files("ws"))
    plan_removal(tree, "ws").apply(tree)
    assert not any((tree / f).exists() for f in external_files("ws")), "the Python pair goes"
    assert not (tree / "src/etl/streaming/ws.q").exists()
    after = {p: (tree / p).read_text() for p in watched}
    assert after == before, "both tables, the expected list and nsList restored"
