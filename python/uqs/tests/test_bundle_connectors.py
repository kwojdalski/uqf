"""A sidecar bundle carries its connectors (#1776): installed with its q into
src/etl/connectors/<bundle>/, listed in the ledger, and discovered from there."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_bundles import make_bundle, repo  # noqa: F401 - the fixture
from typer.testing import CliRunner

from uqs import cli
from uqs.cli import external as cli_external
from uqs.external import feeds
from uqs.model import connectors, dependencies
from uqs.paths import INSTALLED_CONNECTORS
from uqs.stack import bundles
from uqs.stack.bundles import LEDGER, BundleError

FEED = """from uqs.external.feeds import ExternalFeed, FeedOption

RAW = "{table}"
NOTE = "{note}"


def start(paths, options):
    return 7


def stop(paths):
    return None


def status(paths):
    return {{"running": "False", "pid": "", "note": NOTE}}


FEED = ExternalFeed(
    "{name}",
    start,
    stop,
    status,
    options=(FeedOption("piggy_rate", "Rows a second", type=int, default=3),),
    produces={{RAW: "the piggy feed"}},
)
"""


def _bundle(where: Path, name: str = "piggybank", feed: str = "piggy", note: str = "v1") -> Path:
    folder = make_bundle(where / name, name=name)
    if name != "piggybank":  # its own jobs and table: two bundles may not ship one
        for path in list(folder.iterdir()):
            if path.suffix in (".q", ".csv"):
                path.with_name(path.name.replace("piggy", name)).write_text(
                    path.read_text().replace("piggy", name)
                )
                if "piggy" in path.name:
                    path.unlink()
    table = f"{'piggy' if name == 'piggybank' else name}_tape"
    (folder / f"{feed}_feed.py").write_text(FEED.format(name=feed, note=note, table=table))
    (folder / f"{feed}_streamer.py").write_text("print('streaming')\n")
    (folder / f"test_{feed}_feed.py").write_text("def test_x():\n    pass\n")
    return folder


def _install(folder: Path, root: Path) -> dict:
    return bundles.install(bundles.plan(bundles.read_bundle(folder), root), root)


def test_install_copies_the_connectors_and_lists_them_in_the_ledger(repo, tmp_path):  # noqa: F811
    entry = _install(_bundle(tmp_path), repo)
    assert entry["connectors"] == [
        str(INSTALLED_CONNECTORS / "piggybank" / "piggy_feed.py"),
        str(INSTALLED_CONNECTORS / "piggybank" / "piggy_streamer.py"),
    ], "the feed and the streamer it starts beside itself travel together; tests do not"
    assert (repo / INSTALLED_CONNECTORS / "piggybank" / "piggy_streamer.py").is_file()
    assert not (repo / INSTALLED_CONNECTORS / "piggybank" / "test_piggy_feed.py").exists()
    ledger = json.loads((repo / LEDGER).read_text())
    assert ledger["piggybank"]["connectors"] == entry["connectors"]


def test_reinstalling_replaces_the_connectors(repo, tmp_path):  # noqa: F811
    folder = _bundle(tmp_path)
    _install(folder, repo)
    (folder / "piggy_streamer.py").unlink()
    entry = _install(folder, repo)
    assert entry["connectors"] == [str(INSTALLED_CONNECTORS / "piggybank" / "piggy_feed.py")]
    assert not (repo / INSTALLED_CONNECTORS / "piggybank" / "piggy_streamer.py").exists()


def test_discovery_reads_the_installed_copy_never_the_bundle_folder(repo, tmp_path):  # noqa: F811
    """Editing a bundle's feed without reinstalling changes nothing: its q and
    its connector are always one version."""
    folder = _bundle(tmp_path, note="v1")
    _install(folder, repo)
    (folder / "piggy_feed.py").write_text(FEED.format(name="piggy", note="v2", table="piggy_tape"))
    assert feeds.discover(repo)["piggy"].status(None)["note"] == "v1"  # ty: ignore[invalid-argument-type]
    _install(folder, repo)
    assert feeds.discover(repo)["piggy"].status(None)["note"] == "v2"  # ty: ignore[invalid-argument-type]


def test_only_ledger_listed_connectors_are_imported(repo, tmp_path):  # noqa: F811
    stray = repo / INSTALLED_CONNECTORS / "nobody" / "stray_feed.py"
    stray.parent.mkdir(parents=True)
    stray.write_text("raise SystemExit('imported')\n")
    assert "stray" not in feeds.discover(repo)


def test_an_installed_connector_is_what_the_dependency_warnings_read(repo, tmp_path):  # noqa: F811
    assert "piggy_tape" not in dependencies.external_producers(repo)
    _install(_bundle(tmp_path), repo)
    assert dependencies.external_producers(repo)["piggy_tape"] == "the piggy feed"


def test_a_connector_that_raises_on_import_is_reported_and_the_rest_still_load(
    repo,  # noqa: F811
    tmp_path,
):
    _install(_bundle(tmp_path), repo)
    _install(_bundle(tmp_path, name="sowbank", feed="sow"), repo)
    copy = repo / INSTALLED_CONNECTORS / "sowbank" / "sow_feed.py"
    copy.write_text(copy.read_text() + "\nraise RuntimeError('no licence')\n")
    broken: dict[str, str] = {}
    found = feeds.discover(repo, broken)
    assert "piggy" in found and "kafka" in found, "every other connector registers"
    assert "sow" not in found
    assert "RuntimeError: no licence" in broken["sow"]
    assert "src/etl/connectors/sowbank/sow_feed.py (bundle sowbank)" in broken["sow"]


def test_starting_a_broken_connector_names_it(monkeypatch):
    monkeypatch.setitem(cli_external.BROKEN, "sow", "connector x (bundle sowbank) does not load")
    said: list[str] = []
    monkeypatch.setattr(cli_external, "_die", lambda exc: said.append(str(exc)))
    result = CliRunner().invoke(cli.app, ["connector", "start", "sow", "-n"])
    assert result.exit_code == 1
    assert said == ["connector x (bundle sowbank) does not load"]
    status = CliRunner().invoke(cli.app, ["connector", "status", "--json"])
    assert json.loads(status.stdout)["broken"] == [
        {"name": "sow", "error": "connector x (bundle sowbank) does not load"}
    ]


def test_a_name_another_bundle_declares_is_reported_not_raised(repo, tmp_path):  # noqa: F811
    _install(_bundle(tmp_path), repo)
    other = _bundle(tmp_path, name="sowbank", feed="sow")
    _install(other, repo)
    # Bypass the install-time refusal, as a hand edit would.
    copy = repo / INSTALLED_CONNECTORS / "sowbank" / "sow_feed.py"
    copy.write_text(copy.read_text().replace('"sow",', '"piggy",'))
    broken: dict[str, str] = {}
    assert "piggy" in feeds.discover(repo, broken)
    assert "declared twice" in broken["piggy"]


# -------------------------------------------------- refused at install time


def test_a_name_uqs_already_has_is_refused(repo, tmp_path):  # noqa: F811
    own = repo / connectors.CONNECTOR_DIR / "kafka_feed.py"
    own.parent.mkdir(parents=True)
    own.write_text('FEED = ExternalFeed("kafka", s, s, s)\n')
    with pytest.raises(BundleError, match="connector 'kafka' is already uqs's"):
        bundles.plan(bundles.read_bundle(_bundle(tmp_path, feed="kafka")), repo)


def test_a_name_another_bundle_has_is_refused(repo, tmp_path):  # noqa: F811
    _install(_bundle(tmp_path), repo)
    copycat = _bundle(tmp_path, name="copycat", feed="piggy")
    with pytest.raises(BundleError, match="connector 'piggy' is already bundle piggybank's"):
        bundles.plan(bundles.read_bundle(copycat), repo)


def test_a_connector_producing_a_table_its_bundle_does_not_define_is_refused(
    repo,  # noqa: F811
    tmp_path,
):
    folder = _bundle(tmp_path)
    text = (folder / "piggy_feed.py").read_text()
    (folder / "piggy_feed.py").write_text(text.replace('RAW = "piggy_tape"', 'RAW = "quote"'))
    with pytest.raises(BundleError, match="produces quote, which the bundle's tables.q"):
        bundles.plan(bundles.read_bundle(folder), repo)


#: The SIDECAR_EXTERNAL_PRODUCERS wording before #1776: `uqs start`'s warnings
#: about an unfed raw table read the same once each bundle carries its connector.
BEFORE = {
    "databento_mbp10": (
        "the external Databento feed handler (`uqs connector start databento`) "
        "or a `databento_backfill1` run"
    ),
    "flink_vwap_raw": "the external flink_vwap feed (`uqs connector start flink_vwap`)",
}


def test_the_moved_connectors_produce_what_uqs_used_to_say(tmp_path):
    root = tmp_path / "tree"
    sidecars = Path(__file__).resolve().parents[3] / "sidecars"
    ledger: dict[str, dict] = {}
    for bundle, feed in (("databento", "databento_feed.py"), ("flink", "flink_vwap_feed.py")):
        copy = root / INSTALLED_CONNECTORS / bundle / feed
        copy.parent.mkdir(parents=True)
        copy.write_text((sidecars / bundle / feed).read_text())
        ledger[bundle] = {"connectors": [str(INSTALLED_CONNECTORS / bundle / feed)]}
    (root / LEDGER).write_text(json.dumps(ledger))
    produced = dependencies.external_producers(root)
    assert {t: produced[t] for t in BEFORE} == BEFORE
    assert not BEFORE.keys() & dependencies.external_producers(tmp_path), "uninstalled: nothing"
