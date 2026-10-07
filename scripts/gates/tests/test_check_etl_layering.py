"""Tests for `scripts/gates/check_etl_layering.py`'s TorQ-owner rule (#619).

The gate ran in CI with no tests of its own. The rule added here - each TorQ
namespace read by one owner file in src/ - gets a case it must flag and one it
must not for each way a read is spelled, plus the real tree, which must be
clean. Against master before #619 it reported the three process-name reads in
bounded_worker.q and uptime.q, and nothing else.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "check_etl_layering",
    Path(__file__).resolve().parents[3] / "scripts" / "gates" / "check_etl_layering.py",
)
assert _SPEC and _SPEC.loader
layering = importlib.util.module_from_spec(_SPEC)
sys.modules["check_etl_layering"] = layering
_SPEC.loader.exec_module(layering)


def _tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


@pytest.mark.parametrize("ns", sorted(layering.TORQ_OWNERS))
def test_the_owner_file_may_read_its_namespace(tmp_path, ns):
    owner = layering.TORQ_OWNERS[ns]
    root = _tree(tmp_path, {owner: f"x:@[value;`{ns}.thing;`]\ny:{ns}.thing\n"})
    assert layering.torq_reach(root) == []


@pytest.mark.parametrize("ns", sorted(layering.TORQ_OWNERS))
def test_any_other_file_reading_it_is_flagged_with_the_owner(tmp_path, ns):
    root = _tree(tmp_path, {"src/etl/core/other.q": f"p:@[value;`{ns}.thing;`]\n"})
    (hit,) = layering.torq_reach(root)
    assert hit.startswith("src/etl/core/other.q:1:")
    assert layering.TORQ_OWNERS[ns] in hit


def test_the_quant_library_is_held_to_it_too(tmp_path):
    root = _tree(tmp_path, {"src/pricing/forwards.q": "n:.proc.procname\n"})
    assert len(layering.torq_reach(root)) == 1


def test_a_namespace_given_as_a_symbol_is_a_read(tmp_path):
    root = _tree(tmp_path, {"src/etl/core/other.q": "t:@[{`l in key x};`.lg;{0b}]\n"})
    assert len(layering.torq_reach(root)) == 1


def test_a_comment_or_a_string_is_not_a_read(tmp_path):
    root = _tree(
        tmp_path,
        {"src/etl/core/other.q": '/ reads .proc.procname\nm:"see .lg.l"\n'},
    )
    assert layering.torq_reach(root) == []


def test_a_name_that_only_contains_one_is_not_a_read(tmp_path):
    root = _tree(
        tmp_path,
        {"src/etl/core/other.q": "a:.qetl.run.proc_name[]\nb:.foo.lg.x\nc:.processes\n"},
    )
    assert layering.torq_reach(root) == []


def test_the_real_tree_is_clean(monkeypatch):
    assert layering.torq_reach(layering.REPO) == []
    monkeypatch.setattr(sys, "argv", ["check_etl_layering.py", "--check"])
    assert layering.main() == 0
