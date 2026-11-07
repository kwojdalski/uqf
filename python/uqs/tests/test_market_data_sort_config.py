"""How market_data is laid out at end of day, from the one declaration (#2288).

src/etl/plant_tables.q declares market_data sorted by sym, source, source_time with p# on sym
(`sorting`) and its row-number column (`rid`, `row_ids`). Three things act on that, and this
holds them to it:

* scripts/torqconfig/sort.csv, which TorQ's wdb (and sort process and sort workers) sort by at
  end of day - the file KDBSERVCONFIG puts ahead of lib/torq's, which is never edited. Its rows
  for a table are exactly the plant's declaration, its `default` rows exactly TorQ's;
* scripts/processes/uqs_row_ids.q, loaded after wdb.q, which numbers the rows once TorQ's own
  `.sort.sorttab` has sorted them - run here through that very code;
* `uqs data compress --report`, which reads that sort.csv and so names a partition not in the order.
"""

from __future__ import annotations

import csv
import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

import uqs.cli.entry  # noqa: F401 - registers every command on the app
from uqs.cli.shared import app
from uqs.model import plant_metadata
from uqs.paths import repo_root
from uqs.stack import procs

ROOT = repo_root()
SORT_CSV = ROOT / "scripts" / "torqconfig" / "sort.csv"
TORQ_SORT_CSV = ROOT / "lib" / "torq" / "config" / "sort.csv"
DBWRITEUTILS = ROOT / "lib" / "torq" / "code" / "common" / "dbwriteutils.q"
ROW_IDS = ROOT / "scripts" / "processes" / "uqs_row_ids.q"
PLANT = ROOT / "src" / "etl" / "plant_tables.q"


def _rows(path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(path.read_text().splitlines()))


def test_the_default_rows_are_torqs_own():
    ours = [r for r in _rows(SORT_CSV) if r["tabname"] == "default"]
    assert ours == [r for r in _rows(TORQ_SORT_CSV) if r["tabname"] == "default"]


def test_every_declared_sort_rule_is_in_the_file_in_order_and_nothing_else_is():
    own = plant_metadata.load().own()
    declared = {name: t.sorting for name, t in own.items() if t.sorting}
    assert "market_data" in declared, "market_data declares its sort rule"
    assert [list(t) for t in declared["market_data"]] == [
        ["sym", "p"],
        ["source", ""],
        ["source_time", ""],
    ]
    rows = [r for r in _rows(SORT_CSV) if r["tabname"] != "default"]
    want = [
        {"tabname": name, "att": att, "column": col, "sort": "1"}
        for name, rule in declared.items()
        for col, att in rule
    ]
    assert rows == want, "sort.csv holds the plant's rules for a table, and no rule the plant lacks"


def test_torqs_own_loader_accepts_the_file(tmp_path, q_binary):
    q, env = q_binary
    script = tmp_path / "load.q"
    script.write_text(
        '.lg.o:{[x;y]};.lg.e:{[x;y] -2 string[x],": ",y; exit 3};'
        ".proc.getconfigfile:{enlist `:none.csv};\n"
        'system "l ",getenv`DBW;\n'
        ".sort.getsortcsv hsym `$getenv`CSV;\n"
        '-1 "ROWS|",string count select from .sort.params where tabname=`market_data;\n'
        "exit 0\n"
    )
    r = subprocess.run(
        [q, str(script), "-q"],
        env={**env, "DBW": str(DBWRITEUTILS), "CSV": str(SORT_CSV)},
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
        check=False,
    )
    assert "ROWS|3" in r.stdout, r.stdout + r.stderr


def test_the_row_number_overlay_names_the_plants_row_id_columns():
    text = ROW_IDS.read_text()
    q_tables = text.split(".qtorq.row_ids.tables:", 1)[1].splitlines()[0]
    assert q_tables == "enlist[`market_data]!enlist `rid"
    plant = PLANT.read_text().split(".qetl.plant.row_ids:", 1)[1].splitlines()[0]
    assert plant == q_tables, "the overlay and the plant name the same table and column"


def test_every_process_that_runs_the_sort_loads_the_overlay_after_wdb():
    for proc in ("wdb1", "sort1", "sortworker1", "sortworker2"):
        before, after = procs.VENDORED_LOAD_OVERLAY[proc]
        assert after == (procs.ROW_IDS_LOAD,) and before == (), proc


