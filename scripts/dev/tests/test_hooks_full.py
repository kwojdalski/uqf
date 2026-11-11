"""UQF_HOOKS_FULL=1, the switch back to every commit hook in full (#1879).

Full mode must always be one switch away. These hold each targeted hook to
honouring it: python-tests runs the whole suite (test_python_tests_hook.py),
q-tests and q-coverage the whole q suite, and the hooks that run only in full
mode - the docs render, the docs' q blocks on PeachQ - run, and pass
without running otherwise.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
DEV = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, DEV / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return module


hf = _load("hooks_full")
ti = _load("q_test_impact")

HOOKS = {
    h["id"]: h
    for repo in yaml.safe_load((REPO / ".pre-commit-config.yaml").read_text())["repos"]
    for h in repo["hooks"]
}

#: The hooks that run at commit only in full mode: CI runs each on every pull
#: request that can change its result.
FULL_ONLY = ("doc-links", "q-docs-peachq")


@pytest.fixture
def full(monkeypatch):
    monkeypatch.setenv(hf.ENV, "1")
    monkeypatch.delenv(ti.FULL_ENV, raising=False)
    monkeypatch.delenv(ti.DECIDED_ENV, raising=False)
    monkeypatch.delenv(ti.COVERAGE_DECIDED_ENV, raising=False)


def test_the_test_selectors_read_the_same_variable():
    assert hf.ENV == ti.HOOKS_FULL_ENV == "UQF_HOOKS_FULL"


def test_full_mode_runs_the_whole_q_suite_and_its_whole_coverage(full, tmp_path):
    assert ti.plan(root=tmp_path).files is None
    assert ti.hook_line(tmp_path).line == "ALL"
    assert ti.coverage_hook_line(tmp_path).line == "ALL"
    assert "UQF_HOOKS_FULL=1" in ti.plan(root=tmp_path).why


def test_full_mode_overrides_a_decision_handed_over(full, monkeypatch, tmp_path):
    monkeypatch.setenv(ti.DECIDED_ENV, "-files test_a.q")
    monkeypatch.setenv(ti.COVERAGE_DECIDED_ENV, "-files test_a.q -modules src/a.q")
    assert ti.hook_line(tmp_path).files is None
    assert ti.coverage_hook_line(tmp_path).files is None


def test_the_old_q_switch_still_works_alone(monkeypatch, tmp_path):
    monkeypatch.delenv(hf.ENV, raising=False)
    monkeypatch.setenv(ti.FULL_ENV, "1")
    assert ti.plan(root=tmp_path).why == "$UQF_Q_TESTS_FULL=1"


@pytest.mark.parametrize("hook", FULL_ONLY)
def test_a_full_only_hook_is_entered_through_the_switch(hook):
    entry = HOOKS[hook]["entry"]
    assert entry.startswith(f"uv run python scripts/dev/hooks_full.py --hook {hook} -- "), entry


def test_a_full_only_hook_runs_its_command_only_in_full_mode(monkeypatch, tmp_path, capsys):
    marker = tmp_path / "ran"
    cmd = ["--hook", "x", "--", sys.executable, "-c", f"open({str(marker)!r}, 'w')"]
    monkeypatch.delenv(hf.ENV, raising=False)
    assert hf.main(cmd) == 0 and not marker.exists()
    assert "UQF_HOOKS_FULL=1 runs it here" in capsys.readouterr().out
    monkeypatch.setenv(hf.ENV, "1")
    assert hf.main(cmd) == 0 and marker.exists()


def test_a_full_only_hook_fails_when_its_command_does(monkeypatch):
    monkeypatch.setenv(hf.ENV, "1")
    assert hf.main(["--hook", "x", "--", sys.executable, "-c", "raise SystemExit(3)"]) == 3


def test_all_runs_every_hook_over_the_tree_in_full_mode(monkeypatch):
    seen = []

    def run(cmd, env, check):
        seen.append((cmd, env))
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(hf.subprocess, "run", run)
    assert hf.main(["--all"]) == 0
    # the commit hooks, then the push hooks the suites moved to (#1965)
    assert [cmd[-5:] for cmd, _ in seen] == [
        ["pre-commit", "run", "--all-files", "--hook-stage", stage]
        for stage in ("pre-commit", "pre-push")
    ]
    assert all(hf.PRE_COMMIT in cmd and env[hf.ENV] == "1" for cmd, env in seen)


def test_all_fails_when_either_stage_fails(monkeypatch):
    codes = iter([0, 3])
    monkeypatch.setattr(
        hf.subprocess, "run", lambda cmd, env, check: type("R", (), {"returncode": next(codes)})()
    )
    assert hf.main(["--all"]) == 3


def test_a_malformed_call_is_refused():
    assert hf.main(["--hook", "x"]) == 2


def test_python_tests_is_the_selecting_hook():
    assert HOOKS["python-tests"]["entry"] == "uv run python scripts/dev/python_tests_hook.py"
