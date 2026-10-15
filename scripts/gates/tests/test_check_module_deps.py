"""check_module_deps.py: the library's module graph is reviewed and acyclic (#626).

Since #1411 the modules load into seven `.qf.<domain>` namespaces, so a module is
a source file (or a few files grouped by concern) and an edge is found by asking
which file defines the name a qualified reference points at.
"""

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
    assert ("execution", "forwards") not in found
    assert ("forwards", "execution") not in found
    assert ("cross", "execution") not in found
    assert not {(a, b) for a, b in found if a in cmd.FOUNDATION and b not in cmd.FOUNDATION}


def test_every_library_file_is_in_a_domain_or_the_data_module():
    """No library file may sit outside the seven `.qf` domains, bar the
    deliberately separate `.qdata` (#1411)."""
    namespaces = set(cmd.files().values())
    assert namespaces <= {
        ".qf.px",
        ".qf.mkt",
        ".qf.exec",
        ".qf.port",
        ".qf.math",
        ".qf.util",
        ".qf.demo",
        ".qdata",
    }, namespaces


def test_a_new_edge_fails_naming_its_line(tmp_path):
    root = _tree(
        tmp_path,
        {
            "src/foundation/a.q": "\\d .qf.px\nf:{x}\n\\d .\n",
            "src/pricing/b.q": "\\d .qf.mkt\n/ .qf.px.f in a comment\ng:{.qf.px.f x}\n\\d .\n",
        },
    )
    out = cmd.problems(root, frozenset())
    assert out == [
        "src/pricing/b.q:3: b -> a is a new module dependency - add it to ALLOWED in "
        "scripts/gates/check_module_deps.py in the same change, or call something the "
        "module may already depend on"
    ]


def test_an_edge_inside_one_domain_is_still_an_edge(tmp_path):
    """Two files of one namespace are still two modules: sharing `.qf.px` is not
    permission to depend on each other unreviewed."""
    root = _tree(
        tmp_path,
        {
            "src/foundation/a.q": "\\d .qf.px\nf:{x}\n\\d .\n",
            "src/pricing/b.q": "\\d .qf.px\ng:{.qf.px.f x}\n\\d .\n",
        },
    )
    assert set(cmd.edges(root)) == {("b", "a")}


def test_strings_and_comments_are_not_edges(tmp_path):
    root = _tree(
        tmp_path,
        {
            "src/foundation/a.q": "\\d .qf.px\nf:{x}\n\\d .\n",
            "src/pricing/b.q": (
                '\\d .qf.mkt\ng:{\'"see .qf.px.f"} / .qf.px.f is the other one\n\\d .\n'
            ),
        },
    )
    assert cmd.edges(root) == {}


def test_a_reference_to_a_name_nobody_defines_is_not_an_edge(tmp_path):
    root = _tree(
        tmp_path,
        {
            "src/foundation/a.q": "\\d .qf.px\nf:{x}\n\\d .\n",
            "src/pricing/b.q": "\\d .qf.mkt\ng:{.qf.px.nothing x}\n\\d .\n",
        },
    )
    assert cmd.edges(root) == {}


def test_a_stale_allowed_edge_fails(tmp_path):
    root = _tree(tmp_path, {"src/foundation/a.q": "\\d .qf.px\nf:{x}\n\\d .\n"})
    assert cmd.problems(root, frozenset({("a", "b")})) == [
        "ALLOWED lists a -> b, which no code makes - remove it"
    ]


def test_a_cycle_fails(tmp_path):
    root = _tree(
        tmp_path,
        {
            "src/pricing/a.q": "\\d .qf.px\nf:{.qf.mkt.g x}\n\\d .\n",
            "src/execution/b.q": "\\d .qf.mkt\ng:{.qf.px.f x}\n\\d .\n",
        },
    )
    out = cmd.problems(root, frozenset({("a", "b"), ("b", "a")}))
    assert any("cycle: a -> b -> a" in p for p in out), out


def test_a_removed_edge_is_refused_even_when_listed(tmp_path):
    root = _tree(
        tmp_path,
        {
            "src/execution/execution.q": "\\d .qf.exec\nf:{x}\n\\d .\n",
            "src/pricing/cross.q": "\\d .qf.mkt\ng:{.qf.exec.f x}\n\\d .\n",
        },
    )
    out = cmd.problems(root, frozenset({("cross", "execution")}))
    assert "src/pricing/cross.q:2: cross -> execution is refused" in out[0]


def test_a_module_split_across_files_keeps_every_files_edges(tmp_path):
    """A module split by concern keeps its edges (#990): the second file must
    not hide the first one's dependencies, and its calls into the first are not
    an edge at all."""
    root = _tree(
        tmp_path,
        {
            "src/foundation/a.q": "\\d .qf.px\nf:{x}\n\\d .\n",
            "src/foundation/c.q": "\\d .qf.px\nh:{x}\n\\d .\n",
            "src/pricing/b.q": "\\d .qf.mkt\ng:{.qf.px.f x}\n\\d .\n",
            "src/pricing/b_more.q": "\\d .qf.mkt\nk:{.qf.px.h x}\nm:{.qf.mkt.g x}\n\\d .\n",
        },
    )
    groups = {"b_more": "b"}
    assert set(cmd.edges(root, groups)) == {("b", "a"), ("b", "c")}


def test_a_name_defined_in_two_files_of_one_namespace_fails(tmp_path):
    """Load order must never pick the winner: the second definition silently
    replaces the first and the first file's callers get the wrong function."""
    root = _tree(
        tmp_path,
        {
            "src/pricing/a.q": "\\d .qf.px\nclash:{x}\nonly_a:{x}\n\\d .\n",
            "src/pricing/b.q": "\\d .qf.px\nclash:{x+1}\n\\d .\n",
        },
    )
    out = cmd.duplicates(root)
    assert out == [
        ".qf.px.clash is defined in src/pricing/a.q and src/pricing/b.q - the later load "
        "silently wins; rename the narrower one"
    ]


def test_the_same_name_in_two_different_domains_is_fine(tmp_path):
    root = _tree(
        tmp_path,
        {
            "src/pricing/a.q": "\\d .qf.px\nsame:{x}\n\\d .\n",
            "src/pricing/b.q": "\\d .qf.mkt\nsame:{x}\n\\d .\n",
        },
    )
    assert cmd.duplicates(root) == []


def test_a_namespace_only_file_is_read_for_duplicates(tmp_path):
    """src/namespaces.q is no module of the graph, but its names share `.qf.util`."""
    root = _tree(
        tmp_path,
        {
            "src/foundation/schema.q": "\\d .qf.util\nowned:{x}\n\\d .\n",
            "src/namespaces.q": "\\d .qf.util\nowned:{x}\n\\d .\n",
        },
    )
    assert len(cmd.duplicates(root)) == 1
