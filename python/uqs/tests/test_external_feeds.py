"""One interface for the external connectors (#1776): each declares a FEED - its
options, what it produces - and the CLI and the dependency warnings read it."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
from typer.testing import CliRunner

from uqs.external import feeds
from uqs.model import dependencies
from uqs.paths import UqsError

runner = CliRunner()


def _feed(name: str, *options: feeds.FeedOption, start=lambda p, o: 1, **kw) -> feeds.ExternalFeed:
    return feeds.ExternalFeed(name, start, lambda p: None, lambda p: {}, options=options, **kw)


def _discover_with(monkeypatch, name: str, **attrs) -> None:
    """`uqs.external.<name>` holds `attrs`, and discover() sees it."""
    module = types.ModuleType(f"uqs.external.{name}")
    for key, value in attrs.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    real = feeds.pkgutil.iter_modules
    monkeypatch.setattr(
        feeds.pkgutil, "iter_modules", lambda path: [*real(path), types.SimpleNamespace(name=name)]
    )


def test_two_feeds_declaring_one_option_with_different_types_are_refused():
    a = _feed("a", feeds.FeedOption("depth", "Levels", type=int))
    b = _feed("b", feeds.FeedOption("depth", "Levels"))
    with pytest.raises(UqsError, match=r"--depth is declared by feeds a and b with different"):
        feeds.check_options({"a": a, "b": b})
    many = _feed("b", feeds.FeedOption("depth", "Levels", type=int, many=True))
    with pytest.raises(UqsError, match=r"\(int and int, many\)"):
        feeds.check_options({"a": a, "b": many})


def test_one_option_two_feeds_agree_on_is_one_option_naming_both():
    a = _feed("a", feeds.FeedOption("symbols", "Symbols", many=True, default=("X",)))
    b = _feed("b", feeds.FeedOption("symbols", "Symbols", many=True, default=("Y",)))
    (option, takers), *_ = feeds.check_options({"a": a, "b": b}).values()
    assert takers == ["a", "b"] and option.many
    assert a.start_options({})["symbols"] == ("X",), "defaults stay each feed's own"
    assert b.start_options({})["symbols"] == ("Y",)


def test_a_one_argument_start_is_refused_naming_its_module(monkeypatch):
    """A feed scaffolded before #1776 has `start(paths)`: it breaks loudly at
    discovery, not with a TypeError on the first start."""
    _discover_with(monkeypatch, "old_feed", FEED=_feed("old", start=lambda paths: 1))
    with pytest.raises(UqsError, match=r"uqs\.external\.old_feed: feed old's start\(paths\)"):
        feeds.discover()


def test_a_module_may_declare_several_feeds(monkeypatch):
    _discover_with(monkeypatch, "pair_feed", FEEDS=(_feed("one"), _feed("two")))
    found = feeds.discover()
    assert {"one", "two"} <= set(found)


def test_a_feed_name_declared_twice_is_refused(monkeypatch):
    _discover_with(monkeypatch, "twice_feed", FEEDS=(_feed("kafka"),))
    with pytest.raises(UqsError, match="'kafka' is declared twice"):
        feeds.discover()


def test_an_option_named_like_starts_own_parameter_is_refused(monkeypatch):
    _discover_with(monkeypatch, "clash_feed", FEED=_feed("c", feeds.FeedOption("dry_run", "x")))
    with pytest.raises(UqsError, match="connector start` takes for itself"):
        feeds.discover()


def test_an_option_is_its_default_unless_given_and_a_zero_is_given():
    feed = _feed(
        "f",
        feeds.FeedOption("n", "N", type=int, default=5),
        feeds.FeedOption("names", "Names", many=True, default=("a",)),
    )
    assert feed.start_options({}) == {"n": 5, "names": ("a",)}
    assert feed.start_options({"n": 0, "names": "x, y,,"}) == {"n": 0, "names": ("x", "y")}
    assert feed.start_options({"names": ""}) == {"n": 5, "names": ()}


def test_titles_and_start_messages():
    assert _feed("f").status_title == "f feed status"
    assert _feed("f", title="T").status_title == "T"
    assert _feed("f").started(3) == "f feed started (pid 3)"
    assert _feed("f", started_note="n").started(3) == "f feed started (pid 3) - n"


def test_the_dry_run_masks_a_secret_option(monkeypatch, capsys):
    from uqs.cli import external as cli_external

    feed = _feed("vault", feeds.FeedOption("api_key", "Key", secret=True),
                 feeds.FeedOption("symbols", "Symbols", many=True))  # fmt: skip
    monkeypatch.setitem(cli_external.FEEDS, "vault", feed)
    cli_external._start("vault", {"api_key": "K", "symbols": "A,B"}, dry_run=True)
    out = capsys.readouterr().out
    assert "would start vault with api_key=***, symbols=A,B" in out and "K," not in out


# --- what a connector produces, read from its text --------------------------


def test_produces_is_read_from_literals_constants_and_a_siblings_constants(tmp_path: Path):
    (tmp_path / "streamer.py").write_text('TABLE = "t_raw"\n')
    (tmp_path / "x_feed.py").write_text(
        "from uqs.external.streamer import TABLE as RAW\n"
        'WHO = "the x feed"\n'
        "FEED = ExternalFeed('x', s, s, s, produces={RAW: WHO, 'other': 'some' ' one'})\n"
    )
    assert dependencies.feed_produces(tmp_path / "x_feed.py") == {
        "t_raw": "the x feed",
        "other": "some one",
    }


def test_a_produces_the_model_cannot_read_is_refused(tmp_path: Path):
    path = tmp_path / "x_feed.py"
    path.write_text("FEED = ExternalFeed('x', s, s, s, produces={f'{a}': 'w'})\n")
    with pytest.raises(ValueError, match="not a string literal or a module-level string constant"):
        dependencies.feed_produces(path)


def test_a_module_whose_text_and_import_disagree_is_refused(monkeypatch, tmp_path: Path):
    """The warnings read the text and the CLI imports the module; a feed whose
    produces= is computed would make them describe different connectors."""
    path = tmp_path / "drift_feed.py"
    path.write_text("FEED = ExternalFeed('drift', s, s, s, produces={'a': 'w'})\n")
    feed = _feed("drift", produces={"b": "w"})
    _discover_with(monkeypatch, "drift_feed", FEED=feed, __file__=str(path))
    with pytest.raises(UqsError, match="drift_feed: what its feeds produce differs"):
        feeds.discover()


def test_every_tree_connector_reads_the_same_imported_and_as_text():
    """The two readings agree for every connector this tree has - and their
    union is what the dependency warnings use."""
    imported: dict[str, str] = {}
    for feed in feeds.discover().values():
        imported.update(feed.produces)
    assert dependencies.external_producers(Path(__file__).resolve().parents[3]) == imported
