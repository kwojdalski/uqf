"""One interpreter variable: QCMD, and the binary asked which q it is (#1753).

PeachQ was dropped once because it was found by a fallback nobody chose, so a
suite could pass on it and read as a KDB-X pass. Nothing falls back to it now:
QCMD naming a PeachQ binary is the choice, and the binary - not a variable
beside it - says which q it is. These hold that, the one PeachQ lookup the
PeachQ tests share,
the one warning an unpinned PeachQ gets, and the refusal of every variable
that chose an interpreter before.

The binary is a stand-in: a shell script answering the identity probe the way
KDB-X or PeachQ would, so no q is needed.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from uqs import interpreter
from uqs.interpreter import (
    REMOVED_ENV,
    identify,
    interpreter_status,
    peachq_binary,
    q_command,
    q_impl,
)
from uqs.paths import UqsError
from uqs.stack import listing

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
#: scripts/peachq.py, which runs under a bare python3 and cannot import uqs.
_spec = importlib.util.spec_from_file_location("uqf_peachq_interp", SCRIPTS / "peachq.py")
assert _spec and _spec.loader
peachq_script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(peachq_script)

PIN = json.loads((SCRIPTS / "peachq.json").read_text())["commit"]


def _fake_q(tmp_path: Path, answer: str, impl: str | None = None) -> Path:
    """An executable answering the identity probe as `impl` (`answer`, when
    that is an implementation, else kdbx) and every other script with
    `answer`. Each run is counted in <name>.runs."""
    impl = impl or (answer if answer in interpreter.Q_IMPLS else "kdbx")
    q = tmp_path / f"q_{answer}_{impl}"
    q.write_text(
        "#!/bin/sh\n"
        f'echo run >> "{q}.runs"\n'
        '/usr/bin/grep -q load_natives "$1" 2>/dev/null \\\n'
        f"  && echo '{impl}' || echo '{answer}'\n"
    )
    q.chmod(0o755)
    return q


def _pinned(q: Path) -> Path:
    """`q` with a cache entry's manifest beside it, recording the pin."""
    (q.parent / "manifest.json").write_text(json.dumps({"commit": PIN}))
    return q


def _runs(q: Path) -> int:
    runs = Path(f"{q}.runs")
    return len(runs.read_text().splitlines()) if runs.exists() else 0


def _env(**kw: str) -> dict[str, str]:
    return {"PATH": os.environ.get("PATH", ""), **kw}


@pytest.fixture
def warnings(monkeypatch) -> list[str]:
    """What uqs.interpreter warned, each binary afresh."""
    seen: list[str] = []

    class Log:
        def warning(self, fmt: str, *args: object) -> None:
            seen.append(fmt.format(*args))

    monkeypatch.setattr(interpreter, "log", Log())
    monkeypatch.setattr(interpreter, "_WARNED", set())
    return seen


# ------------------------------------------------------------- the choice


def test_qcmd_is_the_binary_and_q_on_path_the_default():
    assert q_command(_env()) == "q"
    assert q_command(_env(QCMD="/opt/pq/q")) == "/opt/pq/q"


@pytest.mark.parametrize("answer", ["kdbx", "peachq"])
def test_the_binary_says_which_it_is(tmp_path, answer):
    assert identify(_fake_q(tmp_path, answer), _env()) == answer


def test_the_implementation_is_asked_of_qcmd_and_nothing_else(tmp_path, warnings):
    """QCMD naming PeachQ is enough: no second variable restates it."""
    assert q_impl(_env(QCMD=str(_fake_q(tmp_path, "kdbx")))) == "kdbx"
    assert q_impl(_env(QCMD=str(_pinned(_fake_q(tmp_path, "peachq"))))) == "peachq"


def test_a_bare_q_on_path_that_answers_peachq_is_peachq(tmp_path, warnings):
    q = _fake_q(tmp_path, "peachq")
    (tmp_path / "q").symlink_to(q)
    assert q_impl({"PATH": str(tmp_path)}) == "peachq"


