"""`uqs data compress --report`, end to end on a real q (#2149).

Each case builds a temporary HDB with q: four days of `trade`, the two oldest
written as TorQ's wdb leaves a partition (sorted by sym then time, `p#sym`)
with `px` and `qty` compressed, one day whose rows are out of order and carry
no attribute, and one in order without `p#`. The command then runs as an
operator runs it, through the CLI, against TorQ's own sort.csv and `.cmp`, and
the files are compared byte for byte afterwards. The compressed columns are
gzip, written by `set` with a compression triple, so the cases run on PeachQ
too (#2153) - all but the one that lists every column, since PeachQ keeps `p#`
in a side file TorQ's walker counts as a column (python/peachq_known_gaps.txt).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

import uqs.cli.entry  # noqa: F401 - registers every command on the app
from uqs.cli import compress as compress_cli
from uqs.cli import shared
from uqs.cli.shared import app

BUILD_Q = r"""
r:hsym `$getenv`HDB;
n:10000;
day:{[n;in_order;with_attr]
    t:`sym`time xasc ([] sym:n?`aa`bb`cc; time:n?0D23:59; px:n#1.5);
    t:update qty:til n from t;
    if[not in_order; t:t neg[n]?n];
    $[with_attr; update `p#sym from t; t]}[n];
put:{[r;d;t] (` sv r,(`$string d),`trade`) set .Q.en[r] t};
days:2026.01.01+til 4;
{[r;d;o;a] put[r;d;day[o;a]]}[r]'[days;1101b;1100b];
/ px at gzip level 6, qty at level 1, on the two oldest days only - a set with a
/ compression triple, the one way PeachQ writes a compressed file (#2153)
{[r;d;c;lvl] f:` sv r,(`$string d),`trade,c; v:get f; hdel f; (f;17;2;lvl) set v}[r]'[
    days 0 0 1 1;`px`qty`px`qty;6 1 6 1];
exit 0
"""


@pytest.fixture
def hdb(q_binary, monkeypatch, tmp_path) -> Path:
    q, env = q_binary
    for key in ("QHOME", "QLIC"):
        if env.get(key):
            monkeypatch.setenv(key, env[key])
    monkeypatch.setenv("QCMD", q)
    root = tmp_path / "hdb"
    script = tmp_path / "build.q"
    script.write_text(BUILD_Q)
    subprocess.run([q, str(script), "-q"], env={**env, "HDB": str(root)},
                   stdin=subprocess.DEVNULL, capture_output=True, check=True,
                   timeout=60)  # fmt: skip
    return root


def _digest(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def run_uqs(*argv: str):
    return CliRunner().invoke(app, list(argv), env={"COLUMNS": "300"})


def test_the_report_reads_each_columns_compression_and_names_the_unsorted_partitions(hdb):
    before = _digest(hdb)
    r = run_uqs("data", "compress", "--report", "--json", "--hdb", str(hdb))
    assert r.exit_code == 0, r.output
    doc = json.loads(r.stdout)
    assert doc["present"] and doc["hdb"] == str(hdb.resolve())
    cols = {c["column"]: c for c in doc["columns"]}
    assert set(cols) == {"sym", "time", "px", "qty"}
    assert (cols["px"]["files"], cols["px"]["compressed"]) == (4, 2)
    assert sorted(cols["px"]["algos"]) == [0, 2] and sorted(cols["qty"]["algos"]) == [0, 2]
    assert cols["px"]["blocksizes"] == [17]
    assert cols["px"]["compressed_ratio"] > 50, "a constant column shrinks by orders of magnitude"
    assert cols["sym"]["compressed"] == 0 and cols["sym"]["compressed_ratio"] is None
    big = doc["largest_uncompressed"]
    assert [p["files"] for p in big] == [4, 4, 2, 2], "the plain days first, largest first"
    assert sorted(p["partition"] for p in big[:2]) == ["2026-01-03", "2026-01-04"]
    unsorted = {u["partition"]: u for u in doc["unsorted"]}
    assert set(unsorted) == {"2026-01-03", "2026-01-04"}
    assert unsorted["2026-01-03"]["sorted"] is False
    assert unsorted["2026-01-03"]["sort_by"] == ["sym", "time"]
    assert unsorted["2026-01-04"]["sorted"] is True
    assert unsorted["2026-01-04"]["missing_attr"] == ["sym"]
    assert unsorted["2026-01-04"]["declared_attr"] == {"sym": "p"}
    assert _digest(hdb) == before, "the report wrote nothing and sorted nothing"


def test_the_limit_bounds_the_largest_uncompressed_partitions(hdb):
    r = run_uqs("data", "compress", "--report", "--json", "--limit", "1", "--hdb", str(hdb))
    assert r.exit_code == 0, r.output
    assert len(json.loads(r.stdout)["largest_uncompressed"]) == 1


def test_the_report_as_a_person_reads_it(hdb):
    r = run_uqs("data", "compress", "--report", "--hdb", str(hdb))
    assert r.exit_code == 0, r.output
    assert "compression by column" in r.stdout
    assert "largest uncompressed partitions" in r.stdout
    assert "2026.01.03" in r.stdout or "2026-01-03" in r.stdout
    assert "not sorted by sym, time" in r.stdout
    assert "missing p#sym" in r.stdout


def test_without_a_mode_it_is_a_usage_error_and_reads_nothing(tmp_path, monkeypatch):
    said: list[str] = []

    class Log:
        def error(self, message, *args):
            said.append(str(message).format(*args))

        def __getattr__(self, _name):
            return lambda *a, **k: None

    monkeypatch.setattr(shared, "log", Log())
    r = run_uqs("data", "compress", "--hdb", str(tmp_path))
    assert r.exit_code == 2
    assert any("--report" in s for s in said), said


def _attached(monkeypatch, where: Path, ssh: str | None) -> None:
    """--attachment ATT resolving to `where`, on `ssh` (None: this machine)."""
    from uqs.cli import hdb_scope

    handle = hdb_scope.Handle("attach", str(where), "pinned", hdb_scope._paths())
    monkeypatch.setattr(type(handle), "ssh", property(lambda self: ssh))
    monkeypatch.setattr(compress_cli, "resolve", lambda attach, hdb: handle)


def test_the_report_reads_an_attachments_hdb_on_this_machine(hdb, monkeypatch):
    """#2237: `data compress` took --hdb but not --attachment, as check does."""
    _attached(monkeypatch, hdb, None)
    r = run_uqs("data", "compress", "--report", "--json", "--attachment", "pb-uat")
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)["hdb"] == str(hdb)


def test_the_report_refuses_an_attachments_hdb_on_another_host(hdb, monkeypatch):
    _attached(monkeypatch, hdb, "ops@pb-uat")
    died: list[Exception] = []

    def record(exc: Exception) -> None:
        died.append(exc)
        raise SystemExit(1)

    monkeypatch.setattr(compress_cli, "_die", record)
    run_uqs("data", "compress", "--report", "--attachment", "pb-uat")
    assert len(died) == 1
    assert "--report reads the HDB's files on this machine" in str(died[0])
    assert "ops@pb-uat" in str(died[0])
