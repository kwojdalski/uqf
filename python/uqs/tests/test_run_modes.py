"""The read-only / executing split of q's run modes is defined once (#1839).

`--wait`, the PeachQ capability check, the receiver path and the attached
backfill each branch on whether a mode opens a source and leaves a status
file. They read uqs.stack.run_modes; this holds that split to q's own list, so
a mode q adds fails here until someone decides which half it belongs in -
rather than falling through to whichever branch each caller happens to take.
"""

from __future__ import annotations

from uqs.generated import q_facts
from uqs.stack import run_modes


def _cli(mode: str) -> str:
    """q's spelling of a mode as the CLI takes it: dry_run is dry-run."""
    return mode.replace("_", "-")


def test_read_only_and_execution_modes_partition_qs_run_modes():
    read_only = set(run_modes.READ_ONLY_MODES)
    executing = set(run_modes.EXECUTION_MODES)
    assert not read_only & executing, read_only & executing
    unclassified = {_cli(m) for m in q_facts.RUN_MODES} - (read_only | executing)
    assert not unclassified, f"q run modes in neither half of the split: {sorted(unclassified)}"
    assert read_only | executing == {_cli(m) for m in q_facts.RUN_MODES}
    # each is a CLI spelling of exactly one q mode, so neither tuple repeats one
    assert len(run_modes.READ_ONLY_MODES) == len(read_only)
    assert len(run_modes.EXECUTION_MODES) == len(executing)


def test_the_split_is_the_one_the_callers_rely_on():
    """validate and plan open nothing and write nothing; dry-run and run start a runner."""
    assert set(run_modes.READ_ONLY_MODES) == {"validate", "plan"}
    assert set(run_modes.EXECUTION_MODES) == {"dry-run", "run"}


def test_modes_are_qs_in_qs_order():
    assert run_modes.MODES == tuple(_cli(m) for m in q_facts.RUN_MODES)
