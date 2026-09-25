"""Tests for `scripts/gates/check_env_reference.py`'s .env.example check.

The gate had no tests at all, which for a checker means nobody has seen it
fail - the repository's own recurring failure mode (a hook scoped to nothing,
a drift test that skipped every case). So each branch of the new check gets a
case it must flag and one it must not, and the flag cases are the three
themes the file's header says must stay out: a secret, a per-run argument and
a key nothing reads.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "check_env_reference",
    Path(__file__).resolve().parents[3] / "scripts" / "gates" / "check_env_reference.py",
)
assert _SPEC and _SPEC.loader
cer = importlib.util.module_from_spec(_SPEC)
sys.modules["check_env_reference"] = cer
_SPEC.loader.exec_module(cer)


def test_only_the_dotenv_reader_s_variables_count_as_read_from_dotenv():
    """`.qdata.cfg` is the one reader with a file fallback. The check must
    rest on that pattern alone - a variable read by os.environ.get or getenv
    is NOT read from .env, however documented it is."""
    names = cer.dotenv_read_names()
    assert "DATABENTO_DATA_DIR" in names
    assert "UQF_FRONTEND_GATEWAY_PASSWD" not in names, "read by Settings.from_env, not from .env"
    assert "UQFSTATUSDIR" not in names, "read by getenv in status.q"


def test_the_example_file_parses_keys_and_ignores_comments(monkeypatch, tmp_path):
    example = tmp_path / ".env.example"
    example.write_text("# comment\n\nA=1\n  B = two  \n# C=not a key\nNOEQUALS\n")
    monkeypatch.setattr(cer, "ENV_EXAMPLE", example)
    assert cer.env_example_keys() == {"A", "B"}


def test_a_missing_example_file_is_an_empty_set_not_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(cer, "ENV_EXAMPLE", tmp_path / "absent")
    assert cer.env_example_keys() == set()


def _run_with_example(monkeypatch, tmp_path, text: str) -> tuple[int, str]:
    import io
    from contextlib import redirect_stderr

    example = tmp_path / ".env.example"
    example.write_text(text)
    monkeypatch.setattr(cer, "ENV_EXAMPLE", example)
    monkeypatch.setattr(sys, "argv", ["check_env_reference.py"])
    err = io.StringIO()
    with redirect_stderr(err):
        rc = cer.main()
    return rc, err.getvalue()


def test_the_real_example_file_passes():
    """The committed file is the reference; the gate must accept it."""
    assert cer.env_example_keys() <= cer.dotenv_read_names()


def test_the_variable_that_is_read_from_dotenv_is_accepted(monkeypatch, tmp_path):
    rc, err = _run_with_example(monkeypatch, tmp_path, "DATABENTO_DATA_DIR=/x\n")
    assert rc == 0
    assert "In .env.example" not in err


def test_a_secret_in_the_example_is_refused_naming_its_real_reader(monkeypatch, tmp_path):
    """ETL-07: environment only, no file fallback, because a file fallback is
    how a credential ends up committed. The message says WHERE it is really
    read, so the fix is obvious."""
    rc, err = _run_with_example(
        monkeypatch, tmp_path, "DATABENTO_DATA_DIR=/x\nUQF_FRONTEND_GATEWAY_PASSWD=pw\n"
    )
    assert rc == 1
    assert "UQF_FRONTEND_GATEWAY_PASSWD" in err
    assert "config.py" in err


def test_an_environment_only_variable_in_the_example_is_refused(monkeypatch, tmp_path):
    """A variable read straight from the environment is ignored in .env, so
    the refusal names the file that really reads it - here status.q, for
    wiring build_env sets on every TorQ process."""
    rc, err = _run_with_example(
        monkeypatch, tmp_path, "DATABENTO_DATA_DIR=/x\nUQFSTATUSDIR=/tmp/status\n"
    )
    assert rc == 1
    assert "UQFSTATUSDIR" in err and "status.q" in err


def test_a_key_nothing_reads_is_refused_and_says_so(monkeypatch, tmp_path):
    rc, err = _run_with_example(monkeypatch, tmp_path, "DATABENTO_DATA_DIR=/x\nMYSTERY=1\n")
    assert rc == 1
    assert "MYSTERY" in err and "nothing reads it at all" in err


def test_the_refusal_points_at_the_header_that_explains_the_themes(monkeypatch, tmp_path):
    _, err = _run_with_example(monkeypatch, tmp_path, "DATABENTO_DATA_DIR=/x\nLOG_LEVEL=DEBUG\n")
    assert "Only .qdata.cfg reads .env" in err
    assert "header of .env.example" in err


# ------------------------------- the documented DEFAULT, not just the name


def test_the_documented_gateway_port_is_the_one_the_code_actually_defaults_to():
    """The gate above checks every variable is NAMED in the reference. It
    does not read the Default column, and that column was wrong for a
    release: #235 moved the frontend's default from 6052 (which is rdb1) to
    the gateway's port, and `docs/reference/environment.md` went on saying
    6052 with every check green.

    A name-only gate is the shape of the bug it was written to prevent -
    documentation that exists and is wrong reads as documentation that is
    right. Asserted against `Settings()` rather than the literal, so
    changing the default fails here until the reference is updated.
    """
    from uqf_frontend.config import Settings

    reference = Path(__file__).resolve().parents[3] / "docs" / "reference" / "environment.md"
    # `lstrip` because the markdown formatter indents tables by two spaces,
    # which every renderer still reads as a table and this test did not.
    row = next(
        line.lstrip()
        for line in reference.read_text().splitlines()
        if line.lstrip().startswith("| `UQF_FRONTEND_GATEWAY_PORT`")
    )
    assert str(Settings().port) in row, (
        f"environment.md documents a default the code does not use; "
        f"Settings().port is {Settings().port}, the row reads: {row}"
    )


# ----------------------------------------- the producer side, added with #371


def test_every_variable_build_env_exports_is_consumed_by_something():
    """The check itself, against the real tree.

    `build_env` writes every key into the generated setenv.sh, which every
    process torq.sh starts inherits, so an export nothing reads is pure cost.
    Nothing verified that until now: produced names were exempted from the
    stale direction because *we* never read them back, and the exemption was
    read as "no consumer needed at all". `KDBSTACKID` sat there exporting
    `-stackid <port>` to nobody, while torq.sh and launchprocess.sh built that
    flag themselves out of KDBBASEPORT.
    """
    produced = cer.produced_names()
    assert produced, "parsed no keys out of build_env - the dict literal moved"
    unconsumed = {n for n, files in cer.producer_consumers(produced).items() if not files}
    assert not unconsumed, f"exported by build_env, consumed by nothing: {sorted(unconsumed)}"


def test_the_vendored_process_csv_counts_as_a_consumer():
    """`.csv` is absent from SCANNED_SUFFIXES, and process.csv is where most of
    build_env is consumed - every `${KDBHDB}`-style placeholder in a row. The
    first version of this check scanned the read side's suffixes and reported
    KDBAPPCODE, whose only consumer is that file, as dead.

    A false positive on a live variable is worse than the hole the check was
    added to close, because it teaches the reader to disbelieve the gate."""
    consumers = cer.producer_consumers({"KDBAPPCODE"})
    assert any(f.endswith("process.csv") for f in consumers["KDBAPPCODE"]), consumers["KDBAPPCODE"]


def test_a_variable_used_only_by_the_vendored_test_harness_is_not_consumed():
    """The distinction the check draws is not "does any file mention it" but
    "is the consuming file on a path this repository executes".

    lib/torq/tests/**/run.sh is the vendored framework's own harness, which
    this repository never runs, so a hit there must not keep a variable alive.
    KDBTESTS is the control: it appears all over that harness AND in
    lib/torq/torq.q, which every process loads, so it stays consumed."""
    assert "lib/torq/tests/" in cer.NON_CONSUMERS
    files = cer.producer_consumers({"KDBTESTS"})["KDBTESTS"]
    assert files, "KDBTESTS is read by lib/torq/torq.q and must count as consumed"
    assert not any("lib/torq/tests/" in f for f in files), files


def test_the_mac_launcher_is_not_a_consumer_because_it_assigns_the_variable():
    """start_torq_demo_mac.sh is excluded for a stronger reason than disuse: it
    ASSIGNS the variables it reads (`KDBSTACKID="-stackid ${KDBBASEPORT}"`), so
    a hit there could never show that OUR exported value is wanted. Keeping it
    in scope would have made KDBSTACKID look live for ever."""
    assert any("start_torq_demo_mac" in skip for skip in cer.NON_CONSUMERS)