def test_with_no_q_at_all_the_target_is_kdbx(tmp_path):
    assert q_impl({"PATH": str(tmp_path), "QCMD": "no-such-q"}) == "kdbx"


def test_the_binary_is_asked_once_per_process(tmp_path):
    q = _fake_q(tmp_path, "kdbx")
    for _ in range(3):
        assert q_impl(_env(QCMD=str(q))) == "kdbx"
    assert _runs(q) == 1


def test_an_answer_that_is_neither_is_refused_with_what_it_printed(tmp_path):
    q = _fake_q(tmp_path, "banner", impl="no licence found")
    with pytest.raises(UqsError, match=r"could not tell which q .*no licence found"):
        q_impl(_env(QCMD=str(q)))


# ------------------------------------------- the variables it replaced


@pytest.mark.parametrize(
    ("name", "replacement"),
    [
        ("UQF_Q_IMPL", "asked which q it is - set QCMD=<binary> alone"),
        ("UQF_PEACHQ", r"set QCMD=<binary> .*or pass --peachq PATH \(pytest\)"),
        ("UQF_PEACHQ_REASON", "not the pinned build is named in a warning instead"),
    ],
)
def test_each_removed_variable_is_refused_naming_its_replacement(tmp_path, name, replacement):
    message = rf"{name} is no longer read - .*{replacement}.*; unset it .*redeploy"
    env = _env(QCMD=str(_fake_q(tmp_path, "kdbx")), **{name: "peachq"})
    with pytest.raises(UqsError, match=message):
        q_command(env)
    with pytest.raises(UqsError, match=message):
        q_impl(env)
    with pytest.raises(UqsError, match=message):
        peachq_binary(env)
    assert interpreter_status(env).problem is not None


def test_scripts_spell_the_removed_variables_as_uqs_does():
    """scripts/peachq.py cannot import uqs, and restates the refusal and the
    question a binary is asked."""
    assert peachq_script.REMOVED_ENV == REMOVED_ENV
    assert peachq_script.IDENTIFY_SCRIPT == interpreter.IDENTIFY_SCRIPT
    assert peachq_script.removed_variables_problem(
        {"UQF_PEACHQ": "x"}
    ) == interpreter.removed_variables_problem({"UQF_PEACHQ": "x"})


