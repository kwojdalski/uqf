"""Tests for `scripts/check_doc_references.py` - that it FIRES, and that it
stays quiet on the things it must not flag.

The must-not-flag half is the important one here. A documentation gate that
cries wolf gets excluded file by file until it checks nothing, and this one
has four ways to be wrong about a reference that is perfectly fine: a q
projection passes fewer arguments than the function takes and is legal; a
niladic call is `f[]` and passes zero, not one; `src/integrations/data.q`
keeps camelCase names deliberately; and namespaces this tree does not own
(`.Q`, `.servers`, `.z`) are not ours to verify.

Each of those is a case below, and each corresponds to something the first
draft got wrong - the camelCase one reported `.qdata.databentoDir`, a
function that does exist, as missing.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]

# scripts/ is not a package, so the checker is loaded by path - the same
# idiom test_check_q_traps.py uses, and for the same reason.
_SPEC = importlib.util.spec_from_file_location(
    "check_doc_references", REPO / "scripts" / "check_doc_references.py"
)
assert _SPEC and _SPEC.loader
cdr = importlib.util.module_from_spec(_SPEC)
sys.modules["check_doc_references"] = cdr
_SPEC.loader.exec_module(cdr)


@pytest.fixture
def scan(tmp_path, monkeypatch):
    """Run the checker over one markdown file of our choosing."""

    def run(markdown: str):
        (tmp_path / "doc.md").write_text(markdown)
        monkeypatch.setattr(cdr, "DOC_ROOTS", (tmp_path,))
        monkeypatch.setattr(cdr, "REPO", tmp_path)
        return cdr.check()

    return run


# --------------------------------------------------------------- it fires


def test_a_renamed_function_is_reported(scan):
    # The failure this gate exists for: prose left behind by a rename. Two
    # of these were in the tree on the gate's first run.
    problems, _ = scan("see `.qcov.stage_coverage` for details\n")
    assert len(problems) == 1
    assert "stage_coverage" in problems[0]


def test_too_many_arguments_is_reported(scan):
    problems, _ = scan("call `.qcov.require_interval[a;b;c]`\n")
    assert len(problems) == 1
    assert "would throw 'rank" in problems[0]


def test_the_line_number_points_at_the_reference(scan):
    problems, _ = scan("first\nsecond\n`.qcov.nonexistent_name`\n")
    assert ":3:" in problems[0]


# ----------------------------------------------------- it stays quiet


def test_a_correct_call_is_not_reported(scan):
    problems, _ = scan("`.qcov.stage_completion[ds;v;from;to;rows]`\n")
    assert problems == []


def test_a_projection_is_not_reported(scan):
    # Fewer arguments than the rank is a partially applied function, which is
    # ordinary q and a legitimate thing to write in a document.
    problems, _ = scan("`.qcov.require_interval[from]`\n")
    assert problems == []


def test_a_niladic_call_is_not_reported(scan):
    # `f[]` passes zero arguments, not one empty argument. Counting the empty
    # body as an argument would flag every niladic call in the docs.
    problems, _ = scan("`.qcov.ledger[]`\n")
    assert problems == []


def test_a_camelcase_name_is_not_reported(scan):
    # src/integrations/data.q keeps its original camelCase deliberately. A
    # lowercase-only name pattern truncated this to `.qdata.databento` and
    # reported a function that exists as missing.
    problems, _ = scan("`.qdata.databentoDir[]`\n")
    assert problems == []


def test_namespaces_this_tree_does_not_own_are_ignored(scan):
    # kdb's and TorQ's surfaces are not ours to verify, and a gate that
    # guesses about someone else's API is one people learn to ignore.
    problems, unverifiable = scan("`.Q.dpft`, `.servers.startup[]`, `.z.p`, `.u.upd`, `.hb.hb`\n")
    assert problems == []
    assert unverifiable == {}


def test_an_unknown_namespace_is_counted_not_flagged(scan):
    # It cannot be confirmed OR denied, so reporting it as missing would be a
    # claim the gate cannot support. It is counted instead, so the blind spot
    # stays visible rather than passing silently.
    problems, unverifiable = scan("`.qpipe.safe_timer[]` and `.qpipe.publish`\n")
    assert problems == []
    assert unverifiable == {".qpipe": 2}


# ------------------------------------------------- the real tree stays clean


def test_the_repository_passes_its_own_gate():
    # The gate is only worth having if it runs green on the tree it guards;
    # a checker committed red teaches everyone to ignore its output.
    problems, _ = cdr.check()
    assert problems == [], "\n".join(problems)


def test_the_gate_actually_reads_documents():
    # The failure mode this repository has hit three times: a check scoped so
    # narrowly it matches almost nothing and passes for that reason. A floor,
    # not an exact count, so adding a document does not edit this test.
    assert len(cdr.doc_files()) >= 20


def test_every_allowed_missing_entry_names_a_real_file():
    # An exemption for a file that no longer exists is an exemption nobody
    # can evaluate, and it would silently start covering nothing.
    for path, _ref in cdr.ALLOWED_MISSING:
        assert (REPO / path).is_file(), f"{path} is exempted but does not exist"


def test_every_allowed_missing_entry_is_still_needed():
    # An exemption that would no longer fire is one to delete: it reads as a
    # known-bad reference when the prose has actually been fixed.
    saved = dict(cdr.ALLOWED_MISSING)
    try:
        cdr.ALLOWED_MISSING.clear()
        problems, _ = cdr.check()
    finally:
        cdr.ALLOWED_MISSING.update(saved)
    still_firing = {p.split(":")[0] + "|" + p.split(" ")[1] for p in problems}
    for path, ref in saved:
        assert f"{path}|{ref}" in still_firing, (
            f"{path} exempts {ref}, but it no longer fires - delete the exemption"
        )
