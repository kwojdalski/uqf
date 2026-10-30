"""`uqs job new NAME --kind external` (#715): a Python publisher outside q, its
raw table, and the q job that reshapes it - written, found, and removed."""

from __future__ import annotations

import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest
from typer.testing import CliRunner

from uqs import cli
from uqs.external import feeds
from uqs.model import dependencies
from uqs.paths import CATALOG_FILE, STACK_TABLES_TEST, TABLES_FILE, UqsError
from uqs.scaffold import write
from uqs.scaffold.docs import SHOWCASE_PAGE, STACK_PAGE
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


def test_the_generated_publisher_sends_a_list_of_columns_in_field_order(monkeypatch):
    """#1580: .u.upd gets one list per column, by position. The segmented
    tickerplant prepends its time column with `(enlist ...),x`, which a list
    accepts and a dictionary answers with `type`."""
    sent: list[tuple] = []

    class Q:
        def __init__(self, *args, **kwargs):
            pass

        def connect(self):
            pass

        def sync(self, *args):
            sent.append(args)

    monkeypatch.setitem(sys.modules, "kola", types.SimpleNamespace(Q=Q))
    streamer: dict = {"__name__": "ws_streamer"}
    exec(compile(_body(_plan(), "ws_streamer.py"), "ws_streamer.py", "exec"), streamer)
    streamer["batches"] = lambda: iter([[{"px": 1.0, "sym": "BTC"}, {"px": 2.0, "sym": "ETH"}]])
    assert streamer["main"](["--port", "5010", "--credential", "u:p"]) == 0
    assert sent == [(".u.upd", "ws_raw", [["BTC", "ETH"], [1.0, 2.0]])]


def test_the_feed_module_declares_itself_for_the_cli():
    feed = _body(_plan(), "ws_feed.py")
    assert "FEED = ExternalFeed(" in feed
    assert "def start(paths: UqsPaths, options: Mapping[str, Any]) -> int:" in feed
    assert 'produces={RAW_TABLE: "the external ws feed (`uqs connector start ws`)"}' in feed


@pytest.mark.parametrize(
    ("raw", "message"),
    [("quote", "already a plant table"), ("ws_quotes", "must differ")],
)
def test_a_raw_table_that_exists_or_doubles_the_output_is_refused(raw, message):
    with pytest.raises(UqsError, match=message):
        external_feed("ws", raw, "ws_quotes", "sym:symbol", known_tables={"quote"})


def test_discover_finds_a_module_that_declares_a_feed(monkeypatch):
    feed = feeds.ExternalFeed("fake", lambda p, o: 1, lambda p: None, lambda p: {})
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


def test_every_connector_declares_itself():
    """#1776: no feed is hand-wired into the CLI any more."""
    assert {"databento", "kafka", "crypto", "crypto-fills", "flink_vwap"} <= set(feeds.discover())


def test_raw_table_is_refused_on_another_kind_and_required_on_external():
    other = runner.invoke(cli.app, ["job", "new", "x", "--raw-table", "r", "--dry-run"])
    assert other.exit_code != 0
    missing = runner.invoke(cli.app, ["job", "new", "x", "--kind", "external", "--dry-run"])
    assert missing.exit_code != 0


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    shutil.copytree(UQF_ROOT / "src", tmp_path / "src")
    shutil.copytree(UQF_ROOT / "scripts" / "processes", tmp_path / "scripts" / "processes")
    for rel in (
        STACK_TABLES_TEST,
        PROFILES_FILE,
        STACK_PAGE,
        SHOWCASE_PAGE,
    ):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(UQF_ROOT / rel, tmp_path / rel)
    for rel in ("python/uqs/src/uqs/external", "python/uqs/tests"):
        (tmp_path / rel).mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_scaffold_then_remove_leaves_the_tree_as_it_was(tree):
    watched = [
        TABLES_FILE,
        STACK_TABLES_TEST,
        STACK_PAGE,
        SHOWCASE_PAGE,
        CATALOG_FILE,
    ]
    before = {p: (tree / p).read_text() for p in watched}
    write.apply_plan(_plan(), tree)
    assert all((tree / f).is_file() for f in external_files("ws"))
    plan_removal(tree, "ws").apply(tree)
    assert not any((tree / f).exists() for f in external_files("ws")), "the Python pair goes"
    assert not (tree / "src/etl/streaming/ws.q").exists()
    after = {p: (tree / p).read_text() for p in watched}
    assert after == before, "tables, lists and catalog restored"
    assert dependencies.external_producers(tree) == {}, "what it produced went with its module"


def test_a_scaffolded_feed_names_its_raw_table_where_the_stack_looks(tree):
    """#888: the raw table is fed from outside the stack, and the two places
    that must say so do, without a hand edit: the feed's own `produces`, which
    `uqs start` and test_dependencies read (#1776), and the catalog, which
    test_catalog.q reads. dependencies.py is not edited."""
    assert dependencies.external_producers(tree) == {}
    write.apply_plan(_plan(), tree)
    assert dependencies.external_producers(tree) == {
        "ws_raw": "the external ws feed (`uqs connector start ws`)"
    }
    catalog = (tree / CATALOG_FILE).read_text()
    assert ".qcat.hidden[`ws_raw]:" in catalog and ".qcat.describe[`ws_quotes]" in catalog


#: Run in a process of its own: the CLI builds `connector start` at import.
_HELP_WITH_A_SCAFFOLDED_FEED = """
import dataclasses, pkgutil, sys, types
from uqs.external import feeds
module = types.ModuleType("uqs.external.ws_feed")
exec(compile(sys.stdin.read(), "ws_feed.py", "exec"), module.__dict__)
option = feeds.FeedOption("ws_url", "Websocket to subscribe to", default="wss://x")
module.FEED = dataclasses.replace(module.FEED, options=(option,))
sys.modules["uqs.external.ws_feed"] = module
real = pkgutil.iter_modules
pkgutil.iter_modules = lambda path: [*real(path), types.SimpleNamespace(name="ws_feed")]
from typer.testing import CliRunner
from uqs import cli
print(CliRunner().invoke(cli.app, ["connector", "start", "--help"], terminal_width=200).output)
print(CliRunner().invoke(cli.app, ["connector", "restart", "ws", "-n", "--ws-url", "y"]).output)
"""


def test_a_scaffolded_feed_with_options_reaches_connector_start_help():
    """#1776: a feed declares its options, and `connector start` and `restart`
    take them - no CLI edit, which only option-less feeds could do before."""
    done = subprocess.run(
        [sys.executable, "-c", _HELP_WITH_A_SCAFFOLDED_FEED],
        input=_body(_plan(), "ws_feed.py"),
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    assert "--ws-url" in done.stdout and "Websocket to subscribe to (ws)" in done.stdout
    assert "would restart ws with ws_url=y" in done.stdout


def test_a_feed_can_publish_onto_an_existing_canonical_table():
    """#1581: --schema shapes the NEW raw table, which an external feed
    always defines. Onto an existing output they were forwarded to the q job,
    which refused them as shaping nothing - and dropping them is not allowed."""
    plan = external_feed(
        "ws", "ws_raw", "quote", "sym:symbol, px:float",
        known_tables={"quote"}, unprofiled="a test feed",
    )  # fmt: skip
    tables = "".join(a.body for a in plan.actions if a.path == TABLES_FILE)
    assert ".qetl.plant.ws_raw:" in tables, "the raw table is still defined"
    assert ".qetl.plant.quote:" not in tables, "the existing output is not redefined"
