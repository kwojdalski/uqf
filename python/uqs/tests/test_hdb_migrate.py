"""A real splayed HDB migration: renamed and widened data survives the swap."""

from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

import pytest

from uqs.paths import UqsError, UqsPaths
from uqs.stack import hdb_migrate, hdb_types

ROOT = Path(__file__).resolve().parents[3]
DAY = date(2026, 1, 1)
SCHEMA = "trade:([] time:`timestamp$(); sym:`g#`symbol$(); price:`float$())\n"
BUILD = """\
root:hsym `$first .z.x;
old:([] time:2026.01.01D10:00 2026.01.01D11:00; sym:`EURUSD`USDJPY; px:12 13j);
(` sv .Q.par[root;2026.01.01;`trade],`) set .Q.en[root;old];
exit 0
"""
READ = """\
root:hsym `$first .z.x;
sym:get ` sv root,`sym;
t:select from get hsym `$(string .Q.par[root;2026.01.01;`trade]),"/";
-1 "rows=",string count t;
-1 "columns=",.Q.s1 cols t;
-1 "price_type=",string type exec price from t;
-1 "prices=",.Q.s1 exec price from t;
-1 "symbols=",.Q.s1 exec sym from t;
exit 0
"""


def _run(q_binary, script: Path, *args: Path) -> subprocess.CompletedProcess[str]:
    q, env = q_binary
    return subprocess.run(
        [q, str(script), *(str(arg) for arg in args)],
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
        check=False,
    )


def _snapshot(table_dir: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in table_dir.iterdir() if path.is_file()}


@pytest.fixture
def hdb(tmp_path: Path, q_binary) -> UqsPaths:
    torqapp = tmp_path / "starter"
    torqapp.mkdir()
    (torqapp / "database.q").write_text(SCHEMA)
    paths = UqsPaths(
        repo_root=ROOT,
        torqhome=tmp_path / "torq",
        torqapphome=torqapp,
        torqdata=tmp_path / "runtime with space",
        scripts_dir=ROOT / "scripts",
        orchestrator_dir=tmp_path / "orchestrator",
        runtime="torq",
    )
    build = tmp_path / "build.q"
    build.write_text(BUILD)
    result = _run(q_binary, build, paths.hdb_dir)
    assert result.returncode == 0, result.stdout + result.stderr
    return paths


def test_rename_and_lossless_type_change_keep_rows_and_a_rollback_copy(hdb, q_binary):
    live = hdb.hdb_dir / "2026.01.01" / "trade"
    before = _snapshot(live)
    dry = hdb_migrate.migrate(hdb, "trade", DAY, {"px": "price"}, {"price"})
    assert dry.startswith("would migrate")
    assert _snapshot(live) == before

    report = hdb_migrate.migrate(hdb, "trade", DAY, {"px": "price"}, {"price"}, apply=True)
    backup = Path(report.split("previous table retained at ", 1)[1])
    assert backup.is_dir()
    assert _snapshot(backup) == before

    read = live.parents[2] / "read.q"
    read.write_text(READ)
    result = _run(q_binary, read, hdb.hdb_dir)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "rows=2" in result.stdout
    assert "columns=`time`sym`price" in result.stdout
    assert "price_type=9" in result.stdout
    assert "prices=12 13f" in result.stdout
    assert "EURUSD`USDJPY" in result.stdout
    assert hdb_types.type_changes(hdb, hdb.torqapphome / "database.q") == []


def test_an_unmapped_source_column_is_refused_without_replacing_it(hdb):
    live = hdb.hdb_dir / "2026.01.01" / "trade"
    before = (live / ".d").read_bytes()
    with pytest.raises(UqsError, match="source is missing price"):
        hdb_migrate.migrate(hdb, "trade", DAY, {}, {"price"}, apply=True)
    assert (live / ".d").read_bytes() == before


def test_a_changed_type_needs_an_explicit_cast(hdb):
    with pytest.raises(UqsError, match="needs --cast price"):
        hdb_migrate.migrate(hdb, "trade", DAY, {"px": "price"}, set(), apply=True)


def test_a_lossy_cast_is_refused_before_the_exchange(hdb, q_binary):
    live = hdb.hdb_dir / "2026.01.01" / "trade"
    rewrite = live.parents[2] / "rewrite.q"
    rewrite.write_text(
        "root:hsym `$first .z.x; "
        "t:([] time:2026.01.01D10:00 2026.01.01D11:00; "
        "sym:`EURUSD`USDJPY; px:12.5 13.5); "
        "(` sv .Q.par[root;2026.01.01;`trade],`) set .Q.en[root;t]; exit 0\n"
    )
    assert _run(q_binary, rewrite, hdb.hdb_dir).returncode == 0
    (hdb.torqapphome / "database.q").write_text(
        "trade:([] time:`timestamp$(); sym:`g#`symbol$(); price:`long$())\n"
    )
    before = _snapshot(live)
    with pytest.raises(UqsError, match="cast of price loses values"):
        hdb_migrate.migrate(hdb, "trade", DAY, {"px": "price"}, {"price"}, apply=True)
    assert _snapshot(live) == before


def test_exchange_failure_leaves_the_original_table(hdb, monkeypatch):
    live = hdb.hdb_dir / "2026.01.01" / "trade"
    before = _snapshot(live)

    def refuse(*_args):
        raise UqsError("exchange unavailable")

    monkeypatch.setattr(hdb_migrate, "_exchange", refuse)
    with pytest.raises(UqsError, match="exchange unavailable"):
        hdb_migrate.migrate(hdb, "trade", DAY, {"px": "price"}, {"price"}, apply=True)
    assert _snapshot(live) == before


def test_the_shared_hdb_write_lock_is_respected(hdb):
    lock = hdb.hdb_dir.with_name(hdb.hdb_dir.name + ".write.lock")
    lock.mkdir()
    with pytest.raises(UqsError, match="write lock already held"):
        hdb_migrate.migrate(hdb, "trade", DAY, {"px": "price"}, {"price"}, apply=True)
