"""Tests for `scripts/gates/check_declaration_reference.py` (#1331).

The gate held only five of the page's eight kind sections to their q files:
the Horizon, Bars and Rolling-window sections were never read, so a required
key could lose its row there, or a row could name a key the kind never took,
and the gate still passed. Each case below edits one of those sections of the
real page and must fail; the last holds BLOCKS itself to the declaring files,
so the next kind cannot be left out the same way.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "check_declaration_reference",
    Path(__file__).resolve().parents[3] / "scripts" / "gates" / "check_declaration_reference.py",
)
assert _SPEC and _SPEC.loader
gate = importlib.util.module_from_spec(_SPEC)
sys.modules["check_declaration_reference"] = gate
_SPEC.loader.exec_module(gate)

PAGE = gate.PAGE.read_text()

#: section heading -> a key its q file REQUIRES
KINDS = {
    "## Horizon job": "horizon",
    "## Bars job": "width",
    "## Rolling-window job": "windows",
}


def _section_edit(heading: str, edit) -> str:
    """The page with `edit` applied to the lines of one section only."""
    start = PAGE.index(heading)
    end = PAGE.index("\n## ", start + len(heading))
    return PAGE[:start] + edit(PAGE[start:end]) + PAGE[end:]


def test_the_real_page_agrees_with_the_tree():
    assert gate.check(PAGE) == []


@pytest.mark.parametrize("heading", sorted(KINDS))
def test_a_required_key_without_a_row_fails(heading):
    key = KINDS[heading]
    page = _section_edit(heading, lambda s: re.sub(rf"(?m)^\s*\| `{key}` .*\n", "", s))
    assert any(f"declares `{key}` but the page has no row" in p for p in gate.check(page))


@pytest.mark.parametrize("heading", sorted(KINDS))
def test_a_row_for_a_key_the_kind_never_takes_fails(heading):
    page = _section_edit(
        heading,
        lambda s: s.replace(
            "  | `procname` ", "  | `no_such_key` | no | x | y | z |\n  | `procname` ", 1
        ),
    )
    assert any("describes `no_such_key`" in p for p in gate.check(page))


def test_an_optional_key_on_a_continuation_line_is_read():
    # horizon.q's optional_keys runs onto a second, indented line.
    page = _section_edit("## Horizon job", lambda s: re.sub(r"(?m)^\s*\| `lookback` .*\n", "", s))
    assert any("declares `lookback`" in p for p in gate.check(page))


def test_every_key_list_in_core_belongs_to_a_block():
    covered = {
        (path.name, name)
        for file, lists, _ in gate.BLOCKS.values()
        for path in gate.CORE.glob(file)
        for name in lists
    }
    found = {
        (path.name, name)
        for path in sorted(gate.CORE.glob("*.q"))
        for name in gate.DECLARATION_LIST.findall(path.read_text())
    }
    assert found, "no key lists found - has the declaring code moved?"
    assert found <= covered, f"key lists no BLOCKS entry checks: {sorted(found - covered)}"
