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
    assert "UQF_BACKFILL_FROM" not in names, "read by getenv in torq_backfill.q"


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


def test_a_per_run_argument_in_the_example_is_refused(monkeypatch, tmp_path):
    """A default range in a file is the guessed range a backfill must never
    have (ETL-02)."""
    rc, err = _run_with_example(
        monkeypatch, tmp_path, "DATABENTO_DATA_DIR=/x\nUQF_BACKFILL_FROM=2026.09.11D00:00\n"
    )
    assert rc == 1
    assert "UQF_BACKFILL_FROM" in err and "torq_backfill.q" in err


def test_a_key_nothing_reads_is_refused_and_says_so(monkeypatch, tmp_path):
    rc, err = _run_with_example(monkeypatch, tmp_path, "DATABENTO_DATA_DIR=/x\nMYSTERY=1\n")
    assert rc == 1
    assert "MYSTERY" in err and "nothing reads it at all" in err


def test_the_refusal_points_at_the_header_that_explains_the_themes(monkeypatch, tmp_path):
    _, err = _run_with_example(monkeypatch, tmp_path, "DATABENTO_DATA_DIR=/x\nLOG_LEVEL=DEBUG\n")
    assert "Only .qdata.cfg reads .env" in err
    assert "header of .env.example" in err
