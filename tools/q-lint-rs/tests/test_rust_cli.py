"""Black-box checks for the compiled Rust executable, including LSP failures."""

import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
BINARY = ROOT / "tools/q-lint-rs/target/release/qlinter"
SERVER = runpy.run_path(str(ROOT / "tools/q-lint/tests/test_qls.py"))["SERVER"]
pytestmark = pytest.mark.skipif(not BINARY.is_file(), reason="Build the Rust release binary first")


def run(*args, cwd=None, source=None):
    return subprocess.run(
        [str(BINARY), *map(str, args)],
        input=source,
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=10,
    )


def test_stdin_and_taxonomy():
    result = run("-", "--format", "json", source="f:{[desc] desc}")
    assert result.returncode == 1
    assert json.loads(result.stdout)[0]["code"] == "QF001"
    assert run("--explain", "QF001").returncode == 0
    assert run("--explain", "UNKNOWN").returncode == 2


def test_exclusions_config_discovery_and_deduplication(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[tool.q-lint]\nexclude=["torq/*"]\n')
    nested = tmp_path / "src"
    nested.mkdir()
    bad = tmp_path / "torq/nested/bad.q"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"\xff")
    good = nested / "good.q"
    good.write_text("a:1;")
    assert run("..", cwd=nested).returncode == 0
    assert run(bad, cwd=nested).returncode == 0
    good.write_text("f:{[desc] desc}")
    result = run("..", good, "--format", "json", cwd=nested)
    assert len(json.loads(result.stdout)) == 1
    assert run("..", "--exclude", "src/", cwd=nested).returncode == 0
    assert run("-", "--stdin-filename", bad, cwd=nested, source="f:{]").returncode == 0


def test_errors_are_not_partial_json(tmp_path):
    for args in ([tmp_path], [tmp_path / "missing.q"], ["--config", tmp_path / "missing.toml"]):
        result = run(*args, "--format", "json")
        assert result.returncode == 2 and not result.stdout


@pytest.mark.parametrize(
    "mode,exit_code",
    [
        ("normal", 1),
        ("crash", 2),
        ("invalid", 2),
        ("init_error", 2),
        ("malformed", 2),
        ("silent", 2),
        ("hang_shutdown", 1),
    ],
)
def test_lsp_server_modes(tmp_path, mode, exit_code):
    server = tmp_path / "fake qls"
    server.write_text(f"#!{sys.executable}\nMODE={mode!r}\n" + SERVER)
    server.chmod(0o755)
    result = run(
        "-",
        "--backend",
        "qls",
        "--qls-executable",
        server,
        "--qls-timeout",
        "0.5",
        "--format",
        "json",
        source="/ café\nBAD",
    )
    assert result.returncode == exit_code, result.stderr
    if exit_code == 1:
        finding = json.loads(result.stdout)[0]
        assert (
            finding["code"],
            finding["rule"],
            finding["line"],
            finding["column"],
            finding["end_column"],
        ) == ("QLS001", "qls/syntax", 2, 3, 6)
    else:
        assert not result.stdout


@pytest.mark.skipif(not os.environ.get("Q_LINT_TEST_QLS"), reason="Optional real qls")
def test_real_qls():
    result = run(
        "-",
        "--backend",
        "qls",
        "--qls-executable",
        os.environ["Q_LINT_TEST_QLS"],
        "--format",
        "json",
        source="f:{[x] x+1};\n",
    )
    assert result.returncode in (0, 1), result.stderr
    assert isinstance(json.loads(result.stdout), list)
