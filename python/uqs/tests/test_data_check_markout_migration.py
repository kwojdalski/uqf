"""`uqs data check --apply` folds a markout partition's configuration columns into
snapshots, on a disposable HDB (#1999).

Before snapshots a markout row repeated contract_version, rate_timing and report_ccy.
Now it carries one `config_id`. History written the old way is migrated by the one
command that already repairs partitions, after it asks: per partition, one LEGACY
snapshot for each distinct triple of the three columns, a config_id column naming each
row's, and the three columns dropped. Everything here is judged from outside: the HDB
by what q loads, the store by `uqs data snapshot`, which reads only q's own file.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from uqs import cli
from uqs.cli import hdb_check
from uqs.cli import snapshot as snapshot_cli
from uqs.paths import UqsPaths

ROOT = Path(__file__).resolve().parents[3]

#: The starter pack's own table, declared beside the plant's; nothing here uses it.
VENDORED = "quote:([] sym:`symbol$(); bid:`float$())\n"

#: The runtime's own schema is the plant's: the check judges a partition against
#: crypto_markout as src/etl/plant_tables.q declares it, so the fixture is that table
#: as it stood before the change - every column but config_id - plus the three that
#: were folded. argv[0] is the HDB root.
BUILD = r"""
\l src/etl/plant_registry.q
\l src/etl/plant_tables.q
root:hsym `$first .z.x;
d:delete config_id from 0#.qetl.plant.crypto_markout;
rows:{[d;cv;rt;rc]
    n:count cv;
    t:flip (cols d)!{[n;v] n#first v}[n;] each d cols d;
    t:update time:n#2026.01.01D10:00:00.000000000,
        fill_id:(`$("f",/:string til n)), status:n#`ok from t;
    t,'([] contract_version:cv; rate_timing:rt; report_ccy:rc)}[d];
