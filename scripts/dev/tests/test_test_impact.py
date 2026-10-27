"""scripts/dev/test_impact.py: which q suites a change reaches (#1690).

The reading of q text is tested on its own. What needs the graph - a tree
built and walked - runs only where a qlinter with `qlinter analyze graph` is
installed ($UQF_QLINTER or PATH), and is skipped elsewhere, saying so.
"""

from __future__ import annotations

import importlib.util
import stat
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]

_SPEC = importlib.util.spec_from_file_location(
    "test_impact", REPO / "scripts" / "dev" / "test_impact.py"
)
assert _SPEC and _SPEC.loader
ti = importlib.util.module_from_spec(_SPEC)
sys.modules["test_impact"] = ti
_SPEC.loader.exec_module(ti)

GRAPH = pytest.mark.skipif(
    not ti.qlint_graph.available(),
    reason="no qlinter with `analyze graph` ($UQF_QLINTER or PATH): scripts/gates/qlint_graph.py",
)


# --- reading q text ----------------------------------------------------------


def test_scan_drops_comments_and_collects_strings():
    code, strings = ti.scan('.a.f:{x} / trailing\n/ a whole line\nb:"x / not a comment";c:1\n')
    assert code == [".a.f:{x}", "", 'b:"";c:1']
    assert strings == ["x / not a comment"]


def test_scan_skips_a_block_comment_and_stops_at_a_lone_backslash():
    code, _ = ti.scan('a:1\n/\n.b.f:"in block"\n\\\nc:2\n\\\nd:"after exit"\n')
    assert [c for c in code if c] == ["a:1", "c:2"]


def test_path_literals_reads_strings_symbols_and_command_strings():
    text = (
        "f:read0 `:tests/run_tests.q\n"
        'system "l src/etl/core/log.q"\n'
        'g:"src/etl/sources/",string x\n'
        'h:"run tests/q/try_acquire.q, then look"\n'
        'k:"src"\n'
        'm:"no path here"\n'
    )
    got = ti.path_literals(text, {"src", "tests", "docs"})
    assert got == {
        "tests/run_tests.q",
        "src/etl/core/log.q",
        "src/etl/sources/",
        "tests/q/try_acquire.q",
        "src",
    }


def test_covers_a_file_a_directory_and_a_computed_prefix():
    known = {"src/a.q", "src", "tests/q/test_a.q", "tests/q"}
    assert ti.covers("src", "src/a.q", known)
    assert ti.covers("src/a.q", "src/a.q", known)
    assert ti.covers("tests/q/test_", "tests/q/test_a.q", known)
    assert not ti.covers("src/a", "src/ab.q", known | {"src/a"})
    assert not ti.covers("tests/q/test_a.q", "tests/q/test_a.qx", known)


def test_chunks_cut_a_harness_file_at_each_top_level_definition():
    text = (
        "/ header\n"
        ".qt.support.a:{[x]\n"
        "    x+1}\n"
        "\n"
        "// the doc of b\n"
        ".qt.support.b:{[] .qt.support.a 1}\n"
        'system "l tests/lib/discovery.q";\n'
    )
    got = ti.chunks(text)
    assert [name for name, _ in got] == [".qt.support.a", ".qt.support.b", "line2"]
    assert "x+1" in got[0][1]
    assert got[1][1].startswith("// the doc of b\n")


def test_literal_loads():
    assert ti.literal_loads('\\l src/init.q\n  system "l ./tests/lib/x.q";\n') == [
        "src/init.q",
        "tests/lib/x.q",
    ]


def test_the_harness_and_the_loaders_run_everything():
    for path in (
        "tests/lib/support.q",
        "tests/run_tests.q",
        "src/init.q",
        "src/etl/init.q",
        "src/etl/generated/load_plan.q",
        "scripts/processes/torq_pipeline.q",
        "lib/torq/code/x.q",
    ):
        assert ti.full_suite_reason(path), path
    assert ti.full_suite_reason("src/etl/core/log.q") is None


def test_render():
    sub = ti.Selection(["tests/q/test_a.q", "tests/q/test_b.q"], 3, [])
    assert ti.render(sub, "text") == "tests/q/test_a.q\ntests/q/test_b.q"
    assert ti.render(sub, "args") == "-files test_a.q,test_b.q"
    every = ti.Selection(["tests/q/test_a.q"], 1, ["tests/run_tests.q: the runner"])
    assert ti.render(every, "text") == "ALL\nreason: tests/run_tests.q: the runner"
    assert ti.render(every, "args") == ""
    assert ti.render(ti.Selection([], 3, []), "args") == ""


# --- without a graph: refuse, never guess ------------------------------------


def _fake_qlinter(tmp_path: Path) -> str:
    """A qlinter like 0.14.14: `analyze` is read as a path that does not exist."""
    exe = tmp_path / "qlinter"
    exe.write_text('#!/bin/sh\necho "qlinter: Path does not exist: $1" >&2\nexit 2\n')
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return str(exe)


