"""`uqs data check` brings market_data's old partitions to the order a markout's as-of
join needs, on a disposable HDB (#2288).

A markout joins against the memory-mapped market_data, which needs the flat level-0 top
(bid, ask), each row's position (rid), and the partition sorted by sym, source,
source_time with p# on sym. History written before that has none of the three. The one
command that already repairs partitions reports what is missing - dry, changing nothing -
and, after it asks, derives the flat top from the stored ladders, rewrites each partition
in order in staging and swaps it in, and numbers the rows. Everything is judged from outside:
the HDB by what q loads, the report by the command's own JSON.
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
from uqs.paths import UqsPaths
from uqs.stack import hdb_fill

ROOT = Path(__file__).resolve().parents[3]

#: The starter pack's own table, declared beside the plant's; nothing here uses it.
VENDORED = "quote:([] sym:`symbol$(); bid:`float$())\n"

#: market_data as it stood before the flat top: every plant column but bid, ask and rid, in
#: arrival order. One book has a withdrawn bid, one a zero-size ask level (no price).
#: argv[0] is the HDB root.
BUILD = r"""
\l src/etl/plant_registry.q
\l src/etl/plant_tables.q
root:hsym `$first .z.x;
d:delete bid, ask, rid from 0#.qetl.plant.market_data;
rows:{[d;day;n]
    t0:`timestamp$day;
    t:flip (cols d)!{[n;v] n#first v}[n;] each d cols d;
    t:update time:t0+1000000000*til n, sym:n#`EURUSD`GBPUSD`USDJPY, source:n#`b`a`c`a`b,
        market:n#`fx, source_time:t0+1000000000*n-til n, clock:n#`venue from t;
    px:1+0.01*til n;
    t:update bid_prices:enlist each px, bid_sizes:n#enlist enlist 1e6 from t;
    t:update ask_prices:enlist each px+0.0002, ask_sizes:n#enlist enlist 1e6 from t;
    t:update bid_prices:{0#0f} each i from t where 0=i mod 7;
    update ask_sizes:{enlist 0f} each i from t where 0=i mod 11}[d];
put:{[root;d;data] (` sv .Q.par[root;d;`market_data],`) set .Q.en[root;data]};
put[root;2026.01.01;rows[2026.01.01;40]];
put[root;2026.01.02;rows[2026.01.02;25]];
exit 0
"""

#: What q loads from the migrated HDB, per partition, enumerations resolved.
READ = r"""
system"l ",first .z.x;
{[d]
    t:select from market_data where date=d;
    o:flip `sym`source`source_time!(t`sym;t`source;t`source_time);
    -1 "PART|",string[d],"|",string[count t],"|",string[attr t`sym],"|",
        string[o~`sym`source`source_time xasc o],"|",string[(t`rid)~til count t];
    ok:{[px;sz] (0<first px)&(0w>first px)&(0<first sz)&0w>first sz};
    top:{[ok;px;sz] $[(count px)&count sz; $[ok[px;sz]; first px; 0n]; 0n]}[ok];
    bids:top'[t`bid_prices;t`bid_sizes];
    asks:top'[t`ask_prices;t`ask_sizes];
    -1 "TOP|",string[d],"|",string[(t`bid)~bids],"|",string[(t`ask)~asks],"|",
        string[any null t`bid],"|",string[any null t`ask]} each 2026.01.01 2026.01.02;
