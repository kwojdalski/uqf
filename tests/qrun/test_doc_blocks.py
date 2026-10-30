"""The ```q blocks in docs/ marked `<!-- q-example: ... -->`, one q per document.

scripts/dev/doc_examples.py reads the markers and writes each document's blocks
as one q session, in document order, the way a reader types them - so a guide's
later block may use what an earlier one defined. This module runs them: once
on KDB-X and once on PeachQ, each document a test of its own on each.

Both interpreters are required, and neither result stands in for the other.
KDB-X is the reference; PeachQ runs part of this tree - these blocks, and CI,
where KDB-X cannot run - and is held to it until the two are compatible enough
for one to vouch for both. So the q-docs hook runs `-m kdbx` and the
q-docs-peachq hook `-m peachq`, each with `--require-q`, which fails a run that
cannot have its interpreter rather than skipping it; CI's sharded PeachQ job
runs the PeachQ half. These were scripts/test.py's q-docs and q-docs-peachq
lanes (#1808), and the lanes are gone.

A block correct on KDB-X that PeachQ cannot run is marked `kdbx-only: REASON`:
on PeachQ it is left out of its document's session and named in a warning,
and a document left with nothing to run skips, saying why.
"""

from __future__ import annotations

import importlib.util
import sys
import warnings
from pathlib import Path

import pytest
from qrun_support import assert_passed, q_env, run_q

REPO = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location("doc_examples", REPO / "scripts/dev/doc_examples.py")
assert _spec and _spec.loader
docex = importlib.util.module_from_spec(_spec)
# Registered before running it: its dataclass resolves annotations through
# sys.modules, and an unregistered module is not there.
sys.modules[_spec.name] = docex
_spec.loader.exec_module(docex)

#: A document's session loads src/init.q and the ETL stack at most; one still
#: running after this is waiting on something.
SESSION_TIMEOUT = 300

#: The marked blocks, read at collection: text only, no q.
BLOCKS = docex.all_blocks(REPO)
DOCS = sorted({b.path for b in BLOCKS})

INTERPRETERS = [
    pytest.param("kdbx", marks=pytest.mark.kdbx, id="kdbx"),
    pytest.param("peachq", marks=pytest.mark.peachq, id="peachq"),
]


class KdbxOnlyBlock(UserWarning):
    """A block marked `kdbx-only`, not run on PeachQ."""


@pytest.mark.q_backend
@pytest.mark.parametrize("impl", INTERPRETERS)
@pytest.mark.parametrize("doc", DOCS, ids=str)
def test_the_marked_q_blocks_run(
    doc: Path, impl: str, request: pytest.FixtureRequest, tmp_path: Path
) -> None:
    q: Path = request.getfixturevalue(impl)
    blocks = [b for b in BLOCKS if b.path == doc]
    left_out = [b for b in blocks if b.kdbx_only and impl != "kdbx"]
    for block in left_out:
        warnings.warn(f"not run on {impl}: {block.where} (kdbx-only: {block.kdbx_only})",
                      KdbxOnlyBlock, stacklevel=1)  # fmt: skip
    run = [b for b in blocks if b not in left_out]
    if not run:
        pytest.skip(f"every marked block in {doc} is kdbx-only")
    out = tmp_path / "session"
    out.mkdir()
    session = docex.write_session(run, out)
    status = tmp_path / "status"
    status.mkdir()
    result = run_q(
        q,
        session,
        cwd=REPO,
        env=q_env(q, {"UQF_STATUS_DIR": str(status)}),
        timeout=SESSION_TIMEOUT,
    )
    assert_passed(result, f"the marked q blocks of {doc} on {impl}")
    assert f"ok {doc}: {len(run)} block(s)" in result.output


def test_every_marked_document_is_collected() -> None:
    """Discovery parity: a test per document that has a marked block - the
    same list the q-docs hooks' scope is held to (check_hook_scopes.py)."""
    assert DOCS, "nothing in docs/ is marked to run"
    assert [d.as_posix() for d in DOCS] == docex.marked_docs(REPO)
