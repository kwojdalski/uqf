"""`uqs data seed` (#766): copying one runtime's HDB partitions into another's.

The q-backed tests build two real HDBs whose sym files enumerate in
different orders - the case a file copy gets silently wrong - and run the
seed script on whatever q is configured (QCMD), skipping when none is.
"""

from __future__ import annotations

import hashlib
import subprocess
from datetime import date
from pathlib import Path

import pytest

from uqs.cli.seed import parse_dates
from uqs.interpreter import q_interpreter
from uqs.paths import UqsError, UqsPaths
from uqs.stack import hdb_seed

ROOT = Path(__file__).resolve().parents[3]

SCHEMA = """trade:([]time:`timestamp$();sym:`g#`symbol$();price:`float$();size:`long$())
quote:([]time:`timestamp$();sym:`g#`symbol$();bid:`float$();ask:`float$())
"""

#: The source enumerates EURUSD GBPUSD USDJPY; the target already holds
#: USDJPY AUDUSD, so the source's indices 0 1 2 mean different symbols there.
MAKE = r"""
/ Roots from strings: a temporary path holds `-`, which ends a `:path literal.
src:hsym `$"SRC"; dst:hsym `$"DST";
t:([]time:2026.01.01D10:00 2026.01.01D11:00 2026.01.01D12:00;
    sym:`EURUSD`GBPUSD`USDJPY;price:1.1 1.3 150f;size:1 2 3)
(` sv src,`sym) set `EURUSD`GBPUSD`USDJPY
sym:get ` sv src,`sym
w:{[dir;t] {[dir;c;v] (` sv dir,c) set v}[dir]'[cols t;value flip t]; (` sv dir,`.d) set cols t}
w[.Q.par[src;2026.01.01;`trade];update `sym$sym from t]
w[.Q.par[src;2026.01.02;`trade];update `sym$sym from update time+1D from t]
(` sv dst,`sym) set `USDJPY`AUDUSD
sym:get ` sv dst,`sym
w[.Q.par[dst;2026.01.03;`trade];
    ([]time:enlist 2026.01.03D00:00;sym:`sym$enlist`AUDUSD;price:enlist 0.6;size:enlist 9)]
exit 0
"""

#: Each partition's trade rows as `date,time,sym,price,size` lines.
READ = r"""
root:hsym `$"DST"
sym:get ` sv root,`sym
parts:asc "D"$string k where (k:key root) like "[0-9]*"
{[root;p]
    dir:.Q.par[root;p;`trade];
    if[()~key ` sv dir,`.d; :()];
    c:get ` sv dir,`.d;
    v:{[dir;c] x:get ` sv dir,c; $[20h=type x; value x; x]}[dir] each c;
    t:flip c!v;
    {[p;r] -1 string[p],",",string[r`time],",",string[r`sym],",",
        string[r`price],",",string r`size}[p] each t;
    }[root] each parts;
exit 0
"""


def _q() -> Path:
    q = q_interpreter()
    if q is None:
        pytest.skip("no q interpreter (QCMD) to build and read an HDB with")
    return q


def _run(q: Path, script: str, tmp: Path) -> str:
    f = tmp / "script.q"
    f.write_text(script)
    out = subprocess.run([str(q), str(f)], capture_output=True, text=True, timeout=60, check=False)
    assert out.returncode == 0, out.stdout + out.stderr
    return out.stdout


def _paths(tmp: Path, runtime: str) -> UqsPaths:
    return UqsPaths(
        repo_root=tmp,
        torqhome=tmp / "lib" / "torq",
        torqapphome=tmp / "lib" / "torq-finance-starter-pack",
        torqdata=tmp / "output" / f"uqs-{runtime}",
        scripts_dir=ROOT / "scripts",
        orchestrator_dir=tmp / "python" / "uqs",
        runtime=runtime,
    )