@pytest.mark.parametrize("name", list(REMOVED_ENV))
def test_the_uqs_command_refuses_a_removed_variable_before_any_command(name):
    env: dict[str, str] = {**os.environ, name: "peachq"}
    for other in REMOVED_ENV:
        if other != name:
            env.pop(other, None)
    done = subprocess.run(
        [sys.executable, "-m", "uqs", "--version"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 1
    assert f"{name} is no longer read" in done.stdout + done.stderr


@pytest.mark.parametrize("name", list(REMOVED_ENV))
def test_deploys_preflight_refuses_a_removed_variable_on_the_server(name):
    from uqs.deploy import preflight

    checks = preflight.PREFLIGHT[: preflight.PREFLIGHT.index('echo "user=')]
    env = {"PATH": os.environ["PATH"], name: "x"}
    done = subprocess.run(
        ["bash", "-c", checks], env=env, capture_output=True, text=True, check=False
    )
    assert done.returncode == 1
    assert f"{name} is set on the server, and is no longer read - {REMOVED_ENV[name]}" in (
        done.stderr
    )
    clean = subprocess.run(
        ["bash", "-c", checks + "echo through"],
        env={"PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
        check=False,
    )
    assert clean.stdout == "through\n"


# ------------------------------------------------- an unpinned PeachQ


def test_an_unpinned_peachq_in_qcmd_is_named_once_with_the_pin(tmp_path, warnings):
    q = _fake_q(tmp_path, "peachq")
    for _ in range(3):
        assert q_impl(_env(QCMD=str(q))) == "peachq"
    assert peachq_binary(_env(QCMD=str(q))) == q
    [warning] = warnings
    assert str(q.resolve()) in warning
    assert f"not the pinned build https://github.com/peachq-org/peachq@{PIN}" in warning


def test_the_pinned_peachq_is_used_without_a_word(tmp_path, warnings):
    q = _pinned(_fake_q(tmp_path, "peachq"))
    assert q_impl(_env(QCMD=str(q))) == "peachq"
    assert peachq_binary(_env(QCMD=str(q))) == q
    assert warnings == []


# --------------------------------------------- the one PeachQ lookup


@pytest.fixture
def resolver(monkeypatch, tmp_path) -> Path:
    """scripts/peachq.py's resolve, answering the pinned build as if cached,
    for uqs."""
    (tmp_path / "cache").mkdir()
    cached = _pinned(_fake_q(tmp_path / "cache", "peachq"))

    def resolve(env=None, **_kw):
        return cached

    monkeypatch.setattr(interpreter.peachq_module(), "resolve", resolve)
    return cached


def _outcome(call) -> str:
    try:
        return str(call())
    except UqsError, SystemExit, peachq_script.PeachQError:
        return "refused"


@pytest.mark.parametrize(
    ("qcmd", "override", "expected"),
    [
        (None, None, "pinned"),
        ("kdbx", None, "pinned"),
        ("peachq", None, "qcmd"),
        ("kdbx", "peachq", "override"),
        ("peachq", "other-peachq", "override"),
        ("kdbx", "kdbx", "refused"),
        ("kdbx", "missing", "refused"),
    ],
)
def test_peachq_binary_chooses_by_the_rule(tmp_path, resolver, warnings, qcmd, override, expected):
    """KDB-X alone: the pinned build. QCMD on PeachQ: QCMD. --peachq: that,
    if it is PeachQ."""
    binaries = {
        "kdbx": _fake_q(tmp_path, "kdbx"),
        "peachq": _fake_q(tmp_path, "peachq"),
        "other-peachq": _fake_q(tmp_path, "other", impl="peachq"),
        "missing": tmp_path / "missing",
    }
    env = {"PATH": str(tmp_path / "empty")}
    if qcmd:
        env["QCMD"] = str(binaries[qcmd])
    chosen = str(binaries[override]) if override else None
    want = {
        "pinned": str(resolver),
        "qcmd": env.get("QCMD"),
        "override": chosen,
        "refused": "refused",
    }[expected]
    assert _outcome(lambda: peachq_binary(env, override=chosen)) == want


def test_a_resolver_refusal_is_a_uqs_error(monkeypatch, tmp_path):
    module = interpreter.peachq_module()

    def unavailable(env=None, **_kw):
        raise module.PeachQError("offline, and the cache is cold")

    monkeypatch.setattr(module, "resolve", unavailable)
    with pytest.raises(UqsError, match="no PeachQ: offline"):
        peachq_binary({"PATH": str(tmp_path)})


# ------------------------------------------- reporting it: uqs list env / summary


def test_status_reports_what_the_binary_says(tmp_path):
    q = _fake_q(tmp_path, "kdbx")
    status = interpreter_status(_env(QCMD=str(q)))
    assert (status.binary, status.actual, status.problem) == (q, "kdbx", None)


def test_status_reports_a_binary_that_will_not_say_and_does_not_refuse(tmp_path):
    q = _fake_q(tmp_path, "banner", impl="banner")
    status = interpreter_status(_env(QCMD=str(q)))
    assert status.actual is None
    assert status.problem is not None
    assert status.problem.startswith(f"could not ask {q}: could not tell which q")


def test_status_with_no_q_is_not_runnable_rather_than_an_error(tmp_path):
    status = interpreter_status({"PATH": str(tmp_path), "QCMD": "no-such-q"})
    assert (status.binary, status.actual) == (None, None)
    assert listing.interpreter_line(status) == "interpreter: not runnable (no q found)"


@pytest.mark.parametrize(
    ("answer", "budget"), [("kdbx", "16 connections"), ("peachq", "no connection cap")]
)
def test_the_summary_line_names_the_interpreter_and_its_budget(
    tmp_path, monkeypatch, warnings, answer, budget
):
    q = _pinned(_fake_q(tmp_path, answer))
    monkeypatch.setenv("QCMD", str(q))
    monkeypatch.delenv("UQS_LICENCE_CONNECTIONS", raising=False)
    assert listing.interpreter_line() == f"interpreter: {answer} ({q}) - {budget}"


# ------------------------------------- can this q load the tree? (#882)


@pytest.mark.parametrize(
    ("impl", "version", "loads"),
    [
        ("kdbx", "5.0", True),
        ("kdbx", "6.1", True),
        ("kdbx", "4.1", False),
        ("kdbx", "4.0", False),
        ("kdbx", "", None),
        ("kdbx", "garbage", None),
        ("peachq", "5.0", False),
    ],
)
def test_one_predicate_answers_whether_a_q_loads_the_tree(impl, version, loads):
    assert interpreter.loads_nested_contexts(impl, version) is loads


def test_the_version_is_asked_of_the_binary(tmp_path):
    interpreter.q_version.cache_clear()
    assert interpreter.q_version(str(_fake_q(tmp_path, "4.1"))) == "4.1"
    assert interpreter.q_version(str(tmp_path / "no_such_q")) == ""


def test_install_sh_draws_the_line_where_the_predicate_does():
    """install.sh is bash, run before uqs exists, so it states the threshold
    itself; this holds the two equal."""
    text = (SCRIPTS.parent / "install.sh").read_text()
    import re

    found = re.findall(r"\(\( \$\{qversion%%\.\*\} < (\d+) \)\)", text)
    assert found == [str(interpreter.NESTED_CONTEXTS_SINCE)]


def _start_paths(monkeypatch, q_tree: str):
    import dataclasses

    from uqs import paths as stack_paths

    paths = stack_paths.default_paths()
    decl = dataclasses.replace(paths.runtime_declaration, q_tree=q_tree)
    monkeypatch.setattr(type(paths), "runtime_declaration", property(lambda _s: decl))
    return paths


@pytest.mark.parametrize(("version", "refused"), [("4.1", True), ("5.0", False), ("", False)])
def test_a_local_start_refuses_a_q_too_old_for_the_tree_as_written(
    tmp_path, monkeypatch, version, refused
):
    from uqs.stack import qtree

    interpreter.q_version.cache_clear()
    q = _fake_q(tmp_path, version or "silent")
    if not version:  # asked which q it is, it answers; asked its version, nothing
        q.write_text('#!/bin/sh\n/usr/bin/grep -q load_natives "$1" && echo kdbx\nexit 0\n')
    env = _env(QCMD=str(q))
    paths = _start_paths(monkeypatch, "source")
    if refused:
        with pytest.raises(
            UqsError, match=rf"kdb\+ {version}, which has no nested contexts.*flattened"
        ):
            qtree.refuse_unloadable(paths, env)
    else:
        qtree.refuse_unloadable(paths, env)


def test_neither_a_flattened_tree_nor_a_peachq_runtime_is_asked(tmp_path, monkeypatch):
    from uqs.stack import qtree

    interpreter.q_version.cache_clear()
    old = _fake_q(tmp_path, "4.0")
    qtree.refuse_unloadable(_start_paths(monkeypatch, "flattened"), _env(QCMD=str(old)))
    peachq = _fake_q(tmp_path, "4.0", impl="peachq")
    qtree.refuse_unloadable(_start_paths(monkeypatch, "source"), _env(QCMD=str(peachq)))