put:{[root;d;data] (` sv .Q.par[root;d;`crypto_markout],`) set .Q.en[root;data]};
put[root;2026.01.01;rows[3#1;3#`execution;3#`USD]];
put[root;2026.01.02;rows[1 1 1 1;`execution`horizon`horizon`execution;`USD`EUR`EUR`USD]];
exit 0
"""

#: What q loads from the migrated HDB: the columns, and the config_id of each row.
READ = r"""
\c 2000 2000
system"l ",first .z.x;
-1 "COLS=",.Q.s1 (cols crypto_markout) except `date;
-1 "IDS=",.Q.s1 exec config_id from select distinct config_id from crypto_markout;
-1 "ROWS=",string count crypto_markout;
-1 "FIRST=",.Q.s1 exec config_id from select config_id from crypto_markout where date=2026.01.01;
exit 0
"""


def _q(q_binary, script: str, *args: str, tmp: Path, status: Path | None = None) -> str:
    q, env = q_binary
    file = tmp / f"{abs(hash(script))}.q"
    file.write_text(script)
    run_env = {**env, **({"UQF_STATUS_DIR": str(status)} if status else {})}
    result = subprocess.run(
        [q, str(file), *args, "-q"],
        capture_output=True,
        text=True,
        env=run_env,
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.fixture
def runtime(tmp_path: Path, q_binary, monkeypatch) -> tuple[UqsPaths, Path]:
    """A runtime whose HDB holds the old-shape partitions, and its status directory."""
    data = tmp_path / "data"
    hdb = data / "hdb"
    hdb.mkdir(parents=True)
    _q(q_binary, BUILD, str(hdb), tmp=tmp_path)
    app = tmp_path / "app"
    app.mkdir()
    (app / "database.q").write_text(VENDORED)
    status = tmp_path / "status"
    paths = UqsPaths(
        repo_root=ROOT,
        torqhome=tmp_path / "torq",
        torqapphome=app,
        torqdata=data,
        scripts_dir=ROOT / "scripts",
        orchestrator_dir=tmp_path / "orchestrator",
        runtime="uqf",
    )
    for module in (hdb_check, snapshot_cli):
        monkeypatch.setattr(module, "_paths", lambda: paths)
    q, env = q_binary
    monkeypatch.setenv("QCMD", q)
    monkeypatch.setenv("QHOME", env["QHOME"])
    monkeypatch.setenv("UQF_STATUS_DIR", str(status))
    return paths, status


def _uqs(monkeypatch, *argv: str) -> tuple[int, dict]:
    monkeypatch.setattr(sys, "argv", ["uqs", *argv])
    result = CliRunner().invoke(cli.app, list(argv), env={"COLUMNS": "300"})
    out = result.stdout
    assert "{" in out, (result.exit_code, out, repr(result.exception))
    return result.exit_code, json.loads(out[out.index("{") :])


def test_check_reports_the_missing_config_id_and_changes_nothing(runtime, monkeypatch):
    paths, status = runtime
    code, doc = _uqs(monkeypatch, "data", "check", "--json")
    assert code == 1 and doc["ok"] is False, doc
    assert doc["missing_columns"] == {
        "2026.01.01": {"crypto_markout": ["config_id"]},
        "2026.01.02": {"crypto_markout": ["config_id"]},
    }
    assert not status.exists(), "a check records no snapshot"


def test_apply_folds_the_columns_into_legacy_snapshots_and_the_hdb_loads(
    runtime, monkeypatch, q_binary, tmp_path
):
    paths, status = runtime
    code, doc = _uqs(monkeypatch, "data", "check", "--apply", "--yes", "--json")
    assert code == 0, doc
    assert doc["ok"] is True and doc["missing_columns"] == {}

    out = _q(q_binary, READ, str(paths.hdb_dir), tmp=tmp_path)
    assert "config_id" in out.split("COLS=")[1].splitlines()[0]
    cols = out.split("COLS=")[1].splitlines()[0]
    assert not any(c in cols for c in ("contract_version", "rate_timing", "report_ccy")), cols
    assert "ROWS=7" in out

    code, snaps = _uqs(
        monkeypatch, "data", "snapshot", "list", "--dataset", "crypto_markout", "--json"
    )
    assert code == 0 and snaps["ok"] is True
    assert len(snaps["snapshots"]) == 2, (
        "two distinct triples, though three partitions' rows share one"
    )
    assert all(s["id"].startswith("markout_legacy_") for s in snaps["snapshots"])
    assert all(
        s["reason"].startswith("legacy: ") and s["actor"] == "fill_hdb_partitions"
        for s in snaps["snapshots"]
    )
    assert all(s["digest_ok"] for s in snaps["snapshots"])

    triples = set()
    for s in snaps["snapshots"]:
        code, shown = _uqs(monkeypatch, "data", "snapshot", "show", s["id"], "--json")
        assert code == 0
        p = shown["parameters"]
        assert p["legacy"] == "1b"
        triples.add((p["contract_version"], p["rate_timing"], p["report_ccy"]))
    assert triples == {("1", "execution", "USD"), ("1", "horizon", "EUR")}, (
        "the triples the old columns held, read back from the snapshots the rows now name"
    )
    # every config_id the HDB now holds resolves to a recorded snapshot
    for line in out.splitlines():
        if line.startswith("IDS="):
            named = {
                w
                for w in line.replace("`", " ").replace(",", " ").split()
                if w.startswith("markout_legacy_")
            }
    assert named == {s["id"] for s in snaps["snapshots"]}


def test_a_second_apply_changes_nothing(runtime, monkeypatch):
    paths, status = runtime
    assert _uqs(monkeypatch, "data", "check", "--apply", "--yes", "--json")[0] == 0
    before = sorted(
        (p.relative_to(paths.hdb_dir), p.read_bytes())
        for p in paths.hdb_dir.rglob("*")
        if p.is_file()
    )
    store = (status / "etl_config_snapshots").read_bytes()
    code, doc = _uqs(monkeypatch, "data", "check", "--apply", "--yes", "--json")
    assert code == 0 and doc["ok"] is True
    after = sorted(
        (p.relative_to(paths.hdb_dir), p.read_bytes())
        for p in paths.hdb_dir.rglob("*")
        if p.is_file()
    )
    assert after == before
    assert (status / "etl_config_snapshots").read_bytes() == store
