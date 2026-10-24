"""A backfill example's shell commands take its directory as one word (#1587).

The example's directory is the absolute workspace path, which may hold spaces
or quotes. Unquoted, `rm -rf <dir>` in a workspace named `repo with spaces`
removes a sibling checkout named `repo`. These run the example's own quoting
helper in q against real directories, so the test is the command the example
runs, not a reading of its text.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from uqs.interpreter import q_interpreter
from uqs.scaffold.example import example_body

NS = "qtxex"


def _body() -> str:
    return example_body("tx", "tx_backfill", "tx_src", "tx", "local", "TX_CRED")


def _quoted_definition() -> str:
    """The example's `quoted` helper, as the line it is written on."""
    (line,) = [ln for ln in _body().splitlines() if ln.startswith("quoted:")]
    return line


def _q() -> tuple[str, dict[str, str]]:
    env = os.environ.copy()
    q = q_interpreter(env)
    if q is None:
        pytest.skip("no q interpreter - set $QCMD, or put q on PATH")
    env.setdefault("QHOME", str(Path.home() / ".kx"))
    return str(q), env


def test_every_shell_command_quotes_the_directory() -> None:
    commands = [ln for ln in _body().splitlines() if "system " in ln]
    assert commands, "the example runs no shell command"
    for line in commands:
        assert f".{NS}.quoted .{NS}.dir" in line, line


@pytest.mark.parametrize("name", ["repo with spaces", "it's repo", "plain"])
def test_cleanup_removes_only_the_example_directory(tmp_path: Path, name: str) -> None:
    q, env = _q()
    sibling = tmp_path / "repo"
    sibling.mkdir()
    (sibling / "keep").write_text("x")
    workspace = tmp_path / name
    target = workspace / "build" / "tx_example_1"
    target.mkdir(parents=True)
    (target / "old").write_text("x")
    script = tmp_path / "probe.q"
    script.write_text(
        "\n".join(
            [
                _quoted_definition(),
                f'dir:"{target.as_posix()}"',
                'system "rm -rf ",quoted dir;',
                'system "mkdir -p ",quoted dir,"/status";',
                "exit 0",
            ]
        )
        + "\n"
    )
    done = subprocess.run(
        [q, str(script), "-q"], env=env, capture_output=True, text=True, timeout=60
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert (sibling / "keep").exists(), "cleanup removed a sibling of the workspace"
    assert not (target / "old").exists(), "cleanup left the example directory"
    assert (target / "status").is_dir(), "setup did not create the status directory"
    assert not (tmp_path / "with").exists(), "mkdir split the path on its spaces"
