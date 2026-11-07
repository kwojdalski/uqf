"""The markout-mapped rule, `scripts/gates/policy/markout_mapped.py`: a markout's path
selects market_data by its date alone and reads no depth (#2288)."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

_SPEC = importlib.util.spec_from_file_location(
    "markout_mapped", REPO / "scripts" / "gates" / "policy" / "markout_mapped.py"
)
assert _SPEC and _SPEC.loader
mm = importlib.util.module_from_spec(_SPEC)
sys.modules["markout_mapped"] = mm
_SPEC.loader.exec_module(mm)

FILE = "src/etl/sources/hdb_x_markouts.q"


def _problems(tmp_path: Path, text: str) -> list[str]:
    f = tmp_path / FILE
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    return mm.problems(tmp_path, (FILE,))


def test_the_real_tree_is_clean():
    assert mm.problems(REPO) == []


def test_the_command_runs_and_passes():
    r = subprocess.run(
        [sys.executable, "scripts/gates/check.py", "markout-mapped"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    assert "select market_data by date alone" in r.stdout


def test_the_path_names_the_core_the_sources_the_workers_and_the_live_jobs():
    listed = set(mm.PATH)
    assert "src/etl/core/mapped_books.q" in listed
    assert "src/etl/transforms/markout_core.q" in listed
    assert {f"src/etl/sources/hdb_{d}_markouts.q" for d in ("fx", "crypto", "eq")} <= listed
    assert "src/etl/streaming/fx_markout_wide.q" in listed
    # the depth reader is deliberately NOT on the path
    assert "src/etl/core/mapped_depth.q" not in listed


def test_a_date_only_select_passes(tmp_path):
    q = (
        "a:select sym, source, source_time, bid, ask from market_data where date=d;\n"
        "b:select sym from `market_data where date within (lo;hi);\n"
        "c:exec max date from select distinct date from market_data;\n"
        "d:select sym from market_data where date=max date;\n"
    )
    assert _problems(tmp_path, q) == []


def test_the_old_book_query_fails_on_its_where_clause_and_its_ladders(tmp_path):
    # the shape every markout source used before: a sym filter and a time range, depth included
    q = (
        "cand:`source_time xasc select source_time, sym, source, bid_prices, bid_sizes,\n"
        "        ask_prices, ask_sizes, clock\n"
        "    from `market_data where date within (neg[skew],skew)+`date$(read_from;from_ts),\n"
        "        source_time>=read_from, sym in syms;\n"
    )
    out = _problems(tmp_path, q)
    assert any("where source_time>=read_from" in m for m in out), out
    assert sum("bid_prices" in m or "ask_sizes" in m for m in out) >= 1, out


def test_each_non_date_constraint_fails_even_after_a_date_one(tmp_path):
    for clause in ("sym in syms", "source in ven", "market=`crypto", "source_time>=t"):
        out = _problems(tmp_path, f"x:select sym from market_data where date=d, {clause};\n")
        assert len(out) == 1 and clause in out[0], (clause, out)


def test_a_where_that_continues_on_the_next_line_is_read(tmp_path):
    q = "x:select sym\n    from market_data\n    where date=d,\n        sym=`EURUSD;\n"
    out = _problems(tmp_path, q)
    assert len(out) == 1 and "sym=`EURUSD" in out[0], out


def test_a_functional_select_needs_a_lone_date_constraint(tmp_path):
    ok = "x:?[`market_data;enlist (=;`date;d);0b;()!()];\n"
    assert _problems(tmp_path, ok) == []
    bad = "x:?[`market_data;((=;`date;d);(in;`sym;syms));0b;()!()];\n"
    assert len(_problems(tmp_path, bad)) == 1


def test_a_comment_is_not_code(tmp_path):
    q = (
        "/ the old query read bid_prices and select ... from market_data where sym in syms\n"
        "x:1; / top_sides is what read them\n"
    )
    assert _problems(tmp_path, q) == []


def test_the_depth_reader_and_top_sides_are_refused_on_the_path(tmp_path):
    out = _problems(tmp_path, "x:.qetl.mapped.depth_at arg;\ny:.qf.mkt.top_sides md;\n")
    assert len(out) == 2, out


def test_a_listed_file_that_is_gone_is_named(tmp_path):
    out = mm.problems(tmp_path, (FILE,))
    assert len(out) == 1 and "listed in PATH but not there" in out[0], out
