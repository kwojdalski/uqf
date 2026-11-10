"""Our odbc.q against KX's odbc.k, on the same queries (#2508).

src/etl/core/odbc.q replaces `\\l odbc.k` so ODBC can run on PeachQ. This runs
tests/q/odbc_parity.q once per client through scripts/dev/odbc_rosetta.sh (the
DuckDB driver; x86_64 under Rosetta on macOS) and requires identical output:
the whole-day datetime case, timeouts, and the refusals included.

It needs the Rosetta setup (`scripts/dev/odbc_rosetta.sh setup`) and KX's
odbc.k as the reference (`scripts/dev/odbc_rosetta.sh fetch-odbc-k`); without
either it skips and says which. The default lane never needs ODBC.

Timeouts are accepted and pass through, but never EXPIRE: DuckDB's driver does
not interrupt a statement, so no expiry is compared.
"""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ROSETTA = REPO / "scripts" / "dev" / "odbc_rosetta.sh"
SCRIPT = "tests/q/odbc_parity.q"


def _main_root() -> Path:
    """The main checkout's root, where the script builds output/odbc-x86_64."""
    common = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return Path(common).parent


def skip_reason() -> str | None:
    """Why this cannot run here, or None when the ODBC setup is present."""
    if platform.system() != "Darwin":
        return "the parity run is wired to scripts/dev/odbc_rosetta.sh, which is macOS-only"
    prefix = _main_root() / "output" / "odbc-x86_64"
    if (
        not (prefix / "qhome" / "m64" / "odbc.so").is_file()
        or not (prefix / "odbcinst.ini").is_file()
    ):
        return "ODBC under Rosetta is not set up - run scripts/dev/odbc_rosetta.sh setup"
    if not (prefix / "qhome" / "odbc.k").is_file():
        return (
            "KX's odbc.k (the reference) is absent - run scripts/dev/odbc_rosetta.sh fetch-odbc-k"
        )
    return None


def run_client(impl: str) -> str:
    run = subprocess.run(
        [str(ROSETTA), "q", SCRIPT, "-impl", impl, "-q"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert run.returncode == 0, f"-impl {impl} exited {run.returncode}:\n{run.stdout}{run.stderr}"
    return run.stdout


def test_odbc_q_answers_exactly_as_odbc_k_does() -> None:
    why = skip_reason()
    if why:
        pytest.skip(why)
    reference, ours = run_client("k"), run_client("q")
    assert ours == reference
    # a comparison of two empty outputs would also be equal
    assert "eval_whole_day|(1b;+`a`d`whole`part!" in ours
    assert 'eval_unknown_handle|(0b;"invalid")' in ours
