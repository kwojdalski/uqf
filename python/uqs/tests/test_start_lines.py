"""`start --dry-run`'s lines are torq.sh's, composed without bootstrapping (#1880).

stack/start_lines.py rebuilds torq.sh's `startline` and `print` in Python so a
dry run need not write process.csv and setenv.sh for torq.sh to read. That is
only worth having while the two agree, so this bootstraps a data directory,
asks the vendored torq.sh to print, and holds the composed lines to the same
text, byte for byte.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from uqs.paths import paths_for_root, repo_root
from uqs.stack import runtime, start_lines

needs_tools = pytest.mark.skipif(
    any(shutil.which(t) is None for t in ("bash", "envsubst")),
    reason="torq.sh needs bash and envsubst",
)


@pytest.fixture
def paths(tmp_path, monkeypatch):
    """This checkout, with its data directory under tmp_path."""
    monkeypatch.setenv("UQS_DATA_ROOT", str(tmp_path))
    for var in ("UQS_TORQ_LAUNCHER", "TORQHOME", "TORQAPPHOME", "UQS_RUNTIME"):
        monkeypatch.delenv(var, raising=False)
    return paths_for_root(repo_root())


def _tree(root: Path) -> set[Path]:
    return set(root.rglob("*")) if root.exists() else set()


def test_the_lines_are_composed_with_nothing_written(paths):
    before = _tree(paths.torqdata.parent)
    lines = start_lines.plan(paths, "rdb1 hdb1", base_port=7000)
    assert [line.procname for line in lines] == ["rdb1", "hdb1"]
    assert "-stackid 7000 -proctype rdb -procname rdb1" in lines[0].line
    assert _tree(paths.torqdata.parent) == before


def test_trace_is_on_every_composed_line(paths):
    """`start --dry-run --trace` shows the `-trace` torq.sh appends (#2675)."""
    lines = start_lines.plan(paths, "rdb1 hdb1", base_port=7000, extras=("-trace",))
    assert all(line.line.endswith(" -trace ") for line in lines)
    assert not any("-trace" in line.line for line in start_lines.plan(paths, "rdb1 hdb1"))


@needs_tools
@pytest.mark.parametrize(
    ("procs", "port", "trace"),
    [("all", None, False), ("rdb1 hdb1 stp1", 7000, False), ("rdb1 hdb1", 7000, True)],
)
def test_the_composed_lines_are_torq_sh_print(paths, procs, port, trace):
    composed = runtime.print_procs(paths, procs, base_port=port, trace=trace).stdout
    extras = ["-extras", "-trace"] if trace else []
    printed = runtime.run_torq_sh(paths, ["print", procs, *extras], base_port=port, capture=True)
    assert printed.returncode == 0, printed.stderr
    assert composed == printed.stdout


def test_envsubst_empties_an_unset_variable():
    env = {"A": "1"}
    assert start_lines._envsubst("$A ${A} $B ${B}x $", env) == "1 1  x $"
