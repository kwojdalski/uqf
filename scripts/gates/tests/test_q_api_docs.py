"""Tests for `scripts/generate/q_api_docs.py`, the q API pages under
docs/reference/q-api/.

Most cases feed it records shaped like the ones docs/man.q exports, so they
need no q: what reaches a page, what is refused, and what a regeneration may
delete. The last runs the whole path - the git index, the reader on a real q
and the render - and is skipped where there is no q.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]

# scripts/ is not a package, so the script is loaded by path - the same
# idiom test_render_diagrams.py uses.
_SPEC = importlib.util.spec_from_file_location(
    "q_api_docs", REPO / "scripts" / "generate" / "q_api_docs.py"
)
assert _SPEC and _SPEC.loader
q_api_docs = importlib.util.module_from_spec(_SPEC)
sys.modules["q_api_docs"] = q_api_docs
_SPEC.loader.exec_module(q_api_docs)

GenerationError = q_api_docs.GenerationError


def records(funcs=(), args=(), files=(), private=()):
    return {
        "funcs": [
            {
                "fullname": n,
                "ns": n.rsplit(".", 1)[0],
                "description": d,
                "name": n,
                "other": "",
                "file": f,
            }
            for n, d, f in funcs
        ],
        "args": [{"fullname": n, "tag": t, "param": p, "description": d} for n, t, p, d in args],
        "files": [
            {"title": f.rsplit("/", 1)[-1], "author": "", "namespaces": ns, "header": h, "file": f}
            for f, ns, h in files
        ],
        "private": list(private),
    }


TWO_CROSSES = records(
    funcs=[
        (".qf.px.cross_rate", "Triangulate.\nSecond paragraph.", "src/pricing/cross.q"),
        (".qpipe.job.cross.on_tick", "Publish a cross.", "src/etl/streaming/cross.q"),
    ],
    args=[
        (".qf.px.cross_rate", "param", "ab_rate", "rate for A/B"),
        (".qf.px.cross_rate", "param", "bc_rate", "rate for B/C"),
        (".qf.px.cross_rate", "return", "", "the rate for A/C"),
        (".qf.px.cross_rate", "throws", "", "when a rate is not positive"),
        (".qf.px.cross_rate", "eg", "", ".qf.px.cross_rate[1.10;150]  -> 165f"),
        (".qf.px.cross_rate", "eg", "", ".qf.px.cross_rate[1;1]  -> 1f"),
    ],
    files=[
        ("src/pricing/cross.q", ".qf.px", "cross.q - crosses."),
        ("src/etl/streaming/cross.q", ".qpipe.job.cross", ""),
    ],
    private=[".qf.px.helper"],
)
SOURCES = ["src/pricing/cross.q", "src/etl/streaming/cross.q"]


def test_two_modules_with_one_basename_get_two_pages():
    modules = q_api_docs.modules_from(TWO_CROSSES, SOURCES)
    pages = q_api_docs.render(modules)
    assert sorted(pages) == ["index.md", "src/etl/streaming/cross.md", "src/pricing/cross.md"]


def test_every_documented_field_reaches_the_page():
    page = q_api_docs.render(q_api_docs.modules_from(TWO_CROSSES, SOURCES))["src/pricing/cross.md"]
    assert "### `.qf.px.cross_rate` {#qf.px.cross_rate}" in page
    assert "Triangulate\\.\n\nSecond paragraph\\." in page, "paragraphs stay paragraphs"
    assert "- `ab_rate` --- rate for A\\/B" in page and "- `bc_rate` --- rate for B\\/C" in page
    assert "**Returns** the rate for A\\/C" in page
    assert "- when a rate is not positive" in page
    assert "```q\n.qf.px.cross_rate[1.10;150]  -> 165f\n```" in page
    assert "```q\n.qf.px.cross_rate[1;1]  -> 1f\n```" in page, "every example, not just the first"
    assert ".qf.px.helper" not in page


def test_the_index_links_each_name_once_to_its_anchor():
    index = q_api_docs.render(q_api_docs.modules_from(TWO_CROSSES, SOURCES))["index.md"]
    assert index.count("](src/pricing/cross.md#qf.px.cross_rate)") == 1
    assert index.count("](src/etl/streaming/cross.md#qpipe.job.cross.on_tick)") == 1


def test_prose_is_escaped_so_it_renders_as_written():
    # A lone backtick before a q symbol would open a code span, `$` math and
    # `@` a citation.
    assert q_api_docs.text("`sym costs $1 @ 2*3") == "\\`sym costs \\$1 \\@ 2\\*3"
    assert q_api_docs.code("a`b") == "``a`b``"
    assert q_api_docs.code_block("x:```a") == "````q\nx:```a\n````"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            lambda r: r["funcs"].append(dict(r["funcs"][1], file="src/pricing/cross.q")),
            "documented in both",
        ),
        (lambda r: r["args"].append(r["args"][2] | {"description": "again"}), "2 @return lines"),
        (lambda r: r["args"].append(r["args"][0]), "@param ab_rate more than once"),
        (
            lambda r: r["funcs"].__setitem__(0, dict(r["funcs"][0], file="src/etl/installed.q")),
            "not in the snapshot",
        ),
        (lambda r: r["private"].append(".qf.px.cross_rate"), "tagged @private"),
        (lambda r: r.update(funcs=[], args=[]), "no documented names"),
    ],
)
def test_an_incomplete_or_ambiguous_reference_is_refused(change, message):
    import copy

    r = copy.deepcopy(TWO_CROSSES)
    change(r)
    with pytest.raises(GenerationError, match=message):
        q_api_docs.modules_from(r, SOURCES)


def test_regeneration_removes_only_its_own_obsolete_pages(tmp_path):
    pages = q_api_docs.render(q_api_docs.modules_from(TWO_CROSSES, SOURCES))
    assert q_api_docs.sync(pages, tmp_path) == (3, 0)
    assert q_api_docs.sync(pages, tmp_path) == (0, 0), "an unchanged page is not rewritten"

    fewer = dict(pages)
    del fewer["src/etl/streaming/cross.md"]
    assert q_api_docs.sync(fewer, tmp_path) == (0, 1)
    assert not (tmp_path / "src" / "etl").exists(), "the emptied directory goes too"

    (tmp_path / "src" / "pricing" / "cross.html").write_text("left by a render")
    assert q_api_docs.sync(fewer, tmp_path) == (0, 1), "a render's leftovers go too"

    (tmp_path / "notes.md").write_text("# mine\n")
    with pytest.raises(GenerationError, match="did not write: notes.md"):
        q_api_docs.sync(fewer, tmp_path)
    assert (tmp_path / "src" / "pricing" / "cross.md").is_file(), "nothing changed on refusal"


def test_the_output_directory_is_not_committed():
    out = q_api_docs.OUT.relative_to(REPO) / "index.md"
    result = subprocess.run(["git", "check-ignore", "-q", str(out)], cwd=REPO, check=False)
    assert result.returncode == 0, f"{out} must be gitignored: the pages are built, never committed"


def _q() -> str | None:
    return shutil.which(os.environ.get("QCMD") or "q")


@pytest.mark.skipif(_q() is None, reason="no q interpreter - set $QCMD, or put q on PATH")
def test_the_tree_renders_on_the_q_at_hand(tmp_path):
    """The whole path on this tree: the git index, the reader, the pages."""
    result = subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts" / "generate" / "q_api_docs.py"),
            "--out",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    pages = {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*.md")}
    assert {"index.md", "src/pricing/cross.md", "src/etl/streaming/cross.md"} <= pages
    forwards = (tmp_path / "src" / "pricing" / "forwards.md").read_text()
    assert "{#qf.px.fwd_simple}" in forwards
    assert "**Examples**" in (tmp_path / "src" / "pricing" / "options.md").read_text()
