"""What the q-running tests under tests/qrun/ promise about running q (#1808).

They replaced scripts/test.py's q-scripts, q-docs and q-docs-peachq lanes, and
these hold what the lanes guaranteed and what moving them must not lose: a q
error fails even when q exits 0, a hung script is stopped with everything it
started, an interpreter a gate requires fails the gate when it is missing
rather than skipping it, and collecting the suite runs no q and changes
nothing in the checkout.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from qrun_support import Q_ERROR, QRun, q_env, run_q

REPO = Path(__file__).resolve().parents[2]


def _script(tmp_path: Path, text: str) -> Path:
    script = tmp_path / "s.q"
    script.write_text(text)
    return script


# --------------------------------------------------------- telling an error


@pytest.mark.parametrize(
    "line",
    ["'2026.10.30T13:56:52.808 type", "'type", "'length", "'boom"],
)
def test_q_s_error_display_is_an_error(line: str) -> None:
    assert Q_ERROR.match(line)


@pytest.mark.parametrize("line", ["ok docs/x.md: 1 block(s)", "", "  'not at the start", "'"])
def test_other_output_is_not(line: str) -> None:
    assert not Q_ERROR.match(line)


def test_an_error_fails_a_run_that_exited_zero() -> None:
    run = QRun(("q", "s.q"), 0, "loading\n'2026.10.30T13:56:52.808 type\n", 10)
    assert run.failure() == "exit 0, but q reported an error: '2026.10.30T13:56:52.808 type"


def test_a_clean_zero_exit_passes_and_a_non_zero_one_fails() -> None:
    assert QRun(("q",), 0, "fine\n", 10).failure() is None
    assert QRun(("q",), 1, "", 10).failure() == "exit 1"
    assert QRun(("q",), None, "", 10).failure() == "still running after 10s - stopped"


@pytest.mark.kdbx
def test_a_real_q_error_before_an_exit_zero_fails(kdbx: Path, tmp_path: Path) -> None:
    """The case an exit code alone misses: a timer tick that errors, then a
    script that exits 0 by its own `exit`. KDB-X: PeachQ prints no error from
    a timer at all, so there is nothing to tell it by."""
    script = _script(tmp_path, '.z.ts:{system"t 0"; .z.ts:{exit 0}; system"t 50"; 1+`a};\n\\t 50\n')
    run = run_q(kdbx, script, cwd=tmp_path, env=q_env(kdbx), timeout=60, stdin_open=True)
    assert run.returncode == 0, run.output
    assert (run.failure() or "").startswith("exit 0, but q reported an error: '"), run.output


# --------------------------------------------------------- stopping a hang


def test_a_script_past_its_timeout_is_stopped_with_what_it_started(
    qcmd: Path, tmp_path: Path
) -> None:
    """q, and the process it is waiting on, both gone once the timeout stops
    the run."""
    pids = tmp_path / "pids"
    script = _script(
        tmp_path,
        f'p:hsym `$"{pids}";\n'
        "p 0: enlist string .z.i;\n"
        f'system "echo $$ >> {pids}; exec sleep 300";\n',
    )
    alive: list[int] = []
    try:
        started = time.monotonic()
        run = run_q(qcmd, script, cwd=tmp_path, env=q_env(qcmd), timeout=3, stdin_open=True)
        assert time.monotonic() - started < 30
        assert run.failure() == "still running after 3s - stopped", run.output
        alive = [int(p) for p in pids.read_text().split()]
        assert len(alive) == 2, pids.read_text()
        deadline = time.monotonic() + 10
        while alive and time.monotonic() < deadline:
            alive = [p for p in alive if _alive(p)]
            time.sleep(0.1)
        assert not alive, f"left running: {alive}"
    finally:
        for pid in alive:  # this test's own processes, should it have failed
            os.kill(pid, signal.SIGKILL)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie is gone for every purpose here, but still answers kill(0).
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return bool(state.stdout.strip()) and not state.stdout.strip().startswith("Z")


def test_a_script_without_exit_ends_at_end_of_input(qcmd: Path, tmp_path: Path) -> None:
    run = run_q(qcmd, _script(tmp_path, '-1 "done";\n'), cwd=tmp_path, env=q_env(qcmd), timeout=60)
    assert run.failure() is None, run.output
    assert "done" in run.output


# ------------------------------------------------- prerequisites and collection


def _pytest(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """pytest on tests/qrun alone - never the repository's whole suite."""
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-p", "no:randomly",
         "-q", "-rs", *args],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=300, check=False,
    )  # fmt: skip


