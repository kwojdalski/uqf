"""scripts/dev/checkout_guard.py: a test that writes into output/ fails (#2082).

Run as the root conftest runs it, in a pytest of its own over a scratch tree:
the guard has to fail the test that wrote, not merely notice a difference.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

GUARD = Path(__file__).resolve().parents[1] / "checkout_guard.py"
_SPEC = importlib.util.spec_from_file_location("checkout_guard", GUARD)
assert _SPEC and _SPEC.loader
guard = importlib.util.module_from_spec(_SPEC)
sys.modules.setdefault("checkout_guard", guard)
_SPEC.loader.exec_module(guard)

CONFTEST = f"""\
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("checkout_guard", {str(GUARD)!r})
_guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_guard)

HERE = Path(__file__).parent
checkout_output_untouched = _guard.make_guard(
    HERE / "output", lambda path: path.is_relative_to(HERE / "guarded")
)
"""

TESTS = """\
from pathlib import Path

OUT = Path(__file__).parents[1] / "output" / "uqs"

def test_writes_setenv():
    (OUT / "setenv.sh").write_text('export UQF_STATUS_DIR="/gone"\\n')

def test_rewrites_process_csv():
    (OUT / "process.csv").write_text("host,port\\nlocalhost,9999\\n")

def test_creates_another_runtime():
    (OUT.parent / "uqs-crypto").mkdir()

def test_writes_inside_a_stacks_live_directory():
    (OUT / "logs" / "rdb1.log").write_text("a running stack logs here\\n")

def test_writes_its_own_tmp_path(tmp_path):
    (tmp_path / "setenv.sh").write_text("fine\\n")
"""


def _run(tmp_path: Path, folder: str) -> subprocess.CompletedProcess[str]:
    out = tmp_path / "output" / "uqs"
    (out / "logs").mkdir(parents=True)
    (out / "process.csv").write_text("host,port\n")
    (tmp_path / "conftest.py").write_text(CONFTEST)
    (tmp_path / folder).mkdir()
    (tmp_path / folder / "test_writes.py").write_text(TESTS)
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    return subprocess.run(
        [sys.executable, "-m", "pytest", folder, "-p", "no:cacheprovider", "-p", "no:xdist",
         "-rA", "-o", "addopts="],
        cwd=tmp_path, capture_output=True, text=True, env=env, timeout=120, check=False,
    )  # fmt: skip


def _outcomes(report: str) -> dict[str, str]:
    """test name -> PASSED or ERROR, from the -rA summary. A test the guard
    fails is listed twice - its call PASSED, its teardown an ERROR - and the
    ERROR is its outcome."""
    found: dict[str, str] = {}
    for line in report.splitlines():
        outcome, _, rest = line.partition(" ")
        if outcome in ("PASSED", "ERROR", "FAILED") and "::" in rest:
            name = rest.split("::")[-1].split()[0]
            if found.get(name) in (None, "PASSED"):
                found[name] = outcome
    return found


def test_a_test_that_writes_into_output_fails_naming_what_it_wrote(tmp_path):
    done = _run(tmp_path, "guarded")
    assert done.returncode == 1, done.stdout + done.stderr
    assert _outcomes(done.stdout) == {
        "test_writes_setenv": "ERROR",
        "test_rewrites_process_csv": "ERROR",
        "test_creates_another_runtime": "ERROR",
        "test_writes_inside_a_stacks_live_directory": "PASSED",
        "test_writes_its_own_tmp_path": "PASSED",
    }
    assert "created uqs/setenv.sh" in done.stdout
    assert "changed uqs/process.csv" in done.stdout
    assert "created uqs-crypto" in done.stdout
    assert "(#2082)" in done.stdout


def test_a_test_the_guard_does_not_apply_to_is_left_alone(tmp_path):
    done = _run(tmp_path, "unguarded")
    assert done.returncode == 0, done.stdout + done.stderr


def test_snapshot_compares_two_levels_and_only_the_existence_of_deep_directories(tmp_path):
    out = tmp_path / "output"
    assert guard.snapshot(out) == {}  # no output/ at all, as in a fresh worktree
    (out / "uqs" / "hdb").mkdir(parents=True)
    before = guard.snapshot(out)
    (out / "uqs" / "hdb" / "2026.11.04").mkdir()
    assert guard.changes(before, guard.snapshot(out)) == []
    (out / "uqs" / "setenv.sh").write_text("x")
    assert guard.changes(before, guard.snapshot(out)) == ["changed uqs", "created uqs/setenv.sh"]
