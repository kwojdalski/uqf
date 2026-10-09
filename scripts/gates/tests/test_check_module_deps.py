"""check_module_deps.py: the library's module graph is reviewed and acyclic (#626)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

# scripts/ is not a package, so the gate is loaded by path, as the other gate
# tests load theirs.
_SPEC = importlib.util.spec_from_file_location(
    "check_module_deps", REPO / "scripts" / "gates" / "check_module_deps.py"
)
assert _SPEC and _SPEC.loader
cmd = importlib.util.module_from_spec(_SPEC)
sys.modules["check_module_deps"] = cmd
_SPEC.loader.exec_module(cmd)


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    return tmp_path


def test_the_real_library_passes():
    assert cmd.problems() == []


def test_the_restructured_edges_are_gone():
    """#626's acceptance: execution no longer reaches into forwards, synthetic
    pricing does not depend on execution, and foundation depends on nothing
    above it."""
    found = set(cmd.edges())
    assert ("qexec", "qfwd") not in found
    assert ("qfwd", "qexec") not in found
    assert ("qcross", "qexec") not in found
    assert not {(a, b) for a, b in found if a in cmd.FOUNDATION and b not in cmd.FOUNDATION}


def test_a_new_edge_fails_naming_its_line(tmp_path):
    root = _tree(
        tmp_path,
        {
            "src/foundation/a.q": "\\d .qa\nf:{x}\n",
            "src/pricing/b.q": "\\d .qb\n/ .qa.f in a comment\ng:{.qa.f x}\n",
        },
    )
    out = cmd.problems(root, frozenset())
    assert out == [
        "src/pricing/b.q:3: .qb -> .qa is a new module dependency - add it to ALLOWED in "
        "scripts/gates/check_module_deps.py in the same change, or call something the "
        "module may already depend on"
    ]


def test_strings_and_comments_are_not_edges(tmp_path):
    root = _tree(
        tmp_path,
        {
            "src/foundation/a.q": "\\d .qa\nf:{x}\n",
            "src/pricing/b.q": '\\d .qb\ng:{\'"see .qa.f"} / .qa.f is the other one\n',
        },
    )
    assert cmd.edges(root) == {}


def test_a_stale_allowed_edge_fails(tmp_path):
    root = _tree(tmp_path, {"src/foundation/a.q": "\\d .qa\nf:{x}\n"})
    assert cmd.problems(root, frozenset({("qa", "qb")})) == [
        "ALLOWED lists .qa -> .qb, which no code makes - remove it"
    ]


def test_a_cycle_fails(tmp_path):
    root = _tree(
        tmp_path,
        {
            "src/pricing/a.q": "\\d .qa\nf:{.qb.g x}\n",
            "src/execution/b.q": "\\d .qb\ng:{.qa.f x}\n",
        },
    )
    out = cmd.problems(root, frozenset({("qa", "qb"), ("qb", "qa")}))
    assert any("cycle: .qa -> .qb -> .qa" in p for p in out), out


def test_a_removed_edge_is_refused_even_when_listed(tmp_path):
    root = _tree(
        tmp_path,
        {
            "src/execution/e.q": "\\d .qexec\nf:{x}\n",
            "src/pricing/c.q": "\\d .qcross\ng:{.qexec.f x}\n",
        },
    )
    out = cmd.problems(root, frozenset({("qcross", "qexec")}))
    assert "src/pricing/c.q:2: .qcross -> .qexec is refused" in out[0]


def test_a_namespace_split_across_files_keeps_every_files_edges(tmp_path):
    """A module split by concern keeps its namespace (#990): the second file
    must not hide the first one's dependencies."""
    root = _tree(
        tmp_path,
        {
            "src/foundation/a.q": "\\d .qa\nf:{x}\n",
            "src/foundation/c.q": "\\d .qc\nh:{x}\n",
            "src/pricing/b.q": "\\d .qb\ng:{.qa.f x}\n",
            "src/pricing/b_more.q": "\\d .qb\nk:{.qc.h x}\n",
        },
    )
    assert set(cmd.edges(root)) == {("qb", "qa"), ("qb", "qc")}
