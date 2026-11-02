"""The live external metadata check, tests/q/smoke_external_metadata.q (#1808).

Opt-in, `-m external`, and deliberately part of no other run: the
deterministic suite proves local behaviour, not that a configured external
service is reachable or compatible, and folding it in would make every run
depend on a remote host being up. scripts/test.py's smoke lane until #1808.

    uv run pytest tests/qrun/test_external_smoke.py -m external \\
        --smoke-targets h1:5010 --smoke-tables trade:sym,price --smoke-timeout-ms 3000

The script reads its sources off its command line as -targets, -tables and
-timeout_ms. With no --smoke-targets this skips: an unconfigured checkout has
nothing to check, which is not a failure (the script itself reports SKIP).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest
from qrun_support import assert_passed, q_env, run_q

REPO = Path(__file__).resolve().parents[2]
SCRIPT = "tests/q/smoke_external_metadata.q"

#: Every connection has its own timeout; this bounds the whole check.
TIMEOUT = 600


def smoke_flags(targets: Sequence[str], tables: Sequence[str], timeout_ms: int | None) -> list[str]:
    """The pytest options as the q script's own flags."""
    flags: list[str] = []
    if targets:
        flags += ["-targets", *targets]
    if tables:
        flags += ["-tables", *tables]
    if timeout_ms is not None:
        flags += ["-timeout_ms", str(timeout_ms)]
    return flags


@pytest.mark.external
def test_the_external_sources_still_have_their_columns(
    qcmd: Path, request: pytest.FixtureRequest
) -> None:
    option = request.config.getoption
    targets = option("--smoke-targets")
    if not targets:
        pytest.skip("no --smoke-targets: nothing external is configured to check")
    flags = smoke_flags(targets, option("--smoke-tables"), option("--smoke-timeout-ms"))
    run = run_q(qcmd, SCRIPT, *flags, cwd=REPO, env=q_env(qcmd), timeout=TIMEOUT)
    assert_passed(run, SCRIPT)


def test_the_options_reach_the_script_as_its_own_flags() -> None:
    assert smoke_flags(["h1:5010", "h2:5011"], ["quote:sym,bid", "trade:sym"], 3000) == [
        "-targets", "h1:5010", "h2:5011", "-tables", "quote:sym,bid", "trade:sym",
        "-timeout_ms", "3000",
    ]  # fmt: skip


def test_with_no_options_it_passes_no_flags() -> None:
    assert smoke_flags([], [], None) == []
