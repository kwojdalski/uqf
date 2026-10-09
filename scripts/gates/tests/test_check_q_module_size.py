"""Tests for `scripts/gates/check_q_module_size.py` (#970)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "check_q_module_size",
    Path(__file__).resolve().parents[3] / "scripts" / "gates" / "check_q_module_size.py",
)
assert _SPEC and _SPEC.loader
gate = importlib.util.module_from_spec(_SPEC)
sys.modules["check_q_module_size"] = gate
_SPEC.loader.exec_module(gate)


def _tree(root: Path, sizes: dict[str, int]) -> Path:
    for rel, n in sizes.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x:1\n" * n)
    return root


def test_the_real_tree_is_clean():
    assert gate.problems() == []


def test_a_file_over_the_limit_is_named(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "ALLOWED", {})
    root = _tree(tmp_path, {"src/a/big.q": gate.LIMIT + 1, "src/a/ok.q": gate.LIMIT})
    found = gate.problems(root)
    assert len(found) == 1 and found[0].startswith("src/a/big.q: 801 lines")


def test_an_allowlisted_file_may_not_grow(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "ALLOWED", {"src/a/big.q": (900, "why")})
    found = gate.problems(_tree(tmp_path, {"src/a/big.q": 901}))
    assert len(found) == 1 and "over its cap of 900" in found[0]


def test_an_allowlisted_file_that_shrank_must_lower_its_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "ALLOWED", {"src/a/big.q": (900, "why")})
    found = gate.problems(_tree(tmp_path, {"src/a/big.q": 850}))
    assert len(found) == 1 and "lower the cap to 850" in found[0]


def test_a_split_file_must_leave_the_allowlist(tmp_path, monkeypatch):
    monkeypatch.setattr(
        gate, "ALLOWED", {"src/a/big.q": (900, "why"), "src/a/gone.q": (900, "why")}
    )
    found = gate.problems(_tree(tmp_path, {"src/a/big.q": 100}))
    assert any("remove it from ALLOWED" in f for f in found)
    assert any("src/a/gone.q, which does not exist" in f for f in found)


def test_generated_files_are_not_counted(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "ALLOWED", {})
    assert gate.problems(_tree(tmp_path, {"src/etl/generated/p.q": gate.LIMIT * 2})) == []
