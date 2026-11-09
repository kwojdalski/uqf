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

import pytest

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


@pytest.mark.parametrize(
    ("heading", "anchor"),
    [
        # Each space is a hyphen: punctuation between two spaces leaves two (#2407).
        ("2 · The tickerplant", "2--the-tickerplant"),
        ("A & B", "a--b"),
        # `_` inside inline code is kept, as GitHub keeps it.
        ("The `snake_case_name` field", "the-snake_case_name-field"),
        (
            "`crypto_trades.source_time`: history has no venue time (#1936)",
            "crypto_tradessource_time-history-has-no-venue-time-1936",
        ),
        ("Plain snake_case words", "plain-snake_case-words"),
        ("An *emphasised* and _underlined_ word", "an-emphasised-and-underlined-word"),
    ],
)
def test_the_slug_is_githubs(heading, anchor):
    assert links.slug(heading) == anchor


def test_a_repeated_heading_is_numbered_as_github_numbers_it(tmp_path):
    page = tmp_path / "page.md"
    page.write_text("## Overview\n\n## Overview\n\n## Overview\n")
    assert {"overview", "overview-1", "overview-2"} <= links.anchors_in(page)


def test_a_link_github_would_not_resolve_is_reported(tmp_path):
    docs = _docs(
        tmp_path,
        {
            "page.md": "[bad](t.md#2-the-tickerplant) [ok](t.md#2--the-tickerplant)\n",
            "t.md": "## 2 · The tickerplant\n",
        },
    )
    assert links.broken_anchors(docs) == [("page.md", "t.md#2-the-tickerplant")]


def test_the_real_docs_resolve():
    assert links.broken_anchors() == []


@pytest.fixture
def rendered_site(monkeypatch):
    """A renderer that writes a site, just as Quarto does even on warnings."""
    outputs = []

    def render(exe, out_dir):
        path = Path(out_dir)
        path.mkdir(parents=True, exist_ok=True)
        (path / "index.html").write_text("<h1>Checked site</h1>")
        outputs.append(path)
        return 0, ["[ 1/1] page.md"]

    monkeypatch.setattr(links, "quarto", lambda: "quarto")
    monkeypatch.setattr(links, "render", render)
    monkeypatch.setattr(links, "broken_anchors", lambda: [])
    return outputs


def test_an_explicit_output_keeps_the_checked_site(tmp_path, rendered_site):
    out = tmp_path / "site"
    assert links.main(["--output-dir", str(out)]) == 0
    assert (out / "index.html").read_text() == "<h1>Checked site</h1>"
    assert rendered_site == [out]


def test_a_default_render_still_leaves_no_artifacts(rendered_site):
    assert links.main([]) == 0
    assert len(rendered_site) == 1
    assert not rendered_site[0].exists()


@pytest.mark.parametrize(
    ("code", "lines", "anchors", "message"),
    [
        (0, ["[ 1/1] page.md", "Unable to resolve link target: gone.md"], [], "gone.md"),
        (0, ["Unable to resolve crossref: sec-gone"], [], "sec-gone"),
        (0, [], [("page.md", "#gone")], "#gone"),
        (1, ["ERROR: render failed"], [], "render failed"),
    ],
)
def test_keeping_the_output_still_fails_broken_links_and_renders(
    tmp_path, monkeypatch, rendered_site, capsys, code, lines, anchors, message
):
    monkeypatch.setattr(links, "render", lambda *_: (code, lines))
    monkeypatch.setattr(links, "broken_anchors", lambda: anchors)
    assert links.main(["--output-dir", str(tmp_path / "site")]) == 1
    assert message in capsys.readouterr().err
