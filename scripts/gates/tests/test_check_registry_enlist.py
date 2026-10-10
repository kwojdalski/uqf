"""Tests for `scripts/gates/check_registry_enlist.py` (#1030)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "check_registry_enlist",
    Path(__file__).resolve().parents[3] / "scripts" / "gates" / "check_registry_enlist.py",
)
assert _SPEC and _SPEC.loader
gate = importlib.util.module_from_spec(_SPEC)
sys.modules["check_registry_enlist"] = gate
_SPEC.loader.exec_module(gate)


def _tree(root: Path, body: str, rel: str = "src/etl/core/kind.q") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\\d .qetl.kind\nregistry:(`symbol$())!();\n" + body)
    return root


@pytest.fixture
def no_exemptions(monkeypatch):
    monkeypatch.setattr(gate, "NOT_DICTS", {})


def test_the_real_tree_is_clean():
    assert gate.problems() == []


def test_an_enlisted_indexed_write_passes(tmp_path, no_exemptions):
    assert gate.problems(_tree(tmp_path, "define:{[n;d] registry[n]:enlist d}\n")) == []


def test_a_bare_indexed_write_fails_naming_the_line(tmp_path, no_exemptions):
    found = gate.problems(_tree(tmp_path, "define:{[n;d]\n    registry[n]:d}\n"))
    assert len(found) == 1
    assert found[0].startswith("src/etl/core/kind.q:4: registry[...]")


def test_a_fully_qualified_write_is_checked_too(tmp_path, no_exemptions):
    found = gate.problems(_tree(tmp_path, "define:{[n;d] .qetl.kind.registry[n]:d}\n"))
    assert len(found) == 1


def test_retentions_original_join_is_caught(tmp_path, no_exemptions):
    """#1028: one enlist is the list of values; the declaration itself still
    joined a table on the second insert."""
    once = "define:{[n;d] `.qetl.kind.registry set registry,enlist[n]!enlist d}\n"
    twice = "define:{[n;d] `.qetl.kind.registry set registry,enlist[n]!enlist enlist d}\n"
    assert len(gate.problems(_tree(tmp_path / "a", once))) == 1
    assert gate.problems(_tree(tmp_path / "b", twice)) == []


def test_a_read_a_reset_and_a_comment_are_not_writes(tmp_path, no_exemptions):
    body = (
        "get:{[n] first registry n}\n"
        "reset:{[] registry::(`symbol$())!()}\n"
        "drop:{[n] registry::(enlist n)_registry}\n"
        "/ registry[n]:d would collapse it\n"
    )
    assert gate.problems(_tree(tmp_path, body)) == []


def test_a_registry_that_holds_no_dicts_may_be_exempted(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "NOT_DICTS", {("src/etl/core/kind.q", "registry"): "symbol lists"})
    assert gate.problems(_tree(tmp_path, "add:{[n;s] registry[n]:s}\n")) == []


def test_an_exemption_for_a_registry_that_is_gone_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "NOT_DICTS", {("src/etl/core/gone.q", "registry"): "was tables"})
    found = gate.problems(_tree(tmp_path, ""))
    assert len(found) == 1
    assert found[0].startswith("NOT_DICTS lists registry in src/etl/core/gone.q")
