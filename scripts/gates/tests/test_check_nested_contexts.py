"""The nested-contexts rule, `scripts/gates/policy/nested_contexts.py`: no `\\d` names
more than one level (#1411)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

_SPEC = importlib.util.spec_from_file_location(
    "nested_contexts", REPO / "scripts" / "gates" / "policy" / "nested_contexts.py"
)
assert _SPEC and _SPEC.loader
cnc = importlib.util.module_from_spec(_SPEC)
sys.modules["nested_contexts"] = cnc
_SPEC.loader.exec_module(cnc)


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    return tmp_path


def test_the_real_tree_is_clean():
    assert cnc.problems(REPO) == []


def test_the_scope_is_one_list_that_names_what_it_scans():
    assert {"src", "scripts", "sidecars", "docs"} <= set(cnc.SCOPE)


def test_a_two_level_context_fails_naming_its_line(tmp_path):
    root = _tree(tmp_path, {"src/a.q": "x:1\n\\d .qf.px\nf:{x}\n\\d .\n"})
    out = cnc.problems(root, ("src",))
    assert len(out) == 1 and out[0].startswith("src/a.q:2: `\\d .qf.px`")


def test_the_root_and_a_one_level_context_pass(tmp_path):
    root = _tree(tmp_path, {"src/a.q": "\\d .qtorq\nf:{x}\n\\d .\n.qf.px.g:{x}\n"})
    assert cnc.problems(root, ("src",)) == []


def test_the_same_switch_as_a_command_fails(tmp_path):
    root = _tree(tmp_path, {"src/a.q": 'system"d .a.b";\n'})
    assert len(cnc.problems(root, ("src",))) == 1


def test_a_q_fence_in_a_document_is_read_and_prose_is_not(tmp_path):
    doc = "Say `\\d .a.b` in prose.\n\n```q\n\\d .a.b\nf:{x}\n```\n"
    root = _tree(tmp_path, {"docs/g.md": doc})
    out = cnc.problems(root, ("docs",))
    assert len(out) == 1 and out[0].startswith("docs/g.md:4:")


def test_a_path_outside_the_scope_is_not_read(tmp_path):
    root = _tree(tmp_path, {"lib/v.q": "\\d .a.b\n", "src/ok.q": "x:1\n"})
    assert cnc.problems(root, ("src",)) == []
