"""uqs.stack.envfiles: `.env` and `.envrc` reach the processes uqs starts.

direnv is faked rather than run: a real `direnv allow` would write to the
operator's own approval list, and these tests are about what uqs does with
direnv's answer, not about direnv.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from uqs.stack import envfiles
from uqs.stack.envfiles import load_env_files, parse_dotenv


def fake_direnv(
    monkeypatch: pytest.MonkeyPatch, *, stdout: str = "", returncode: int = 0, stderr: str = ""
) -> list:
    calls: list = []
    monkeypatch.setattr(envfiles.shutil, "which", lambda name: "/usr/bin/direnv")

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    monkeypatch.setattr(envfiles.subprocess, "run", run)
    return calls


def test_dotenv_reads_assignments_and_skips_the_rest():
    text = """
# a comment
PLAIN=value
export EXPORTED=yes
QUOTED="has # inside"
SINGLE='kept as is'
TRAILING=value # a comment
EMPTY=
not an assignment
1BAD=no
"""
    assert parse_dotenv(text) == {
        "PLAIN": "value",
        "EXPORTED": "yes",
        "QUOTED": "has # inside",
        "SINGLE": "kept as is",
        "TRAILING": "value",
        "EMPTY": "",
    }


def test_dotenv_sets_what_the_environment_lacks(tmp_path: Path):
    (tmp_path / ".env").write_text("FROM_FILE=1\n")
    environ: dict[str, str] = {}
    loaded = load_env_files(tmp_path, environ)
    assert environ == {"FROM_FILE": "1"}
    assert loaded.set_from_dotenv == ["FROM_FILE"]


def test_the_environment_wins_over_both_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # `UQF_X=1 uqs start` must mean what it says.
    (tmp_path / ".env").write_text("KEY=from_dotenv\n")
    (tmp_path / ".envrc").write_text("export KEY=from_envrc\n")
    fake_direnv(monkeypatch, stdout=json.dumps({"KEY": "from_envrc"}))
    environ = {"KEY": "from_shell"}
    loaded = load_env_files(tmp_path, environ)
    assert environ == {"KEY": "from_shell"}
    assert loaded.shadowed == ["KEY"]


def test_envrc_wins_over_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / ".env").write_text("KEY=from_dotenv\nONLY_DOTENV=d\n")
    (tmp_path / ".envrc").write_text("export KEY=from_envrc\n")
    fake_direnv(monkeypatch, stdout=json.dumps({"KEY": "from_envrc"}))
    environ: dict[str, str] = {}
    loaded = load_env_files(tmp_path, environ)
    assert environ == {"KEY": "from_envrc", "ONLY_DOTENV": "d"}
    assert loaded.set_from_envrc == ["KEY"]
    assert loaded.set_from_dotenv == ["ONLY_DOTENV"]


def test_envrc_is_evaluated_by_direnv_in_the_repo_root_not_sourced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (tmp_path / ".envrc").write_text("export KEY=x\n")
    calls = fake_direnv(monkeypatch, stdout=json.dumps({"KEY": "x"}))
    load_env_files(tmp_path, {})
    argv, kwargs = calls[0]
    assert argv == ["/usr/bin/direnv", "export", "json"]
    assert kwargs["cwd"] == tmp_path


def test_direnv_bookkeeping_and_unloads_do_not_reach_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # A null is direnv unloading ANOTHER directory's .envrc from the caller's
    # shell: dropped, never turned into an unset.
    (tmp_path / ".envrc").write_text("export KEY=x\n")
    fake_direnv(
        monkeypatch,
        stdout=json.dumps(
            {"KEY": "x", "OTHER_DIRS_VAR": None, "DIRENV_DIFF": "abc", "DIRENV_FILE": "/f"}
        ),
    )
    environ = {"OTHER_DIRS_VAR": "kept"}
    load_env_files(tmp_path, environ)
    assert environ == {"OTHER_DIRS_VAR": "kept", "KEY": "x"}


def test_a_blocked_envrc_is_skipped_and_dotenv_still_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (tmp_path / ".env").write_text("FROM_DOTENV=1\n")
    (tmp_path / ".envrc").write_text("export KEY=x\n")
    fake_direnv(
        monkeypatch,
        stdout=json.dumps({"DIRENV_DIFF": "abc"}),
        returncode=1,
        stderr=(
            "\x1b[31mdirenv: error .envrc is blocked."
            " Run `direnv allow` to approve its content\x1b[0m\n"
        ),
    )
    environ: dict[str, str] = {}
    load_env_files(tmp_path, environ)
    assert environ == {"FROM_DOTENV": "1"}


def test_without_direnv_the_envrc_is_skipped_not_sourced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (tmp_path / ".envrc").write_text("export KEY=x\n")
    monkeypatch.setattr(envfiles.shutil, "which", lambda name: None)

    def must_not_run(*args, **kwargs):
        raise AssertionError("nothing may execute .envrc without direnv's approval check")

    monkeypatch.setattr(envfiles.subprocess, "run", must_not_run)
    environ: dict[str, str] = {}
    load_env_files(tmp_path, environ)
    assert environ == {}


def test_no_files_change_nothing(tmp_path: Path):
    environ = {"A": "1"}
    load_env_files(tmp_path, environ)
    assert environ == {"A": "1"}


def test_a_child_process_inherits_what_was_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # The point of loading into os.environ: every spawn site inherits it,
    # with or without an explicit env= that merges os.environ.
    (tmp_path / ".env").write_text("UQS_ENVFILES_TEST=reached\n")
    monkeypatch.delenv("UQS_ENVFILES_TEST", raising=False)
    import os

    load_env_files(tmp_path, os.environ)
    try:
        out = subprocess.run(
            ["sh", "-c", 'printf %s "$UQS_ENVFILES_TEST"'],
            capture_output=True,
            text=True,
            check=True,
        )
        assert out.stdout == "reached"
    finally:
        os.environ.pop("UQS_ENVFILES_TEST", None)
