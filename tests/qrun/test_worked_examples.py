"""Every worked example under scripts/examples/, each run as a script in its own q.

The ONE runner of the examples (#891, #1808). They used to be scripts/test.py's
q-scripts lane, and before that a pytest module ran them as well, with its own
glob and its own process handling, so a fix to either never reached the other.
This module took the lane over and the lane is gone: the q-tests hook runs it
(`-m kdbx --require-q`), and a plain `uv run pytest` does too.

These are scripts a reader runs by hand to see the library work end to end,
and until the lane existed NOTHING ran them. That gap was not theoretical: the
first commit to add a scenario found `plant_tables.q` claiming its `quotes`
table was usable by `cross_book_at` "with no reshaping", when `cross_book_at`
refuses it outright for a missing `ts` - a prose claim about two halves of this
tree fitting together, false for as long as it stood.

Each runs in its own process: they load src/init.q, define top-level tables and
insert into them, and a shared process would let one example's rows reach the
next. Each runs from its own directory too, linking to the checkout's entries
(checkout_view): an example writes under `build/` or `output/` relative to where it runs,
and from the checkout two xdist workers - or a test and a reader - would share
one file. KDB-X only: they load src/etl/core/log.q, whose nested `\\d` PeachQ
rejects, so on PeachQ they skip (visibly), as the lane never ran there.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from qrun_support import assert_passed, q_env, run_q

REPO = Path(__file__).resolve().parents[2]
EXAMPLES = REPO / "scripts" / "examples"

#: The slowest example takes well under a second; one still running after this
#: is waiting on something - a timer, a connection - and a hung test is not a
#: useful way to say so.
EXAMPLE_TIMEOUT = 120

#: Examples whose own `exit` is the only way out: a timer replay that races
#: stdin EOF against its next tick (#1338). They get a stdin that never ends
#: and check themselves in .z.exit, so a replay cut short exits non-zero.
STDIN_OPEN_EXAMPLES = frozenset({"timer_replay_example"})


#: What an example writes into, relative to where it runs: never linked, so
#: those writes land in the test's own directory.
WRITTEN = frozenset({"build", "output"})


def checkout_view(work: Path) -> Path:
    """`work`, holding a link to each top-level entry of the checkout but the
    ones an example writes into - so an example reads src/ and lib/ as from the
    root, and writes nothing there."""
    work.mkdir()
    for entry in REPO.iterdir():
        if entry.name not in WRITTEN and not entry.name.startswith("."):
            (work / entry.name).symlink_to(entry, target_is_directory=entry.is_dir())
    return work


def examples() -> list[Path]:
    """Every worked example, by name - read at collection, nothing run."""
    return sorted(EXAMPLES.glob("*.q"))


@pytest.mark.q_backend
@pytest.mark.kdbx
@pytest.mark.parametrize("script", examples(), ids=lambda p: p.stem)
def test_the_worked_example_runs(script: Path, kdbx: Path, tmp_path: Path) -> None:
    work = checkout_view(tmp_path / "work")
    status = tmp_path / "status"
    status.mkdir()
    run = run_q(
        kdbx,
        script,
        cwd=work,
        env=q_env(kdbx, {"UQF_STATUS_DIR": str(status)}),
        timeout=EXAMPLE_TIMEOUT,
        stdin_open=script.stem in STDIN_OPEN_EXAMPLES,
    )
    assert_passed(run, f"scripts/examples/{script.name}")


def test_every_example_is_collected_and_none_is_left_out() -> None:
    """Discovery parity: what runs is exactly what the directory holds."""
    on_disk = {p.name for p in EXAMPLES.iterdir() if p.suffix == ".q"}
    assert on_disk, "scripts/examples/ holds no example"
    assert {p.name for p in examples()} == on_disk
    assert STDIN_OPEN_EXAMPLES <= {Path(n).stem for n in on_disk}
