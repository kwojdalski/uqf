"""A real splayed HDB migration: renamed and widened data survives the swap."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from uqs.interpreter import PEACHQ, q_impl
from uqs.paths import UqsError, UqsPaths
from uqs.stack import hdb_layout, hdb_migrate, hdb_types

ROOT = Path(__file__).resolve().parents[3]


def test_the_lock_modules_are_the_ones_the_lock_script_loads():
    """LOCK_MODULES is what _lock_code flattens for PeachQ, and hdb_lock.q's
    own `\\l` lines are what runs (#1795): a module only one of them names is
    loaded unflattened, or flattened for nothing."""
    from test_runs import q_loads

    proof = q_loads(ROOT / "scripts" / hdb_migrate.LOCK_SCRIPT)
    assert proof, "hdb_lock.q loads nothing this test can read"
    assert hdb_migrate.LOCK_MODULES == proof


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
    found = hdb_layout.inspect(hdb, hdb.hdb_dir).dirs
    assert hdb_types.type_changes(hdb, hdb.torqapphome / "database.q", found) == []


@pytest.mark.parametrize(
    ("renames", "casts", "said"),
    [
        ({"nosuch": "foo"}, set(), "rename target is not declared: foo"),
        ({"px": "price", "time": "sym"}, {"price"}, "two target columns read the same source"),
        ({"px": "sym"}, set(), "source is missing price"),
        ({"px": "price"}, {"price", "nosuchcol"}, "cast target is not declared: nosuchcol"),
        ({"px": "price"}, set(), "needs --cast price"),
    ],
)
def test_the_dry_run_refuses_what_apply_would_refuse(hdb, renames, casts, said):
    """The plan is the migration's own check without the write: a rename or
    cast the stage would refuse is refused by the dry run, with the stage's
    message, and changes nothing (#2463)."""
    live = hdb.hdb_dir / "2026.01.01" / "trade"
    before = _snapshot(live)
    with pytest.raises(UqsError, match=said):
        hdb_migrate.migrate(hdb, "trade", DAY, renames, casts)
    assert _snapshot(live) == before
    assert sorted(p.name for p in hdb.hdb_dir.iterdir()) == ["2026.01.01", "sym"]


def test_an_hdb_named_by_hdb_is_the_one_migrated_and_locked(hdb, tmp_path):
    """#2237: `data migrate --hdb DIR` - the root it migrates, and the one whose
    write lock it takes, is DIR, not this runtime's."""
    other = tmp_path / "elsewhere" / "hdb"
    other.parent.mkdir()
    shutil.move(hdb.hdb_dir, other)
    with pytest.raises(UqsError, match="no HDB at"):
        hdb_migrate.migrate(hdb, "trade", DAY, {"px": "price"}, {"price"})
    dry = hdb_migrate.migrate(hdb, "trade", DAY, {"px": "price"}, {"price"}, hdb=other)
    assert dry.startswith("would migrate")
    report = hdb_migrate.migrate(
        hdb, "trade", DAY, {"px": "price"}, {"price"}, apply=True, hdb=other
    )
    backup = Path(report.split("previous table retained at ", 1)[1])
    assert backup.is_relative_to(other.parent)
    assert not hdb.hdb_dir.exists()


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


# --reset-empty (#2251): an empty table with an obsolete schema, rebuilt from
# the declaration through the same lock, staging, validation and exchange.
EMPTY_BUILD = """\
root:hsym `$first .z.x;
mk:{[root;d;t] (` sv .Q.par[root;d;`trade],`) set .Q.en[root;t]};
/ an obsolete schema: px and an extra column no longer declared, empty
mk[root;2026.01.01;([] time:`timestamp$(); sym:`symbol$(); px:`long$(); legacy:`long$())];
/ a populated neighbour and the shared sym file
mk[root;2026.01.02;([] time:enlist 2026.01.02D10:00; sym:enlist `EURUSD;
  px:enlist 1j; legacy:enlist 7j)];
exit 0
"""


@pytest.fixture
def empty_hdb(hdb, q_binary, tmp_path) -> UqsPaths:
    import shutil

    shutil.rmtree(hdb.hdb_dir / "2026.01.01")
    build = tmp_path / "empty_build.q"
    build.write_text(EMPTY_BUILD)
    result = _run(q_binary, build, hdb.hdb_dir)
    assert result.returncode == 0, result.stdout + result.stderr
    return hdb


def _tree(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


def _reset(paths: UqsPaths, **kw) -> str:
    return hdb_migrate.migrate(paths, "trade", DAY, {}, set(), reset_empty=True, **kw)


def _lock(paths: UqsPaths) -> Path:
    return paths.hdb_dir.with_name(paths.hdb_dir.name + ".write.lock")


def test_reset_empty_dry_run_reports_the_change_and_writes_nothing(empty_hdb):
    before = _tree(empty_hdb.hdb_dir)
    report = _reset(empty_hdb)
    assert report.startswith("would migrate 2026.01.01/trade: reset empty table")
    assert "legacy:7" in report and "price:9" in report
    assert _tree(empty_hdb.hdb_dir) == before
    assert not _lock(empty_hdb).exists()


def test_reset_empty_rebuilds_from_the_declaration_and_keeps_the_rest(empty_hdb, q_binary):
    root = empty_hdb.hdb_dir
    live = root / "2026.01.01" / "trade"
    original = _snapshot(live)
    neighbour = _tree(root / "2026.01.02")
    sym = (root / "sym").read_bytes()

    report = _reset(empty_hdb, apply=True)
    backup = Path(report.split("previous table retained at ", 1)[1])
    assert _snapshot(backup) == original
    assert (root / "sym").read_bytes() == sym
    assert _tree(root / "2026.01.02") == neighbour
    assert not _lock(empty_hdb).exists()

    read = root.parent / "read.q"
    read.write_text(
        "root:hsym `$first .z.x; "
        't:select from get hsym `$(string .Q.par[root;2026.01.01;`trade]),"/"; '
        '-1 "cols=",.Q.s1 cols t; -1 "types=",.Q.s1 type each exec time,sym,price from t; '
        '-1 "rows=",string count t; -1 "attr=",string attr exec sym from t; exit 0'
    )
    out = _run(q_binary, read, root).stdout
    assert "cols=`time`sym`price" in out and "rows=0" in out
    assert "types=`time`sym`price!12 20 9h" in out
    if q_impl() != PEACHQ:  # PeachQ keeps no attribute on a splayed column; KDB-X judges it
        assert "attr=g" in out
    found = hdb_layout.inspect(empty_hdb, root).dirs
    assert hdb_types.type_changes(empty_hdb, empty_hdb.torqapphome / "database.q", found) == []


def _write_q(root: Path, q_binary, code: str) -> None:
    script = root.parent / "mutate.q"
    script.write_text("root:hsym `$first .z.x; " + code + " exit 0\n")
    result = _run(q_binary, script, root)
    assert result.returncode == 0, result.stdout + result.stderr


def test_reset_empty_refuses_a_populated_column(empty_hdb, q_binary):
    # Only the OBSOLETE column holds a row: still a refusal.
    root = empty_hdb.hdb_dir
    _write_q(
        root,
        q_binary,
        "d:.Q.par[root;2026.01.01;`trade]; (` sv d,`legacy) set enlist 7j;",
    )
    before = _tree(root)
    for apply in (False, True):
        with pytest.raises(UqsError, match="populated column.*legacy"):
            _reset(empty_hdb, apply=apply)
    assert _tree(root) == before
    assert not _lock(empty_hdb).exists()


def test_reset_empty_refuses_an_unreadable_column(empty_hdb):
    root = empty_hdb.hdb_dir
    (root / "2026.01.01" / "trade" / "px").write_bytes(b"not a q file")
    before = _tree(root)
    with pytest.raises(UqsError, match="unreadable column.*px"):
        _reset(empty_hdb, apply=True)
    assert _tree(root) == before
    assert not _lock(empty_hdb).exists()


def test_reset_empty_refuses_a_file_the_table_does_not_list(empty_hdb):
    stray = empty_hdb.hdb_dir / "2026.01.01" / "trade" / "orphan"
    stray.write_bytes(b"x")
    with pytest.raises(UqsError, match="files .d does not list: orphan"):
        _reset(empty_hdb)


def test_reset_empty_cannot_be_combined_with_rename_or_cast(empty_hdb):
    before = _tree(empty_hdb.hdb_dir)
    with pytest.raises(UqsError, match="cannot be combined with --rename or --cast"):
        hdb_migrate.migrate(
            empty_hdb, "trade", DAY, {"px": "price"}, set(), reset_empty=True, apply=True
        )
    with pytest.raises(UqsError, match="cannot be combined with --rename or --cast"):
        hdb_migrate.migrate(empty_hdb, "trade", DAY, {}, {"price"}, reset_empty=True)
    assert _tree(empty_hdb.hdb_dir) == before


def test_reset_empty_respects_the_writer_lock(empty_hdb):
    _lock(empty_hdb).mkdir()
    before = _tree(empty_hdb.hdb_dir)
    with pytest.raises(UqsError, match="write lock already held"):
        _reset(empty_hdb, apply=True)
    assert _tree(empty_hdb.hdb_dir) == before


def test_reset_empty_rechecks_emptiness_under_the_lock(empty_hdb, q_binary, monkeypatch):
    """A row published after the plan but before staging refuses the apply."""
    root = empty_hdb.hdb_dir
    acquire = hdb_migrate._write_lock

    def populate_then_lock(paths, q, env, action, *rest):
        if action == "acquire":
            _write_q(
                root,
                q_binary,
                "d:.Q.par[root;2026.01.01;`trade]; (` sv d,`px) set enlist 5j;",
            )
        return acquire(paths, q, env, action, *rest)

    monkeypatch.setattr(hdb_migrate, "_write_lock", populate_then_lock)
    with pytest.raises(UqsError, match="populated column.*px"):
        _reset(empty_hdb, apply=True)
    assert not _lock(empty_hdb).exists()


def test_reset_empty_failure_leaves_the_original_and_releases_the_lock(empty_hdb, monkeypatch):
    root = empty_hdb.hdb_dir
    before = _tree(root)

    def refuse(*_args):
        raise UqsError("exchange unavailable")

    monkeypatch.setattr(hdb_migrate, "_exchange", refuse)
    with pytest.raises(UqsError, match="exchange unavailable"):
        _reset(empty_hdb, apply=True)
    assert _tree(root) == before
    assert not _lock(empty_hdb).exists()
    assert not list(root.parent.glob("*.migration-*"))


def test_the_cli_passes_reset_empty_through_and_keeps_the_dry_run_default(monkeypatch):
    from typer.testing import CliRunner

    from uqs.cli.entry import app

    seen = []

    def migrate(_paths, table, day, renames, casts, **kw):
        seen.append((table, day, renames, casts, kw))
        return "plan"

    monkeypatch.setattr(hdb_migrate, "migrate", migrate)
    runner = CliRunner()
    assert (
        runner.invoke(
            app, ["data", "migrate", "trade", "--date", "2026-01-01", "--reset-empty"]
        ).exit_code
        == 0
    )
    assert seen == [("trade", DAY, {}, set(), {"apply": False, "reset_empty": True, "hdb": None})]
    seen.clear()
    result = runner.invoke(
        app,
        ["data", "migrate", "trade", "--date", "2026-01-01", "--reset-empty", "--apply", "--yes"],
    )
    assert result.exit_code == 0, result.output
    assert seen == [("trade", DAY, {}, set(), {"apply": True, "reset_empty": True, "hdb": None})]


# ----------------------------------------- the command: --json, --yes (#2229, #2247)


@pytest.fixture
def migrating(monkeypatch):
    """Each hdb_migrate.migrate call's `apply`."""
    calls: list[bool] = []

    def migrate(paths, table, day, renames, casts, *, apply=False, hdb=None, reset_empty=False):
        calls.append(apply)
        return f"plan for {table}"

    monkeypatch.setattr(hdb_migrate, "migrate", migrate)
    return calls


def _migrate(*argv: str, **kw):
    from typer.testing import CliRunner

    from uqs.cli import app

    line = ["data", "migrate", "quote", "--date", "2026.09.13", "--rename", "bid:bid2", *argv]
    return CliRunner().invoke(app, line, **kw)


def test_the_json_document_is_the_plan(migrating):
    import json

    result = _migrate("--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "ok": True, "table": "quote", "partition": "2026.09.13", "applied": False,
        "report": "plan for quote",
    }  # fmt: skip
    assert migrating == [False]


def test_json_without_yes_apply_is_refused(migrating, terminal, monkeypatch):
    """#2229: --json never asks, even at a terminal that would say y."""
    import json

    argv = ["data", "migrate", "quote", "--date", "2026.09.13", "--rename", "bid:bid2"]
    argv += ["--apply", "--json"]
    monkeypatch.setattr("sys.argv", ["uqs", *argv])  # json_requested reads it
    result = _migrate("--apply", "--json", input="y\n")
    assert result.exit_code == 1
    doc = json.loads(result.stdout)
    assert (
        doc["ok"] is False and "cannot ask for confirmation: add --yes to migrate" in doc["error"]
    )
    assert True not in migrating


def test_apply_yes_migrates_without_asking(migrating):
    assert _migrate("--apply", "--yes").exit_code == 0
    assert migrating == [True]
