"""Choosing PeachQ out loud: UQF_Q_IMPL, and the binary asked which it is.

PeachQ was dropped once because it was found by a fallback nobody chose, so a
suite could pass on it and read as a KDB-X pass. These hold the three things
that keep that from coming back: `peachq` needs a QCMD naming the binary, an
unknown implementation is refused, and a binary that is not what was declared
is refused in both directions - by uqs.interpreter and by scripts/test.py,
which restates the rule and must not drift from it.

The binary is a stand-in: a shell script answering the identity probe the way
KDB-X or PeachQ would, so no q is needed.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from uqs import interpreter
from uqs.interpreter import check_identity, identify, interpreter_status, q_command, q_impl
from uqs.paths import UqsError
from uqs.stack import listing

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "test.py"
_spec = importlib.util.spec_from_file_location("uqf_test_runner_interp", SCRIPT)
assert _spec and _spec.loader
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)


def _fake_q(tmp_path: Path, answer: str) -> Path:
    """An executable that answers the identity probe with `answer`."""
    q = tmp_path / f"q_{answer}"
    q.write_text(f"#!/bin/sh\necho '{answer}'\n")
    q.chmod(0o755)
    return q


def _env(**kw: str) -> dict[str, str]:
    return {"PATH": os.environ.get("PATH", ""), **kw}


# ------------------------------------------------------------- the choice


def test_kdbx_is_the_default_and_q_on_path_its_binary():
    assert q_impl(_env()) == "kdbx"
    assert q_command(_env()) == "q"


def test_peachq_needs_qcmd_naming_its_binary():
    with pytest.raises(UqsError, match="needs QCMD set to the PeachQ binary"):
        q_command(_env(UQF_Q_IMPL="peachq"))
    assert q_command(_env(UQF_Q_IMPL="peachq", QCMD="/opt/pq/q")) == "/opt/pq/q"


def test_an_unknown_implementation_is_refused():
    with pytest.raises(UqsError, match="it is one of kdbx, peachq"):
        q_impl(_env(UQF_Q_IMPL="kdb+"))


# ---------------------------------------------------- the binary is asked


@pytest.mark.parametrize("answer", ["kdbx", "peachq"])
def test_the_binary_says_which_it_is(tmp_path, answer):
    assert identify(_fake_q(tmp_path, answer), _env()) == answer


def test_a_peachq_binary_without_the_opt_in_is_refused(tmp_path):
    """The case that got PeachQ dropped: it answers to QCMD, nobody chose it."""
    q = _fake_q(tmp_path, "peachq")
    with pytest.raises(UqsError, match="set UQF_Q_IMPL=peachq to run it knowingly"):
        check_identity(q, _env(QCMD=str(q)))
    assert check_identity(q, _env(QCMD=str(q), UQF_Q_IMPL="peachq")) == "peachq"


def test_a_kdbx_binary_declared_as_peachq_is_refused(tmp_path):
    q = _fake_q(tmp_path, "kdbx")
    with pytest.raises(UqsError, match="point QCMD at the PeachQ binary"):
        check_identity(q, _env(QCMD=str(q), UQF_Q_IMPL="peachq"))


def test_an_answer_that_is_neither_is_refused_not_guessed(tmp_path):
    with pytest.raises(UqsError, match="could not tell which q"):
        identify(_fake_q(tmp_path, "banner"), _env())


# ------------------------------------------------------- scripts/test.py


def test_the_runner_asks_the_same_question():
    """Restated there because a bare python3 cannot import uqs."""
    assert runner.IDENTIFY_SCRIPT == interpreter.IDENTIFY_SCRIPT
    assert runner.Q_IMPL_ENV == interpreter.Q_IMPL_ENV
    assert runner.Q_IMPLS == interpreter.Q_IMPLS


@pytest.mark.parametrize(
    ("answer", "declared", "message"),
    [
        ("peachq", None, "set UQF_Q_IMPL=peachq to run it knowingly"),
        ("kdbx", "peachq", "point QCMD at the PeachQ binary"),
    ],
)
def test_the_runner_refuses_a_mismatch_before_any_lane(tmp_path, answer, declared, message):
    q = _fake_q(tmp_path, answer)
    env = _env(QCMD=str(q), **({"UQF_Q_IMPL": declared} if declared else {}))
    with pytest.raises(SystemExit, match=message):
        runner.check_interpreter(env)


def test_the_runner_refuses_peachq_without_qcmd():
    with pytest.raises(SystemExit, match="needs QCMD set to the PeachQ binary"):
        runner.check_interpreter(_env(UQF_Q_IMPL="peachq"))


@pytest.mark.parametrize("answer", ["kdbx", "peachq"])
def test_the_runner_accepts_what_was_declared(tmp_path, answer):
    q = _fake_q(tmp_path, answer)
    assert runner.check_interpreter(_env(QCMD=str(q), UQF_Q_IMPL=answer)) == answer


def test_the_runner_leaves_a_missing_q_to_the_lanes(tmp_path):
    assert runner.check_interpreter(_env(QCMD=str(tmp_path / "nothing"))) is None


# ------------------------------------------- reporting it: uqs list env / summary


def test_status_reports_a_binary_that_is_what_was_declared(tmp_path):
    q = _fake_q(tmp_path, "kdbx")
    status = interpreter_status(_env(QCMD=str(q)))
    assert (status.declared, status.binary, status.actual, status.problem) == (
        "kdbx",
        q,
        "kdbx",
        None,
    )


def test_status_reports_a_mismatch_in_check_identitys_words_and_does_not_refuse(tmp_path):
    q = _fake_q(tmp_path, "peachq")
    status = interpreter_status(_env(QCMD=str(q)))
    assert status.actual == "peachq"
    hint = "set UQF_Q_IMPL=peachq to run it knowingly"
    assert status.problem == f"{q} is peachq, but UQF_Q_IMPL declares kdbx - {hint}"


def test_status_with_no_q_is_not_runnable_rather_than_an_error(tmp_path):
    status = interpreter_status({"PATH": str(tmp_path), "QCMD": "no-such-q"})
    assert (status.binary, status.actual) == (None, None)
    assert (
        listing.interpreter_line(status) == "interpreter: kdbx declared - not runnable (no q found)"
    )


@pytest.mark.parametrize(
    ("answer", "declared", "budget"),
    [("kdbx", None, "16 connections"), ("peachq", "peachq", "no connection cap")],
)
def test_the_summary_line_names_the_interpreter_and_its_budget(
    tmp_path, monkeypatch, answer, declared, budget
):
    q = _fake_q(tmp_path, answer)
    monkeypatch.setenv("QCMD", str(q))
    monkeypatch.delenv("UQS_LICENCE_CONNECTIONS", raising=False)
    if declared:
        monkeypatch.setenv("UQF_Q_IMPL", declared)
    else:
        monkeypatch.delenv("UQF_Q_IMPL", raising=False)
    assert listing.interpreter_line() == f"interpreter: {answer} ({q}) - {budget}"
