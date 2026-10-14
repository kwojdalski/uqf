"""Tests for `scripts/gates/check_doc_links.py`'s anchor pass (#1336).

Quarto resolves a link's target file but not its `#fragment`, so the gate
checks fragments itself. It skipped same-page `](#anchor)` links on the belief
that Quarto caught those; it does not, so renaming a heading broke every
in-page link to it with the gate green. The anchor pass needs no Quarto, so
these run everywhere.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "check_doc_links",
    Path(__file__).resolve().parents[3] / "scripts" / "gates" / "check_doc_links.py",
)
assert _SPEC and _SPEC.loader
links = importlib.util.module_from_spec(_SPEC)
sys.modules["check_doc_links"] = links
_SPEC.loader.exec_module(links)


def _docs(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (root / rel).write_text(text)
    return root


def test_a_broken_same_page_anchor_is_reported(tmp_path):
    docs = _docs(tmp_path, {"page.md": "## Real section\n\nSee [setup](#no-such-section).\n"})
    assert links.broken_anchors(docs) == [("page.md", "#no-such-section")]


def test_a_same_page_anchor_to_a_real_heading_passes(tmp_path):
    docs = _docs(tmp_path, {"page.md": "## Real section\n\nSee [it](#real-section).\n"})
    assert links.broken_anchors(docs) == []


def test_a_broken_anchor_in_another_page_is_still_reported(tmp_path):
    docs = _docs(
        tmp_path,
        {
            "page.md": "See [other](other.md#no-such-section) and [ok](other.md#there).\n",
            "other.md": "## There\n",
        },
    )
    assert links.broken_anchors(docs) == [("page.md", "other.md#no-such-section")]


def test_an_absolute_url_is_not_ours_to_check(tmp_path):
    docs = _docs(tmp_path, {"page.md": "[x](https://example.com/a#nowhere)\n"})
    assert links.broken_anchors(docs) == []


def test_the_real_docs_resolve():
    assert links.broken_anchors() == []
