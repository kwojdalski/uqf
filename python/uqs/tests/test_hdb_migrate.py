"""A real splayed HDB migration: renamed and widened data survives the swap."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
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


# A backfill writer on the same root (#1168): .qetl.io.locked, the lock each
# of .qetl.io.hdb's write, flush, finish and recover takes, given the root
# as the stack would give it and waiting half a second for a live holder.
# The two modules alone, so it loads on PeachQ from a flattened copy too.
WRITER = """\
\\l src/etl/core/backfill_state.q
\\l src/etl/core/io_hdb.q
.qetl.io.hdb_lock_wait:0D00:00:00.5;
root:hsym `$first .z.x;
-1 "LOCK|",.qetl.io.hdb_lock root;
-1 @[{.qetl.io.locked[x;{[r] "WROTE"};enlist x]};root;{"REFUSED|",x}];
exit 0
"""

# The spellings a writer's root arrives in, relative to the HDB's parent.
SPELLINGS = {
    "absolute": lambda hdb_dir: str(hdb_dir),
    "trailing slash": lambda hdb_dir: str(hdb_dir) + "/",
    "relative": lambda _hdb_dir: "hdb",
    "relative, trailing slash": lambda _hdb_dir: "hdb/",
}


def _write_as_a_q_writer(q_binary, tmp_path: Path, paths: UqsPaths, spelling: str) -> str:
    q, env = q_binary
    script = tmp_path / "writer.q"
    script.write_text(WRITER)
    hdb_dir = paths.hdb_dir
    # Run where the lock's own q loads from (the checkout, or PeachQ's
    # flattened copy); a relative root is joined to the HDB's parent from there.
    with hdb_migrate._lock_code(paths, env) as code:
        root = SPELLINGS[spelling](hdb_dir)
        if not root.startswith("/"):
            # Both resolved: q's cwd is the physical directory, and a temp
            # directory's name may pass through a link (/var on macOS).
            root = os.path.relpath(hdb_dir.parent.resolve(), code.resolve()) + "/" + root
        result = subprocess.run(
            [q, str(script), root],
            capture_output=True,
            text=True,
            env=env,
            cwd=code,
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=120,
        )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.mark.parametrize("spelling", list(SPELLINGS))
def test_a_live_migration_holds_the_lock_a_q_writer_takes_for_any_spelling(
    hdb, q_binary, tmp_path, monkeypatch, spelling
):
    seen: dict[str, str] = {}
    exchange = hdb_migrate._exchange

    def write_mid_migration(left: Path, right: Path) -> None:
        seen["writer"] = _write_as_a_q_writer(q_binary, tmp_path, hdb, spelling)
        exchange(left, right)

    monkeypatch.setattr(hdb_migrate, "_exchange", write_mid_migration)
    hdb_migrate.migrate(hdb, "trade", DAY, {"px": "price"}, {"price"}, apply=True)

    lock = hdb.hdb_dir.with_name(hdb.hdb_dir.name + ".write.lock")
    writer_lock = seen["writer"].split("LOCK|", 1)[1].splitlines()[0]
    assert Path(writer_lock).resolve() == lock.resolve()
    assert "REFUSED|" in seen["writer"] and "could not take the lock" in seen["writer"]
    assert not lock.exists()


KILLED_MIGRATION = """\
import os, signal, sys
from datetime import date
from pathlib import Path
from uqs.paths import UqsPaths
from uqs.stack import hdb_migrate

root = Path(sys.argv[1])
paths = UqsPaths(
    repo_root=Path(sys.argv[2]),
    torqhome=root / "torq",
    torqapphome=root / "starter",
    torqdata=root / "runtime with space",
    scripts_dir=Path(sys.argv[2]) / "scripts",
    orchestrator_dir=root / "orchestrator",
    runtime="torq",
)
hdb_migrate._exchange = lambda *_: os.kill(os.getpid(), signal.SIGKILL)
hdb_migrate.migrate(paths, "trade", date(2026, 1, 1), {"px": "price"}, {"price"}, apply=True)
"""


def test_a_killed_migrations_lock_is_broken_by_the_next_q_writer(hdb, q_binary, tmp_path):
    script = tmp_path / "killed.py"
    script.write_text(KILLED_MIGRATION)
    _, env = q_binary
    killed = subprocess.run(
        [sys.executable, str(script), str(tmp_path), str(ROOT)],
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
        check=False,
        timeout=300,
    )
    assert killed.returncode == -signal.SIGKILL, killed.stdout + killed.stderr
    lock = hdb.hdb_dir.with_name(hdb.hdb_dir.name + ".write.lock")
    assert lock.is_dir(), "the killed migration left its lock behind"

    out = _write_as_a_q_writer(q_binary, tmp_path, hdb, "absolute")
    assert "WROTE" in out, out
    assert not lock.exists()
