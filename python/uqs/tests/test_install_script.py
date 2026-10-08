"""./install.sh (#850), run against stub tools.

Each test copies the script into a fake repository and runs it with a PATH
holding only stubs, so a missing, broken or failing tool is a stub left out or
told to fail - nothing real is installed, and no network is used. Every stub
logs its argv, so what the script ran (or did not) is asserted directly.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
BASH = shutil.which("bash") or "/bin/bash"

STUBS = {
    # `tool list` answers $FAKE_UV_LIST; `tool install` of the package puts a
    # working `uqs` stub on PATH, unless $FAKE_UV_FAIL makes it fail.
    "uv": """
if [ -n "$FAKE_UV_FAIL" ]; then exit 1; fi
if [ "$1 $2" = "tool list" ]; then printf '%b' "$FAKE_UV_LIST"; exit 0; fi
if [ "$1 $2" = "tool install" ] && [ "$3" = "--force" ]; then
  printf '#!%s\\necho "$0 $*" >> "$LOG"\\n' "$BASH_PATH" > "$BIN/uqs"; chmod +x "$BIN/uqs"
fi
case "$1 $2 $3" in "tool install pre-commit"*)
  printf '#!%s\\necho "pre-commit $*" >> "$LOG"\\n' "$BASH_PATH" > "$BIN/pre-commit"
  chmod +x "$BIN/pre-commit";;
esac
""",
    "q": 'read -r line; if [ -z "$FAKE_Q_BROKEN" ]; then echo UQF_Q_OK; fi',
    "node": 'echo "${FAKE_NODE:-v22.14.0}"',
    "envsubst": "",
    "rlwrap": "",
    "npm": "",
    "git": "",
    "pre-commit": "",
}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    for rel in ("lib/torq/torq.q", "python/uqs/pyproject.toml", "web/package.json"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("")
    shutil.copy2(ROOT / "install.sh", root / "install.sh")
    return root


def run(repo: Path, *args: str, missing: tuple[str, ...] = (), **env: str):
    """Run the copied script from OUTSIDE the repo; (code, output, log lines)."""
    bin_dir = repo.parent / "bin"
    bin_dir.mkdir(exist_ok=True)
    log = repo.parent / "calls.log"
    log.touch()
    for name, body in STUBS.items():
        stub = bin_dir / name
        if name in missing:
            stub.unlink(missing_ok=True)
            continue
        stub.write_text(f'#!{BASH}\necho "{name} $*" >> "$LOG"\n{body}\n')
        stub.chmod(0o755)
    for tool in ("uname", "sed", "chmod"):
        link = bin_dir / tool
        if not link.exists():
            link.symlink_to(shutil.which(tool) or f"/usr/bin/{tool}")
    elsewhere = repo.parent / "elsewhere"
    elsewhere.mkdir(exist_ok=True)
    result = subprocess.run(
        [BASH, str(repo / "install.sh"), *args],
        cwd=elsewhere,
        env={
            "PATH": str(bin_dir),
            "HOME": str(repo.parent),
            "LOG": str(log),
            "BIN": str(bin_dir),
            "BASH_PATH": BASH,
            "FAKE_UV_LIST": "",
            **env,
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.returncode, result.stdout + result.stderr, log.read_text().splitlines()


def ran(calls: list[str], prefix: str) -> bool:
    return any(c.startswith(prefix) for c in calls)


def test_the_default_installs_the_cli_from_any_directory(repo: Path) -> None:
    code, out, calls = run(repo)
    assert code == 0, out
    assert "uv tool install --force --editable python/uqs" in calls
    assert "uqs --help" in " ".join(calls)  # the install is verified
    assert "Installed:" in out
    # lightweight: nothing beyond the CLI
    assert not ran(calls, "uv sync") and not ran(calls, "npm") and not ran(calls, "pre-commit")


def test_check_changes_nothing(repo: Path) -> None:
    code, out, calls = run(repo, "--check", "--dev", "--web")
    assert code == 0, out
    assert "nothing was changed" in out
    assert [c for c in calls if not c.startswith(("q ", "node "))] == []


@pytest.mark.parametrize("tool", ["rlwrap", "envsubst", "uv"])
def test_a_missing_required_tool_stops_before_any_change(repo: Path, tool: str) -> None:
    code, out, calls = run(repo, missing=(tool,))
    assert code == 1
    assert "Missing, required:" in out and tool in out
    assert "Nothing was changed" in out
    assert not ran(calls, "uv tool install")


def test_a_q_that_cannot_run_is_reported(repo: Path) -> None:
    code, out, _ = run(repo, FAKE_Q_BROKEN="1")
    assert code == 1 and "QHOME" in out


def test_missing_torq_is_reported(repo: Path) -> None:
    (repo / "lib/torq/torq.q").unlink()
    code, out, _ = run(repo)
    assert code == 1 and "TORQHOME" in out


def test_optional_tools_are_reported_not_installed(repo: Path) -> None:
    code, out, calls = run(repo)
    assert code == 0
    assert "Missing, optional:" in out and "direnv" in out
    assert not any("direnv" in c for c in calls)


def test_dev_syncs_and_installs_hooks(repo: Path) -> None:
    code, out, calls = run(repo, "--dev", missing=("pre-commit",))
    assert code == 0, out
    assert "uv sync" in calls
    assert "uv tool install pre-commit==4.6.2" in calls
    assert "pre-commit install" in calls


def test_web_builds_from_the_lockfile(repo: Path) -> None:
    code, out, calls = run(repo, "--web")
    assert code == 0, out
    assert calls.index("npm --prefix web ci") < calls.index("npm --prefix web run build")


@pytest.mark.parametrize("version", ["v20.11.0", "v22.12.0", "v23.1.0", "v25.0.0"])
def test_an_unsupported_node_is_refused(repo: Path, version: str) -> None:
    code, out, calls = run(repo, "--web", FAKE_NODE=version)
    assert code == 1 and version.lstrip("v") in out
    assert not ran(calls, "npm")


def test_a_rerun_is_safe_and_former_names_are_removed(repo: Path) -> None:
    assert run(repo)[0] == 0
    code, out, calls = run(repo, FAKE_UV_LIST="uqf-stack v0.1.0\\n- uqs\\n")
    assert code == 0, out
    assert "uv tool uninstall uqf-stack" in calls
    assert "uv tool uninstall torq-demo" not in calls


def test_a_failing_command_ends_the_run_with_its_name(repo: Path) -> None:
    code, out, _ = run(repo, FAKE_UV_FAIL="1")
    assert code == 1
    assert "failed: uv tool install" in out


def test_an_unknown_option_is_refused(repo: Path) -> None:
    assert run(repo, "--everything")[0] == 2


def test_tls_is_never_bypassed_and_sudo_never_run() -> None:
    text = (ROOT / "install.sh").read_text()
    assert "allow-insecure-host" not in text
    # sudo appears only inside the install hints it prints, never as a command
    assert re.search(r"^\s*(\$sudo|sudo)\b", text, re.MULTILINE) is None


def test_the_old_installer_is_gone() -> None:
    assert not (ROOT / "scripts/dev/install.sh").exists()
    assert os.access(ROOT / "install.sh", os.X_OK)