def test_a_qlinter_without_the_graph_is_refused_naming_what_is_needed(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("UQF_QLINTER", _fake_qlinter(tmp_path))
    assert not ti.qlint_graph.available()
    with pytest.raises(ti.qlint_graph.GraphUnavailable, match="analyze graph"):
        ti.build(REPO)
    assert ti.main(["src/pricing/forwards.q"]) == 2
    err = capsys.readouterr().err
    assert "has no `qlinter analyze graph`" in err and "0.14.15" in err


def test_a_missing_binary_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("UQF_QLINTER", str(tmp_path / "nowhere"))
    with pytest.raises(ti.qlint_graph.GraphUnavailable, match="not a file"):
        ti.qlint_graph.binary()


# --- a tree, built and walked ------------------------------------------------


def _tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return root


_SMALL = {
    "tests/run_tests.q": "\\l tests/lib/support.q\n\\l src/init.q\n",
    "tests/lib/support.q": ".qt.support.h:{[] .qf.a.f 1}\n.qt.support.k:{[] read0 `:data/h.txt}\n",
    "data/h.txt": "x\n",
    "src/init.q": "\\l src/a.q\n\\l src/b.q\n",
    "src/a.q": ".qf.a.f:{x+1}\n",
    "src/b.q": ".qf.b.g:{.qf.a.f x}\n",
    "src/c.q": ".qf.c.h:{x}\n",
    "src/etl/core/reg.q": (
        ".qetl.r.registry:(`symbol$())!()\n"
        ".qetl.r.define:{[name;fn] .qetl.r.registry[name]:fn}\n"
        ".qetl.r.run:{[name;x] .qetl.r.registry[name] x}\n"
    ),
    "src/etl/transforms/d1.q": ".qpipe.d1.f:{x*2}\n.qetl.r.define[`d1;.qpipe.d1.f]\n",
    "src/etl/transforms/d2.q": ".qetl.r.define[`d2;{x}]\n",
    "tests/q/test_a.q": ".qt.qf.a.test_f:{[t] .qf.a.f 1}\n.qt.qf.a.helper:{[] 1}\n",
    "tests/q/test_b.q": ".qt.qf.b.test_g:{[t] .qf.b.g 1}\n",
    "tests/q/test_c.q": ".qt.qf.c.test_h:{[t] .qf.c.h 1}\n",
    "tests/q/test_d.q": ".qt.qf.d.test_reuse:{[t] .qt.qf.a.helper[]}\n",
    "tests/q/test_reg.q": ".qt.qetl.r.test_run:{[t] .qetl.r.run[`d2;1]}\n",
    "tests/q/test_d1.q": ".qt.qpipe.d1.test_it:{[t] .qt.support.k[]; `d1}\n",
    "tests/q/test_text.q": ".qt.text.test_reads:{[t] read0 `:src/c.q}\n",
    "tests/q/test_layout.q": ".qt.layout.test_scans:{[t] .qt.support.k[]}\n",
    "tests/q/test_helper_user.q": ".qt.hu.test_h:{[t] .qt.support.h[]}\n",
    "tests/q/child.q": "\\l src/c.q\nx:get ` sv `.qf.c,`h\n",
    "tests/q/test_child.q": '.qt.child.test_runs:{[t] system "q tests/q/child.q"}\n',
    "data/fixture.csv": "a,b\n",
    "src/reads.q": '.qf.r.load:{("SS";enlist",") 0: `:data/fixture.csv}\n',
    "src/dyn.q": ".qf.dyn.f:{[nm] get ` sv `.qf.c,nm}\n",
    "python/x.py": "print(1)\n",
}


@pytest.fixture(scope="module")
def small(tmp_path_factory):
    root = _tree(tmp_path_factory.mktemp("tree"), _SMALL)
    return ti.build(root)


def _pick(tree, *changed):
    return ti.select(list(changed), tree)


@GRAPH
def test_a_change_reaches_its_suites_through_every_dependent(small):
    sel = _pick(small, "src/a.q")
    assert not sel.all
    # test_b through src/b.q; test_d through test_a's helper, which it calls
    assert sel.files == [
        "tests/q/test_a.q",
        "tests/q/test_b.q",
        "tests/q/test_d.q",
        "tests/q/test_helper_user.q",
    ]


@GRAPH
def test_a_suite_changed_reaches_the_suites_reusing_its_helpers(small):
    assert _pick(small, "tests/q/test_a.q").files == ["tests/q/test_a.q", "tests/q/test_d.q"]
    assert _pick(small, "tests/q/test_d.q").files == ["tests/q/test_d.q"]


@GRAPH
def test_a_declaration_reaches_the_suites_of_its_shell_and_the_suites_naming_it(small):
    sel = _pick(small, "src/etl/transforms/d1.q")
    assert sel.files == ["tests/q/test_d1.q", "tests/q/test_reg.q"]
    assert small.registries == {".qetl.r.define": "src/etl/core/reg.q"}
    assert small.registered == {
        "src/etl/transforms/d1.q": {"d1"},
        "src/etl/transforms/d2.q": {"d2"},
    }


@GRAPH
def test_a_shell_reached_by_a_declaration_does_not_fan_out_to_its_siblings(small):
    """d1's change runs the shell's suites, not every suite of d2."""
    assert "tests/q/test_d1.q" not in _pick(small, "src/etl/transforms/d2.q").files


@GRAPH
def test_a_suite_reading_a_file_as_text_or_starting_it_as_a_child_is_reached(small):
    assert _pick(small, "src/c.q").files == [
        "tests/q/test_c.q",
        "tests/q/test_child.q",
        "tests/q/test_text.q",
    ]


@GRAPH
def test_a_helper_reaches_only_the_suites_calling_it(small):
    """tests/lib is cut per definition: a src change reached by .qt.support.h
    does not reach test_layout, which calls only .qt.support.k."""
    assert "tests/q/test_layout.q" not in _pick(small, "src/a.q").files


@GRAPH
@pytest.mark.parametrize(
    ("path", "why"),
    [
        ("tests/lib/support.q", "test harness"),
        ("tests/run_tests.q", "the runner"),
        ("src/gone.q", "deleted q file"),
        ("src/dyn.q", "DYNAMIC_REVIEWED does not explain"),
    ],
)
def test_what_the_graph_cannot_judge_runs_everything(small, path, why):
    sel = _pick(small, path)
    assert sel.all and sel.files == small.suites
    assert any(why in r for r in sel.reasons), sel.reasons


@GRAPH
def test_a_file_q_reads_is_walked_from_its_reader(small):
    """data/fixture.csv is named by src/reads.q, which no suite reaches."""
    assert _pick(small, "data/fixture.csv") == ti.Selection([], 10, [])


@GRAPH
def test_a_file_a_harness_helper_reads_reaches_the_suites_calling_it(small):
    """data/h.txt is read by .qt.support.k, which test_d1 and test_layout call."""
    assert _pick(small, "data/h.txt").files == ["tests/q/test_d1.q", "tests/q/test_layout.q"]


@GRAPH
def test_a_change_reaches_the_suites_through_a_harness_helper(small):
    """src/a.q is read by .qt.support.h; a suite calling h is reached."""
    tree = small
    assert "tests/q/test_helper_user.q" in _pick(tree, "src/a.q").files


@GRAPH
def test_a_child_reaching_names_at_run_time_reaches_only_the_suite_starting_it(small):
    assert _pick(small, "tests/q/child.q") == ti.Selection(["tests/q/test_child.q"], 10, [])


@GRAPH
def test_a_path_no_q_reads_runs_nothing(small):
    assert _pick(small, "python/x.py").files == []


# --- the real tree -----------------------------------------------------------


@pytest.fixture(scope="module")
def real():
    return ti.build(REPO)


@GRAPH
def test_the_registries_the_issue_names_are_found_not_listed(real):
    shells = set(real.registries)
    assert {
        ".qetl.transform.define",
        ".qetl.source.define",
        ".qetl.job.stream.define",
        ".qetl.job.stream.normalizer.define",
        ".qetl.job.bounded.define",
    } <= shells


@GRAPH
def test_every_declaration_registers_through_an_entry_point_the_selector_knows(real):
    """The guard on the found list: a top-level call from a declaration into
    src/etl/core/ that is not an entry point is a registry the links miss -
    name its entry point so ENTRY_POINT matches it."""
    assert ti.unregistered_calls(real) == []


@GRAPH
def test_every_reviewed_dynamic_file_still_has_a_dynamic_entry(real):
    stale = [f for f in ti.DYNAMIC_REVIEWED if f not in real.dynamic]
    assert stale == [], "remove these from DYNAMIC_REVIEWED: nothing in them is dynamic now"


@GRAPH
def test_a_declaration_reaches_its_own_suite_and_the_shell_suite(real):
    sel = _pick(real, "src/etl/transforms/order_quality.q")
    assert not sel.all
    assert {"tests/q/test_order_quality.q", "tests/q/test_transform.q"} <= set(sel.files)


@GRAPH
def test_a_library_change_selects_a_subset(real):
    sel = _pick(real, "src/pricing/forwards.q")
    assert not sel.all
    assert "tests/q/test_forwards.q" in sel.files
    assert len(sel.files) < len(real.suites) / 4


@GRAPH
def test_the_selection_is_what_the_runner_can_load(real):
    """Every selected name is a suite file support.q's suite_files globs."""
    names = {Path(f).name for f in _pick(real, "src/etl/core/transform.q").files}
    on_disk = {p.name for p in (REPO / "tests/q").glob("test_*.q")}
    assert names <= on_disk


@GRAPH
def test_the_cli_prints_the_runner_arguments(capsys):
    assert ti.main(["--format", "args", "src/pricing/forwards.q"]) == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("-files ") and "test_forwards.q" in out