exit 0
"""


def _q(q_binary, script: str, *args: str, tmp: Path) -> str:
    q, env = q_binary
    file = tmp / f"{abs(hash(script))}.q"
    file.write_text(script)
    result = subprocess.run(
        [q, str(file), *args, "-q"],
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.fixture
def runtime(tmp_path: Path, q_binary, kdbx, monkeypatch) -> UqsPaths:
    """A runtime whose HDB holds the old-shape partitions."""
    data = tmp_path / "data"
    hdb = data / "hdb"
    hdb.mkdir(parents=True)
    _q(q_binary, BUILD, str(hdb), tmp=tmp_path)
    app = tmp_path / "app"
    app.mkdir()
    (app / "database.q").write_text(VENDORED)
    paths = UqsPaths(
        repo_root=ROOT,
        torqhome=tmp_path / "torq",
        torqapphome=app,
        torqdata=data,
        scripts_dir=ROOT / "scripts",
        orchestrator_dir=tmp_path / "orchestrator",
        runtime="uqf",
    )
    monkeypatch.setattr(hdb_check, "_paths", lambda: paths)
    q, env = q_binary
    monkeypatch.setenv("QCMD", q)
    monkeypatch.setenv("QHOME", env["QHOME"])
    monkeypatch.setenv("UQF_STATUS_DIR", str(tmp_path / "status"))
    return paths


def _uqs(monkeypatch, *argv: str) -> tuple[int, dict]:
    monkeypatch.setattr(sys, "argv", ["uqs", *argv])
    result = CliRunner().invoke(cli.app, list(argv), env={"COLUMNS": "300"})
    out = result.stdout
    assert "{" in out, (result.exit_code, out, repr(result.exception))
    return result.exit_code, json.loads(out[out.index("{") :])


def _files(paths: UqsPaths) -> list[tuple[Path, bytes]]:
    return sorted(
        (p.relative_to(paths.hdb_dir), p.read_bytes())
        for p in paths.hdb_dir.rglob("*")
        if p.is_file()
    )


def test_check_reports_what_is_missing_and_out_of_order_and_changes_nothing(runtime, monkeypatch):
    before = _files(runtime)
    code, doc = _uqs(monkeypatch, "data", "check", "--json")
    assert code == 1 and doc["ok"] is False, doc
    for day in ("2026.01.01", "2026.01.02"):
        assert sorted(doc["missing_columns"][day]["market_data"]) == ["ask", "bid", "rid"], doc
    parts = {(u["partition"], u["table"]): u["why"] for u in doc["unsorted"]}
    assert set(parts) == {("2026.01.01", "market_data"), ("2026.01.02", "market_data")}, doc
    assert all(w.startswith("not sorted by sym, source, source_time") for w in parts.values())
    assert _files(runtime) == before, "a check writes nothing"


def test_the_bootstrap_fill_does_not_derive_them(runtime, monkeypatch):
    """The fill every uqs command runs adds no bid, ask or rid: a null there would read as a
    withdrawn book, and deriving them reads every ladder."""
    assert hdb_fill.fill_hdb_partitions(runtime) in (True, False)
    code, doc = _uqs(monkeypatch, "data", "check", "--json")
    assert code == 1
    assert sorted(doc["missing_columns"]["2026.01.01"]["market_data"]) == ["ask", "bid", "rid"]
    assert doc["unsorted"], "and rewrites no partition"


def test_apply_derives_the_flat_top_sorts_and_numbers_the_rows(
    runtime, monkeypatch, q_binary, tmp_path
):
    code, doc = _uqs(monkeypatch, "data", "check", "--apply", "--yes", "--json")
    assert code == 0 and doc["ok"] is True, doc
    assert doc["missing_columns"] == {} and doc["unsorted"] == []

    out = _q(q_binary, READ, str(runtime.hdb_dir), tmp=tmp_path)
    parts = [line.split("|") for line in out.splitlines() if line.startswith("PART|")]
    days = [(p[1], p[2]) for p in parts]
    assert days == [("2026.01.01", "40"), ("2026.01.02", "25")], "no row lost"
    # (date, rows, attribute on sym, sorted, rid is the position)
    assert all(p[3:] == ["p", "1", "1"] for p in parts), parts
    tops = [line.split("|") for line in out.splitlines() if line.startswith("TOP|")]
    # bid and ask are the first level where it is a price; the fixture withdraws some of each
    assert all(t[2:] == ["1", "1", "1", "1"] for t in tops), tops


def test_a_second_apply_changes_nothing(runtime, monkeypatch):
    assert _uqs(monkeypatch, "data", "check", "--apply", "--yes", "--json")[0] == 0
    before = _files(runtime)
    code, doc = _uqs(monkeypatch, "data", "check", "--apply", "--yes", "--json")
    assert code == 0 and doc["ok"] is True
    assert _files(runtime) == before