def _no_q_env(tmp_path: Path) -> dict[str, str]:
    """An environment whose QCMD names no runnable file, and whose PATH has no q."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    env["QCMD"] = str(tmp_path / "no-such-q")
    env["PATH"] = os.pathsep.join(
        d for d in env.get("PATH", "").split(os.pathsep) if not (Path(d) / "q").exists()
    )
    return env


ONE_EXAMPLE = "tests/qrun/test_worked_examples.py::test_the_worked_example_runs[scenario_example]"


def test_without_kdbx_an_example_skips_saying_why(tmp_path: Path) -> None:
    done = _pytest(ONE_EXAMPLE, env=_no_q_env(tmp_path))
    assert done.returncode == 0, done.stdout + done.stderr
    assert "1 skipped" in done.stdout and "no q interpreter" in done.stdout


def test_a_gate_that_requires_kdbx_fails_without_it(tmp_path: Path) -> None:
    done = _pytest(ONE_EXAMPLE, "--require-q", "kdbx", env=_no_q_env(tmp_path))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "1 error" in done.stdout and "no q interpreter" in done.stdout


def test_collecting_runs_no_q_and_changes_nothing(tmp_path: Path) -> None:
    """A QCMD that records every start: collecting every q-running test must
    leave it unstarted, and leave `git status` as it found it."""
    marker = tmp_path / "started"
    fake = tmp_path / "q"
    fake.write_text(f'#!/bin/sh\necho "$@" >> {marker}\necho kdbx\n')
    fake.chmod(0o755)
    env = {**_no_q_env(tmp_path), "QCMD": str(fake)}
    status = ["git", "status", "--porcelain", "--untracked-files=all"]
    before = subprocess.run(status, cwd=REPO, capture_output=True, text=True, check=True).stdout
    done = _pytest("--collect-only", "tests/qrun", "python/uqs/tests/test_peachq_pipelines.py",
                   env=env)  # fmt: skip
    after = subprocess.run(status, cwd=REPO, capture_output=True, text=True, check=True).stdout
    assert done.returncode == 0, done.stdout + done.stderr
    assert "test_the_worked_example_runs" in done.stdout
    assert not marker.exists(), f"collection started q: {marker.read_text()}"
    assert after == before


# ------------------------------------------------- one owner per test (#891)


def _hook(config: str, hook_id: str) -> str:
    return config.split(f"- id: {hook_id}\n", 1)[1].split("- id:", 1)[0]


def test_each_q_hook_runs_its_tests_through_pytest_and_no_lane_runs_them_too() -> None:
    """The worked examples and the doc blocks have one runner, pytest: the q
    hooks name the modules, on the interpreter each requires, and the
    python-tests hook leaves the q_backend tests to them."""
    config = (REPO / ".pre-commit-config.yaml").read_text()
    q_tests = _hook(config, "q-tests")
    assert "--require-q kdbx tests/qrun/test_worked_examples.py" in q_tests
    assert "q-scripts" not in q_tests.replace("q-scripts lane", "")
    assert "-m kdbx --require-q kdbx tests/qrun/test_doc_blocks.py" in _hook(config, "q-docs")
    assert "-m peachq --require-q peachq tests/qrun/test_doc_blocks.py" in _hook(
        config, "q-docs-peachq"
    )
    hook = (REPO / "scripts/dev/python_tests_hook.py").read_text()
    assert 'NOT_Q_HOOKS = ["-m", "not q_backend"]' in hook
    assert hook.count("*NOT_Q_HOOKS]") == 2, "both modes leave them out"
    test_py = (REPO / "scripts/test.py").read_text()
    for gone in ("lane_q_scripts", "lane_q_docs", "scripts/examples", "doc_examples"):
        assert gone not in test_py


def test_nothing_else_runs_the_worked_examples() -> None:
    """The examples' directory is globbed in one test module, as #891 left it."""
    pattern = r"(examples\.glob|EXAMPLES\.glob|\"examples\"\)\.glob)"
    users = subprocess.run(
        ["git", "grep", "--untracked", "-l", "-E", pattern, "--", "*.py"],
        cwd=REPO, capture_output=True, text=True, check=False,
    ).stdout.split()  # fmt: skip
    assert users == ["tests/qrun/test_worked_examples.py"]