@pytest.fixture
def hdbs(tmp_path: Path) -> tuple[UqsPaths, UqsPaths, Path]:
    q = _q()
    src, dst = _paths(tmp_path, "uqf"), _paths(tmp_path, "torq")
    for p in (src, dst):
        (p.torqdata / "hdb").mkdir(parents=True)
    dst.generated_schema.write_text(SCHEMA)
    _run(
        q,
        MAKE.replace("SRC", str(src.torqdata / "hdb")).replace("DST", str(dst.torqdata / "hdb")),
        tmp_path,
    )
    return src, dst, q


def _rows(dst: UqsPaths, q: Path, tmp: Path) -> list[str]:
    out = _run(q, READ.replace("DST", str(dst.torqdata / "hdb")), tmp)
    return [line for line in out.splitlines() if line[:4].isdigit()]


def _tree(root: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(str(f.relative_to(root)).encode() + f.read_bytes())
    return h.hexdigest()


def test_symbols_decode_in_the_target_as_they_did_in_the_source(hdbs, tmp_path):
    src, dst, q = hdbs
    hdb_seed.seed(dst, src)
    rows = _rows(dst, q, tmp_path)
    assert [r.split(",")[2] for r in rows if r.startswith("2026.01.01")] == [
        "EURUSD",
        "GBPUSD",
        "USDJPY",
    ], "re-enumerated against the target's sym file, not copied as indices"
    assert sum(r.startswith("2026.01.02") for r in rows) == 3
    assert [r for r in rows if r.startswith("2026.01.03")][0].split(",")[2] == "AUDUSD", (
        "the target's own partition still decodes - its sym file was extended, not replaced"
    )


def test_the_source_is_only_read(hdbs):
    src, dst, _ = hdbs
    before = _tree(src.torqdata / "hdb")
    hdb_seed.seed(dst, src)
    assert _tree(src.torqdata / "hdb") == before


def test_a_partition_already_there_is_skipped_unless_overwritten(hdbs):
    src, dst, _ = hdbs
    hdb_seed.seed(dst, src)
    again = hdb_seed.seed(dst, src)
    assert "seeded 0 table partition(s)" in again and "skipped 2" in again
    assert "seeded 2 table partition(s)" in hdb_seed.seed(dst, src, overwrite=True)


def test_only_the_dates_asked_for_are_copied(hdbs, tmp_path):
    src, dst, q = hdbs
    hdb_seed.seed(dst, src, first=date(2026, 1, 2), last=date(2026, 1, 2))
    dates = {r.split(",")[0] for r in _rows(dst, q, tmp_path)}
    assert dates == {"2026.01.02", "2026.01.03"}


def test_a_column_whose_type_differs_is_refused_with_nothing_written(hdbs):
    src, dst, _ = hdbs
    dst.generated_schema.write_text(SCHEMA.replace("price:`float$()", "price:`long$()"))
    with pytest.raises(UqsError, match="column price is f in the source but j in the target"):
        hdb_seed.seed(dst, src)
    assert not (dst.torqdata / "hdb" / "2026.01.01").exists()


def test_a_table_the_target_does_not_declare_is_refused(hdbs):
    src, dst, _ = hdbs
    with pytest.raises(UqsError, match="the target does not declare packets"):
        hdb_seed.seed(dst, src, ["trade", "packets"])


def test_seeding_a_runtime_from_itself_is_refused(tmp_path):
    p = _paths(tmp_path, "torq")
    with pytest.raises(UqsError, match="is the runtime being seeded"):
        hdb_seed.seed(p, p)


def test_a_source_with_no_hdb_is_refused_naming_it(tmp_path):
    with pytest.raises(UqsError, match="the uqf runtime has no HDB"):
        hdb_seed.seed(_paths(tmp_path, "torq"), _paths(tmp_path, "uqf"))


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("2026-09-01..2026-09-30", (date(2026, 9, 1), date(2026, 9, 30))),
        ("2026-09-01..", (date(2026, 9, 1), None)),
        ("..2026-09-30", (None, date(2026, 9, 30))),
        (None, (None, None)),
    ],
)
def test_dates_take_either_side_open(text, want):
    assert parse_dates(text) == want


def test_a_malformed_date_range_is_refused():
    with pytest.raises(UqsError, match="is not FIRST..LAST"):
        parse_dates("2026-09-01-2026-09-30")