ROWID_Q = r"""
.lg.o:{[x;y]};.lg.e:{[x;y] -2 string[x],": ",y; exit 3};
.proc.getconfigfile:{enlist `:none.csv};
system "l ",getenv`DBW;
.sort.getsortcsv hsym `$getenv`CSV;
system "l ",getenv`OVERLAY;
db:hsym `$getenv`DB;
dir:` sv db,`2026.01.01`market_data`;
/ arrival order, no attribute, the row number as the writer left it: null
n:12;
t0:2026.01.01D10:00:00;
t:([] time:t0+1000000000*til n; sym:n#`b`a`c; source:n#`y`x;
    source_time:t0+1000000000*n-til n; rid:n#0N);
dir set .Q.en[db;t];
.sort.sorttab (`market_data;enlist dir);
r:get dir;
s:select sym, source, source_time from r;
-1 "SORTED|",string s~`sym`source`source_time xasc s;
-1 "ATTR|",string attr r`sym;
-1 "RID|",string (r`rid)~til n;
/ a table with no row-number column is sorted as before and nothing else is written
other:hsym `$getenv[`DB],"/2026.01.01/quote/";
other set .Q.en[db;([] time:2026.01.01D10:00:00+1000000000*3 1 2; sym:`b`a`c)];
.sort.sorttab (`quote;enlist other);
o:get other;
-1 "OTHER|",string (select sym, time from o)~`sym`time xasc select sym, time from o;
exit 0
"""


def test_torqs_end_of_day_sort_is_followed_by_the_row_numbers(tmp_path, q_binary, kdbx):
    q, env = q_binary
    script = tmp_path / "rowids.q"
    script.write_text(ROWID_Q)
    db = tmp_path / "db"
    db.mkdir()
    r = subprocess.run(
        [q, str(script), "-q"],
        env={
            **env,
            "DBW": str(DBWRITEUTILS),
            "CSV": str(SORT_CSV),
            "OVERLAY": str(ROW_IDS),
            "DB": str(db),
        },
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
        check=False,
    )
    out = r.stdout
    assert "SORTED|1" in out, out + r.stderr
    assert "ATTR|p" in out, out
    assert "RID|1" in out, "each row numbered by its position after the sort"
    assert "OTHER|1" in out, out


@pytest.fixture
def hdb(q_binary, kdbx, tmp_path) -> Path:
    """Two market_data days: one in the declared order with p#, one as the old sort left it."""
    q, env = q_binary
    root = tmp_path / "hdb"
    script = tmp_path / "build.q"
    script.write_text(
        r"""
r:hsym `$getenv`HDB;
n:2000;
day:{[n;declared]
    t:([] sym:n?`aa`bb`cc; source:n?`x`y`z; time:n?0D23:59; px:n#1.5);
    t:$[declared; `sym`source`time xasc t; `sym`time xasc t];
    $[declared; update `p#sym from t; t]}[n];
put:{[r;d;t] (` sv r,(`$string d),`market_data`) set .Q.en[r] t};
put[r;2026.01.01;day 1b];
put[r;2026.01.02;day 0b];
exit 0
"""
    )
    subprocess.run(
        [q, str(script), "-q"],
        env={**env, "HDB": str(root)},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=True,
        timeout=60,
    )
    return root


def test_the_compression_report_flags_a_market_data_partition_not_in_the_declared_order(
    hdb, q_binary, monkeypatch
):
    q, env = q_binary
    monkeypatch.setenv("QCMD", q)
    for key in ("QHOME", "QLIC"):
        if env.get(key):
            monkeypatch.setenv(key, env[key])
    # the sort check names the file it judged by: the one the wdb reads
    r = CliRunner().invoke(
        app,
        ["data", "compress", "--report", "--json", "--hdb", str(hdb), "--sort-csv", str(SORT_CSV)],
        env={"COLUMNS": "300"},
    )
    assert r.exit_code == 0, r.output
    flagged = {u["partition"]: u for u in json.loads(r.stdout)["unsorted"]}
    # source_time is not a column of the fixture: the declared order is judged on the columns it has
    assert "2026-01-02" in flagged, flagged
    assert flagged["2026-01-02"]["sorted"] is False
    assert "2026-01-01" not in flagged, "the partition in the declared order, with p#, passes"
