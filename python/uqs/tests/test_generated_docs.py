"""Guards on `scripts/generate/generate_operational_docs.py`.

The decision those questions record: operational docs are generated at
build, committed so a clone has them, and checked in CI — and where the
source and the document disagree, **the source is right by definition**.

These tests exist because the document is worthless if nothing notices it
going stale, and that is not hypothetical here: the hand-written
`docs/integrations/torq/README.md` listed uqf's processes in prose and had already lost
`tap1`, which had been in the registry since the tap pipeline landed. Nobody
noticed, because prose cannot fail.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
GENERATOR = REPO / "scripts" / "generate" / "generate_operational_docs.py"
GENERATED = REPO / "docs" / "integrations" / "torq" / "processes.md"

_spec = importlib.util.spec_from_file_location("gen_ops_docs", GENERATOR)
assert _spec and _spec.loader
gen = importlib.util.module_from_spec(_spec)
sys.modules["gen_ops_docs"] = gen
_spec.loader.exec_module(gen)

from uqs.model.pipelines import PIPELINE_OFFSETS  # noqa: E402
from uqs.model.registry import PIPELINES  # noqa: E402


def test_the_generator_and_its_output_both_exist():
    """A missing generator or output would make every check below vacuous."""
    assert GENERATOR.is_file()
    assert GENERATED.is_file()


def test_the_committed_document_matches_the_registry():
    """The CI-facing check, run here too so a commit cannot land stale.

    `--check` regenerates in memory and diffs; this asserts the exit code
    rather than re-deriving the comparison, so the test and CI agree by
    construction rather than by coincidence.
    """
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    assert result.returncode == 0, result.stderr


def test_check_fails_on_a_stale_document():
    """A checker nobody has seen fail might be comparing nothing.

    Perturbs the committed file, requires `--check` to exit non-zero and say
    so, then restores it byte-for-byte.
    """
    original = GENERATED.read_text()
    try:
        GENERATED.write_text(original.replace("# uqf stack processes", "# Edited by hand", 1))
        result = subprocess.run(
            [sys.executable, str(GENERATOR), "--check"],
            capture_output=True,
            text=True,
            cwd=REPO,
        )
        assert result.returncode == 1
        assert "stale" in result.stderr
    finally:
        GENERATED.write_text(original)
    assert GENERATED.read_text() == original


def test_every_pipeline_appears_in_the_document():
    """The failure that motivated this: `tap1` was missing from the prose.

    Generated output cannot omit a pipeline, but this asserts the property
    directly rather than trusting the loop — if someone adds a filter to the
    generator, this is what catches it.
    """
    text = GENERATED.read_text()
    missing = [p.procname for p in PIPELINES if f"`{p.procname}`" not in text]
    assert not missing, f"generated document omits {missing}"


def test_the_ports_shown_are_the_resolved_ones():
    """`Pipeline.offset` is None for every auto-allocated pipeline.

    Only `fxfeed1` pins an offset; the rest are allocated contiguously and
    live in `PIPELINE_OFFSETS`. Reading the raw field produced a TypeError
    comparing None to int, and a laxer version of that bug would have
    printed the base port for eight of the nine processes.
    """
    text = GENERATED.read_text()
    for name, offset in PIPELINE_OFFSETS.items():
        port = gen.DEFAULT_BASE_PORT + offset
        row = next((ln for ln in text.splitlines() if ln.startswith(f"| `{name}` |")), None)
        assert row, f"no row for {name}"
        assert str(port) in row, f"{name} should show port {port}, row was: {row}"


def test_the_document_carries_a_do_not_edit_banner():
    """Without it the next person edits the output and loses the edit."""
    assert "DO NOT EDIT" in GENERATED.read_text().splitlines()[0]


def test_the_generator_refuses_when_the_edges_disagree(monkeypatch):
    """A diagram from a wrong declaration is worse than a stale one.

    It looks authoritative. So the generator refuses rather than rendering
    unverified edges — verified by making the edge check report a problem.
    """
    monkeypatch.setattr(
        gen, "verify_pipeline_edges", lambda _dir, _pipelines: ["cross1: declares nonsense"]
    )
    try:
        gen.render()
    except SystemExit as exc:
        assert "refusing to generate" in str(exc)
        assert "cross1" in str(exc)
    else:
        raise AssertionError("render() should refuse when the edge check fails")


def test_the_vendored_count_is_read_not_remembered():
    """`docs/integrations/torq/README.md` says "the vendored 14-process stack" in prose.

    The real count is whatever `process.csv` holds, and a number in prose is
    a number nobody re-counts. This asserts the generator reports the read
    value rather than a literal.
    """
    vendored = gen._vendored_procnames()
    text = GENERATED.read_text()
    assert f"**{len(vendored)} vendored processes**" in text
    ours = {p.procname for p in PIPELINES}
    assert not (set(vendored) & ours), "uqf's own processes must not be counted as vendored"


def test_the_prose_architecture_doc_is_consistent_with_the_registry():
    """`docs/integrations/torq/README.md` is authored — diagrams and explanation — but the
    process names in it are facts, and facts in prose go stale.

    This is the check that would have caught the missing `tap1`. It is
    deliberately here rather than in the generator: the README stays
    hand-written (which of the stack's parts its three d2 diagrams show is a
    judgement), so the right gate is one that verifies its facts, not one that
    overwrites its wording.
    """
    readme = (REPO / "docs" / "integrations" / "torq" / "README.md").read_text()
    missing = [p.procname for p in PIPELINES if not re.search(rf"\b{p.procname}\b", readme)]
    assert not missing, (
        f"docs/integrations/torq/README.md does not mention {missing}; it is "
        f"authored prose, so add "
        f"them there by hand — or if the diagram deliberately omits a process, say so "
        f"in the text and this test will still fail, which is the prompt to reconsider"
    )
