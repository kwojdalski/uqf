"""The compression policy TorQ's `.cmp` reads (stack/compression.py, #2152): copied
into TORQDATA on bootstrap, and the file `.cmp` resolves once the overlay has
loaded -- not the starter pack's, which TorQ's own lookup would prefer."""

from __future__ import annotations

import subprocess
from pathlib import Path

from uqs.paths import UqsPaths
from uqs.stack import compression


def _paths(tmp_path: Path, repo_root: Path) -> UqsPaths:
    (tmp_path / "data").mkdir()
    return UqsPaths(
        repo_root=tmp_path,
        torqhome=tmp_path / "torq",
        torqapphome=tmp_path / "app",
        torqdata=tmp_path / "data",
        scripts_dir=repo_root / "scripts",
        orchestrator_dir=tmp_path / "uqs",
        runtime="uqf",
    )


def test_bootstrap_copies_the_derived_policy_into_torqdata(tmp_path, repo_root):
    paths = _paths(tmp_path, repo_root)
    assert compression.write_compression_config(paths)
    committed = (repo_root / "scripts" / "torqconfig" / "compressionconfig.csv").read_text()
    assert compression.target(paths).read_text() == committed
    assert compression.target(paths) == tmp_path / "data" / "compressionconfig.csv"


def test_a_tree_without_a_policy_writes_nothing(tmp_path):
    paths = UqsPaths(
        repo_root=tmp_path,
        torqhome=tmp_path / "torq",
        torqapphome=tmp_path / "app",
        torqdata=tmp_path / "data",
        scripts_dir=tmp_path / "scripts",
        orchestrator_dir=tmp_path / "uqs",
        runtime="uqf",
    )
    assert not compression.write_compression_config(paths)
    assert not compression.target(paths).exists()


def test_cmp_resolves_the_generated_copy_not_the_starter_packs(q_binary, repo_root, tmp_path):
    """The overlay is loaded before compress.q, as a process that runs `.cmp` is
    to load it. `.proc.getconfigfile` is stubbed to answer what TorQ's lookup
    does -- the starter pack's own appconfig file, first -- and compress.q
    (lib/torq, never edited) keeps the value the overlay set."""
    q, env = q_binary
    starter = (
        repo_root / "lib" / "torq-finance-starter-pack" / "appconfig" / "compressionconfig.csv"
    )
    assert starter.is_file(), "the starter pack ships a file TorQ's lookup would prefer"
    script = tmp_path / "resolve.q"
    script.write_text(
        ".lg.o:{[a;b] ::};.lg.e:{[a;b] -2 b; exit 1};\n"
        f'.proc.getconfigfile:{{enlist `$":{starter}"}};\n'
        f"\\l {repo_root}/scripts/processes/uqs_compression_config.q\n"
        f"\\l {repo_root}/lib/torq/code/common/compress.q\n"
        "-1 .cmp.inputcsv;\n"
        "exit 0\n"
    )
    out = subprocess.run(
        [q, str(script)], capture_output=True, text=True, timeout=60,
        env={**env, "TORQDATA": str(tmp_path / "data")}, cwd=repo_root,
    )  # fmt: skip
    lines = [x for x in out.stdout.splitlines() if x.strip()]
    assert lines[-1:] == [f":{tmp_path / 'data'}/compressionconfig.csv"], out.stdout + out.stderr
