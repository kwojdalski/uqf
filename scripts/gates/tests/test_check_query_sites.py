"""Tests for the query-sites rule, `scripts/gates/policy/query_sites.py` (#3001)."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

_SPEC = importlib.util.spec_from_file_location(
    "query_sites", REPO / "scripts" / "gates" / "policy" / "query_sites.py"
)
assert _SPEC and _SPEC.loader
gate = importlib.util.module_from_spec(_SPEC)
sys.modules["query_sites"] = gate
_SPEC.loader.exec_module(gate)

FILE = "src/etl/core/thing.q"


def _problems(tmp_path: Path, body: str, unwired: dict | None = None) -> list[str]:
    """The gate over a one-file tree, with the real tree's UNWIRED list out of the way."""
    f = tmp_path / FILE
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("\\d .qetl.thing\n" + body)
    return gate.problems(tmp_path, unwired=unwired or {})


def test_the_real_tree_is_clean():
    assert gate.problems(REPO) == []


def test_the_command_runs_and_passes():
    r = subprocess.run(
        [sys.executable, "scripts/gates/check.py", "query-sites"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    assert "query site" in r.stdout


def test_a_functional_select_off_the_road_is_refused_with_its_line(tmp_path):
    found = _problems(tmp_path, "read:{[t] ?[t;();0b;`x`y!`x`y]}\n")
    assert len(found) == 1 and found[0].startswith(f"{FILE}:2:")


def test_the_vector_conditional_is_not_a_query(tmp_path):
    assert _problems(tmp_path, "pick:{[c;a;b] ?[c;a;b]}\nnest:{[c;a;b] ?[c;?[c;a;b];b]}\n") == []


def test_a_delete_is_a_query_site_too(tmp_path):
    assert len(_problems(tmp_path, "drop:{[t] ![t;();0b;enlist `x]}\n")) == 1


def test_a_definition_that_calls_the_road_is_wired(tmp_path):
    body = "read:{[t] .qetl.query.local[`storage;{[t] ?[t;();0b;`x`y!`x`y]};enlist t]}\n"
    assert _problems(tmp_path, body) == []


def test_the_road_in_a_neighbouring_definition_does_not_count(tmp_path):
    body = "a:{[t] .qetl.query.local[`storage;{[t] t};enlist t]}\nb:{[t] ?[t;();0b;`x`y!`x`y]}\n"
    assert len(_problems(tmp_path, body)) == 1


def test_a_line_that_says_why_it_is_not_a_query_is_allowed(tmp_path):
    assert (
        _problems(tmp_path, "drop:{[t] ![t;();0b;enlist `x]}  / not a query: drops a column\n")
        == []
    )


def test_a_gateway_call_off_the_road_is_refused(tmp_path):
    found = _problems(
        tmp_path, 'ask:{[h] h(`.gw.syncexec;"1";`rdb)}\nfetch:{[h] h(`.dataaccess.getdata;()!())}\n'
    )
    assert len(found) == 2


def test_a_comment_or_a_string_is_not_code(tmp_path):
    body = '/ ?[t;();0b;()] and .gw.syncexec in a comment\nmsg:"?[t;();0b;()] .gw.x"\n'
    assert _problems(tmp_path, body) == []


def test_an_unwired_file_is_skipped_and_a_missing_one_is_reported(tmp_path):
    found = _problems(
        tmp_path,
        "read:{[t] ?[t;();0b;`x`y!`x`y]}\n",
        {FILE: "later", "src/etl/core/gone.q": "later"},
    )
    assert len(found) == 1 and "gone.q" in found[0]
