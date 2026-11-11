"""The pre-push PeachQ run (#1965): which suite files it picks, and which
failures stop a push. The documented-examples suite joins when an `@eg`
line changes (#1978)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("peachq_push", REPO / "scripts/peachq/pre_push.py")
assert _spec and _spec.loader
pp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pp)

suite = pp._load("full_suite", "scripts/peachq/full_suite.py")
KNOWN_GAP = next(iter(suite.read_gaps()))


def test_a_changed_suite_file_is_picked():
    assert pp.select(["tests/q/test_forwards.q", "README.md"]) == ["test_forwards.q"]


def test_a_changed_module_picks_its_namesake_suite():
    assert pp.select(["src/pricing/forwards.q"]) == ["test_forwards.q"]


def test_a_module_without_a_namesake_and_a_helper_pick_nothing():
    picked = pp.select(["src/no_such_module.q", "tests/lib/support.q", "tests/q/run_coverage.q"])
    assert picked == []


ADDS_EG = """\
diff --git a/src/pricing/forwards.q b/src/pricing/forwards.q
--- a/src/pricing/forwards.q
+++ b/src/pricing/forwards.q
@@ -10,0 +11 @@
+/ @eg .qf.px.fwd_cont[1.1;0.02;0.01;0.5]  ->  1.105528
"""

NO_EG = """\
diff --git a/src/pricing/forwards.q b/src/pricing/forwards.q
--- a/src/pricing/forwards.q
+++ b/src/pricing/forwards.q
@@ -20 +20 @@
-    s*exp (rd-rf)*t}
+    s*exp t*rd-rf}
@@ -30 +30 @@
-/ The @eg above shows the continuous case.
+/ The @eg above shows the continuously compounded case.
"""


def test_adding_an_eg_line_picks_the_examples_suite():
    assert pp.touches_eg(ADDS_EG)
    picked = pp.select(["src/pricing/forwards.q"], examples=pp.touches_eg(ADDS_EG))
    assert picked == ["test_docstring_examples.q", "test_forwards.q"]


def test_removing_an_eg_line_picks_it_too():
    assert pp.touches_eg(ADDS_EG.replace("+/ @eg", "-/ @eg"))


def test_a_change_touching_no_eg_line_does_not():
    assert not pp.touches_eg(NO_EG), "code and prose that mentions @eg are not a tag"
    assert pp.select(["src/pricing/forwards.q"], examples=pp.touches_eg(NO_EG)) == [
        "test_forwards.q"
    ]


@pytest.fixture
def fake_q(tmp_path, monkeypatch):
    """A `q` that reports the tests named in $FAKE_FAILED as failed, and
    $FAKE_RAN as run."""
    q = tmp_path / "q"
    q.write_text(
        "#!/bin/sh\n"
        'printf "%b" "$FAKE_RAN" > "$UQF_TEST_RESULTS"\n'
        'printf "%b" "$FAKE_FAILED" > "$UQF_TEST_FAILURES"\n'
    )
    q.chmod(0o755)
    monkeypatch.setattr(pp, "peachq", lambda: str(q))
    monkeypatch.setattr(pp, "changed", lambda base: ["tests/q/test_forwards.q"])
    monkeypatch.setattr(pp, "q_diff", lambda base: "")
    return q


def test_a_known_gap_failing_does_not_stop_the_push(fake_q, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_RAN", f".qt.x\t{KNOWN_GAP}\n")
    monkeypatch.setenv("FAKE_FAILED", f"{KNOWN_GAP}\tfail\tnope\n")
    assert pp.run("origin/master") == 0
    assert "1 known gap(s) failed as expected" in capsys.readouterr().out


def test_a_new_failure_stops_the_push_and_is_named(fake_q, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_RAN", ".qt.x\t.qt.x.test_new\n")
    monkeypatch.setenv("FAKE_FAILED", ".qt.x.test_new\terror\ttype\n")
    assert pp.run("origin/master") == 1
    err = capsys.readouterr().err
    assert "FAILED .qt.x.test_new  error: type" in err
    assert "-files test_forwards.q" in err, "says how to reproduce it"


def test_a_suite_that_never_finished_loading_stops_the_push(tmp_path, monkeypatch, capsys):
    q = tmp_path / "q"
    q.write_text('#!/bin/sh\necho "\'nyi" >&2\nexit 1\n')
    q.chmod(0o755)
    monkeypatch.setattr(pp, "peachq", lambda: str(q))
    monkeypatch.setattr(pp, "changed", lambda base: ["tests/q/test_forwards.q"])
    monkeypatch.setattr(pp, "q_diff", lambda base: "")
    assert pp.run("origin/master") == 1
    assert "did not finish loading" in capsys.readouterr().err


def test_an_eg_change_runs_the_examples_suite_on_peachq(fake_q, monkeypatch, capsys):
    """Wired through run(): the suite is on the command line, even when the
    changed module has no namesake suite of its own."""
    monkeypatch.setattr(pp, "changed", lambda base: ["src/no_such_module.q"])
    monkeypatch.setattr(pp, "q_diff", lambda base: ADDS_EG)
    monkeypatch.setenv("FAKE_RAN", ".qt.man.examples\t.qt.man.examples.test_x\n")
    monkeypatch.setenv("FAKE_FAILED", "")
    assert pp.run("origin/master") == 0
    assert "1 suite file(s) on PeachQ: test_docstring_examples.q" in capsys.readouterr().out


def test_nothing_to_run_passes(monkeypatch, capsys):
    monkeypatch.setattr(pp, "changed", lambda base: ["README.md"])
    monkeypatch.setattr(pp, "q_diff", lambda base: "")
    assert pp.run("origin/master") == 0
    assert "nothing to run" in capsys.readouterr().out
