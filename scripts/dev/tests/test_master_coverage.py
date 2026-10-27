"""scripts/dev/master_coverage.py: the whole-suite coverage run on master (#1719)."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


mc = _load("master_coverage", "scripts/dev/master_coverage.py")
attest = _load("attest_kdbx_for_tests", "scripts/dev/attest_kdbx.py")


def _fake_lane(monkeypatch, code: int, failures: str = "", map_text: str | None = "m\n"):
    """scripts/test.py q-coverage exiting `code`, having written the outcomes
    and, unless map_text is None, the coverage map."""
    seen = {}

    def fake(argv, cwd, env, check):
        seen["argv"] = argv
        Path(env["UQF_TEST_RESULTS"]).write_text(".qt.a\ttest_one\n")
        Path(env["UQF_TEST_FAILURES"]).write_text(failures + "\n")
        if map_text is not None:
            Path(argv[argv.index("-map") + 1]).write_text(map_text)
        return subprocess.CompletedProcess(argv, code)

    monkeypatch.setattr(mc, "_attest", lambda tree: attest)
    monkeypatch.setattr(mc.subprocess, "run", fake)
    return seen


def test_the_whole_suite_runs_with_no_selection_and_writes_the_map(monkeypatch, tmp_path):
    seen = _fake_lane(monkeypatch, 0)
    code, fresh = mc.run(tmp_path, tmp_path)
    assert code == 0 and fresh.read_text() == "m\n"
    # no -files, no -modules: every function judged, every suite run
    assert seen["argv"][1:] == ["scripts/test.py", "q-coverage", "-map", str(fresh)]


def test_a_coverage_failure_fails_it(monkeypatch, tmp_path, capsys):
    _fake_lane(monkeypatch, 1)
    assert mc.run(tmp_path, tmp_path)[0] == 1
    assert "q-coverage failed" in capsys.readouterr().err


def test_a_test_that_failed_in_the_run_fails_it(monkeypatch, tmp_path, capsys):
    _fake_lane(monkeypatch, 0, ".qt.a.test_one\tfail\tx")
    assert mc.run(tmp_path, tmp_path)[0] == 1
    assert "q-unit failed" in capsys.readouterr().err


def test_a_ref_that_is_no_commit_is_refused(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(mc, "state_dir", lambda root=REPO: tmp_path / "state")
    assert mc.main(["--no-fetch", "--ref", "no-such-ref-1719"]) == 2
    assert "no commit no-such-ref-1719" in capsys.readouterr().err


def test_map_drift_counts_the_rows_either_map_lacks(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_text("# header\nsrc/a.q\t.a.f\tt.q\nsrc/a.q\t.a.g\tt.q\n")
    b.write_text("# other header\nsrc/a.q\t.a.f\tt.q\nsrc/a.q\t.a.h\tt.q\n")
    assert mc.map_drift(a, b) == 2
    assert mc.map_drift(a, a) == 0


def test_the_status_line_says_when_it_never_ran(monkeypatch, tmp_path):
    monkeypatch.setattr(mc, "state_dir", lambda root=REPO: tmp_path)
    assert "has not run on this machine" in mc.status_line()


def test_the_status_line_reports_the_last_verdict_and_a_stale_map(monkeypatch, tmp_path):
    monkeypatch.setattr(mc, "state_dir", lambda root=REPO: tmp_path)
    now = dt.datetime(2026, 10, 27, 12, tzinfo=dt.UTC)
    last = {"sha": "a" * 40, "verdict": "passed", "finished": "2026-10-27T09:00:00+00:00"}
    (tmp_path / "last.json").write_text(json.dumps(last))
    assert (
        mc.status_line(now=now)
        == f"the whole-suite coverage run on master {'a' * 12} 3h ago: passed"
    )
    (tmp_path / "last.json").write_text(json.dumps({**last, "verdict": "FAILED", "map_drift": 4}))
    line = mc.status_line(now=now)
    assert "FAILED - see" in line and "fix master" in line and "in 4 line(s)" in line


def test_if_new_skips_a_tip_already_judged(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(mc, "state_dir", lambda root=REPO: tmp_path)
    head = mc.git("rev-parse", "HEAD").stdout.strip()
    (tmp_path / "last.json").write_text(json.dumps({"sha": head, "verdict": "passed"}))
    monkeypatch.setattr(mc, "run", lambda *a: (_ for _ in ()).throw(AssertionError("ran")))
    assert mc.main(["--no-fetch", "--ref", head, "--if-new"]) == 0
    assert "already judged: passed" in capsys.readouterr().out
