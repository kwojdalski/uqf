"""The data directory's layout is named once, in uqs.paths (#819).

stack/env.py tells TorQ where to log and where the HDB is (KDBLOG, KDBHDB),
and uqs, the feeds and the checks read the same places. Twelve modules used
to rebuild those directories from `torqdata` by hand, and three wrote TorQ's
log file names themselves - correct only while every copy said the same
word. Move the logs, and TorQ would write where env.py said while `uqs logs`
read the old place and reported a quiet process.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from uqs.paths import UqsPaths, torq_log_stem
from uqs.stack.env import build_env

PYTHON = Path(__file__).resolve().parents[2]

#: A directory under the data directory built by hand, or a TorQ log name
#: written out, rather than asked of UqsPaths.
HAND_BUILT = re.compile(
    r"""torqdata\)?\s*/\s*["'](logs|hdb)["']|f["'](out|err|\{stream\}|\{kind\})_\{"""
)


@pytest.fixture
def fake_paths(tmp_path: Path) -> UqsPaths:
    return UqsPaths(
        repo_root=tmp_path,
        torqhome=tmp_path / "lib" / "torq",
        torqapphome=tmp_path / "lib" / "torq-finance-starter-pack",
        torqdata=tmp_path / "output" / "uqs",
        scripts_dir=tmp_path / "scripts",
        orchestrator_dir=tmp_path / "python" / "uqs",
    )


def test_only_paths_builds_the_data_layout():
    offenders = [
        f"{path.relative_to(PYTHON)}:{n}"
        for path in sorted(PYTHON.glob("*/src/**/*.py"))
        if path.name != "paths.py"
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if HAND_BUILT.search(line)
    ]
    assert not offenders, f"ask UqsPaths (log_dir, hdb_dir, torq_log) instead: {offenders}"


def test_torq_is_told_the_directories_uqs_reads(fake_paths):
    env = build_env(fake_paths)
    assert env["KDBLOG"] == str(fake_paths.log_dir)
    assert env["KDBHDB"] == str(fake_paths.hdb_dir)


def test_a_torq_log_is_named_as_torq_names_it(fake_paths):
    """`<stream>_<proc>.log`, the alias TorQ repoints on each roll."""
    assert fake_paths.torq_log("rdb1", "err") == fake_paths.log_dir / "err_rdb1.log"
    assert torq_log_stem("hdb1", "out") == "out_hdb1"
