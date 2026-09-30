"""The q-example markers in docs/, checked without q.

scripts/dev/doc_examples.py turns marked ```q blocks into q sessions for the
q-docs lane. Running them needs an interpreter; reading the markers does not,
so a marker that says nothing runnable - a typo'd mode, a marker adrift from
its fence, a transcript with no `q)` line - fails here, in the Python lane
every CI run has.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "dev" / "doc_examples.py"
_spec = importlib.util.spec_from_file_location("uqf_doc_examples", SCRIPT)
assert _spec and _spec.loader
docex = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = docex
_spec.loader.exec_module(docex)

DOC = Path("docs/x.md")


def test_every_marker_in_the_docs_is_well_formed():
    blocks = docex.all_blocks()
    assert blocks, "nothing is marked, so the q-docs lane would run nothing"
    assert {b.mode for b in blocks} == {"run", "transcript"}


def test_a_marked_block_is_read_with_its_mode_and_reason():
    text = "<!-- q-example: run kdbx-only: needs -11! log replay -->\n```q\n1+1\n```\n"
    (block,) = docex.blocks_in(text, DOC)
    assert (block.mode, block.kdbx_only, block.body, block.line) == (
        "run",
        "needs -11! log replay",
        "1+1",
        2,
    )


def test_an_unmarked_block_is_not_run():
    assert docex.blocks_in("```q\n1+1\n```\n", DOC) == []


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("<!-- q-example: runs -->\n```q\n1\n```\n", "mode 'runs'"),
        ("<!-- q-example: run -->\n\n```q\n1\n```\n", "directly above a ```q fence"),
        ("<!-- q-example: run -->\n```sh\nls\n```\n", "directly above a ```q fence"),
        ("<!-- q-example: run -->\n```q\n1\n", "never closed"),
        ("<!-- q-example: transcript -->\n```q\n1+1\n```\n", "has none"),
        ("<!-- q-example: run -->\n```q\nq)1+1\n2\n```\n", "mark it `transcript`"),
        ("<!-- q-example: run because -->\n```q\n1\n```\n", "kdbx-only: REASON"),
        ("<!-- q-example: run kdbx-only: -->\n```q\n1\n```\n", "kdbx-only: REASON"),
    ],
)
def test_a_marker_that_says_nothing_runnable_is_refused(text, message):
    with pytest.raises(docex.MarkerError, match=message):
        docex.blocks_in(text, DOC)


def test_a_transcript_checks_single_line_output_and_only_runs_the_rest():
    body = "q)1+1\n2\nq)([] a:1 2)\na\n-\n1\n2\nq)`x set 3\n"
    assert docex.transcript_q(body) == (
        '.docex.check["1+1";"2"];\n.docex.check["([] a:1 2)";""];\n.docex.check["`x set 3";""];\n'
    )


def test_quotes_and_backslashes_survive_into_the_q_string():
    assert docex.transcript_q('q)"a\\\\b"\n') == '.docex.check["\\"a\\\\\\\\b\\"";""];\n'


def test_a_document_is_one_session_in_order_with_the_etl_stack_when_named(tmp_path):
    doc = tmp_path / "docs" / "g.md"
    doc.parent.mkdir()
    doc.write_text(
        "<!-- q-example: run -->\n```q\nx:1\n```\n\n"
        "<!-- q-example: run -->\n```q\n.qetl.cfg.audit.watch[`o;`.a.b]\n```\n"
        "<!-- q-example: run kdbx-only: no PeachQ -->\n```q\ny:2\n```\n"
    )
    out = tmp_path / "out"
    out.mkdir()
    sessions, skipped = docex.write_sessions(tmp_path, out, "peachq")
    assert [b.line for b in skipped] == [11]
    (session,) = sessions
    text = session.read_text()
    assert "\\l src/etl/init.q" in text, "a block names .qetl, so the ETL stack loads"
    assert text.index("docs/g.md:2") < text.index("docs/g.md:7"), "document order"
    assert text.rstrip().endswith("exit 0")
    kdbx, _ = docex.write_sessions(tmp_path, tmp_path / "out", "kdbx")
    assert "docs/g.md:11" in kdbx[0].read_text(), "KDB-X runs the kdbx-only block"
