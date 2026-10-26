"""Tests for `scripts/gates/check_hook_scopes.py`'s coverage half.

The gate exists to fail on a hook that is valid but too narrow. It read only
each gate hook's `files:`, so an `exclude:` that took nearly all Python out
of the lint, test and type gates still reported full coverage (#1335). Each
case writes a small config and a fixed file list, so what is judged is the
scope logic alone, not this repository's current config.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "check_hook_scopes",
    Path(__file__).resolve().parents[3] / "scripts" / "gates" / "check_hook_scopes.py",
)
assert _SPEC and _SPEC.loader
chs = importlib.util.module_from_spec(_SPEC)
sys.modules["check_hook_scopes"] = chs
_SPEC.loader.exec_module(chs)

FILES = [
    "lib/vendored/a.py",
    "python/uqs/src/uqs/cli.py",
    "python/uqs/tests/test_cli.py",
    "scripts/gates/check_x.py",
]


def _config(exclude: str) -> str:
    hooks = "".join(
        f"      - id: {hook}\n        files: '\\.py$'\n        exclude: '{exclude}'\n"
        for hook in ("ruff", "ruff-format", "python-tests", "ty")
    )
    return f"repos:\n  - repo: local\n    hooks:\n{hooks}"


@pytest.fixture
def run(tmp_path, monkeypatch, capsys):
    def go(config_text: str) -> tuple[int, str]:
        config = tmp_path / ".pre-commit-config.yaml"
        config.write_text(config_text)
        monkeypatch.setattr(chs, "CONFIG", config)
        monkeypatch.setattr(chs, "tracked_files", lambda: FILES)
        code = chs.main()
        out = capsys.readouterr()
        return code, out.out + out.err

    return go


def test_an_exclude_that_drops_python_from_every_gate_is_reported(run):
    code, out = run(_config("^(lib|python)/"))
    assert code == 1
    assert "1/3 tracked .py file(s) covered by the lint gate" in out
    assert "UNGATED (lint)  python/uqs" in out
    assert "UNGATED (test)  python/uqs" in out
    assert "UNGATED (type)  python/uqs" in out


def test_the_vendored_exclude_alone_leaves_everything_gated(run):
    code, out = run(_config("^lib/"))
    assert code == 0, out
    assert "3/3 tracked .py file(s) covered by the lint gate" in out
    assert "UNGATED" not in out


def test_the_real_config_gates_all_tracked_python():
    """The repository's own config passes, so the hook is committable."""
    assert chs.main() == 0


# --- the q-docs scopes name exactly the documents with q-example blocks ----

DOCS = {"docs/a.md", "docs/b.md", "docs/plain.md", "README.md"}
MARKED = {"docs/a.md", "docs/b.md"}


def _doc_hooks(pattern: str) -> list[tuple[str, str, str | None]]:
    return [(hook, pattern, None) for hook in chs.DOC_HOOKS] + [("q-tests", r"\.q$", None)]


def test_a_doc_scope_naming_exactly_the_marked_documents_passes():
    assert chs.doc_scope_drift(_doc_hooks(r"\.q$|^docs/(a|b)\.md$"), DOCS, MARKED) == []


def test_a_marked_document_outside_the_doc_scope_is_reported():
    """Its block would go unrun on a docs edit, and unattested in CI."""
    problems = chs.doc_scope_drift(_doc_hooks(r"\.q$|^docs/a\.md$"), DOCS, MARKED)
    assert problems == [
        f"{hook}: docs/b.md has a q-example block its scope does not take" for hook in chs.DOC_HOOKS
    ]


def test_a_doc_scope_taking_an_unmarked_document_is_reported():
    """An edit to it would ask for a KDB-X attestation that judges nothing."""
    problems = chs.doc_scope_drift(_doc_hooks(r"\.(q|md)$"), DOCS, MARKED)
    assert "q-docs: its scope takes docs/plain.md, which has no q-example block" in problems
    assert "q-docs: its scope takes README.md, which has no q-example block" in problems


def test_the_real_doc_scopes_match_the_marked_documents():
    hooks = chs.scoped_hooks(chs.CONFIG.read_text())
    assert {h for h, _, _ in hooks} >= set(chs.DOC_HOOKS)
    assert chs.doc_scope_drift(hooks, *chs.doc_files_and_marked()) == []
