"""`uqs --target NAME ...` against a source-only release (#1642).

A release installed by something other than `uqs deploy push` holds the tree
under `current` but no .venv and no deploy.env. Its target names an
interpreter and an environment file, and the command runs the release's own
source. As in test_remote_targets.py, a stand-in `ssh` executes the very
string the real one would hand the remote shell, so quoting, the preflight
and the exit codes are exercised for real. The interpreter is a stand-in
too: `-c 'import uqs.cli'` is the dependency probe, `-m uqs ...` echoes what
it was run with.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from uqs.deploy import control, launch, targets
from uqs.deploy.config import DeployError
from uqs.paths import UqsError

FAKE_SSH = """#!/bin/bash
cmd="${@: -1}"
exec bash -c "$cmd"
"""
FAKE_PYTHON = """#!/bin/bash
if [ "$1" = -c ]; then exit "${FAKE_IMPORT_EXIT:-0}"; fi
[ "$1 $2" = "-m uqs" ] || { echo "unexpected: $*" >&2; exit 99; }
shift 2
echo "env=$FAKE_ENV cwd=$(basename "$(pwd -P)") path=$PYTHONPATH"
for a in "$@"; do echo "[$a]"; done
exit "${FAKE_EXIT:-0}"
"""


@pytest.fixture
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "ssh").write_text(FAKE_SSH)
    (bin_dir / "ssh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    # A source-only release: the tree, no .venv, no deploy.env.
    release = tmp_path / "srv" / "releases" / "r9"
    source = release / launch.SOURCE_DIR / "uqs"
    source.mkdir(parents=True)
    (source / "__main__.py").write_text("")
    (tmp_path / "srv" / "current").symlink_to(release)
    # Spaces and a quote in both paths: everything reaching the shell is quoted.
    provisioned = tmp_path / "opt dir" / "py it's"
    provisioned.mkdir(parents=True)
    (provisioned / "python3").write_text(FAKE_PYTHON)
    (provisioned / "python3").chmod(0o755)
    (tmp_path / "uat runtime.env").write_text("export FAKE_ENV=uat\n")
    declared = tmp_path / "deploy_targets.toml"
    declared.write_text(
        f'[targets.uat]\nhost = "svc@uat.example"\ndest = "{tmp_path / "srv"}"\n'
        'launch_mode = "source"\n'
        f'launch_python = "{provisioned / "python3"}"\n'
        f'launch_env_file = "{tmp_path / "uat runtime.env"}"\n\n'
        f'[targets.prepared]\nhost = "h"\ndest = "{tmp_path / "srv"}"\n'
    )
    monkeypatch.setenv(targets.DECLARATION_ENV, str(declared))
    monkeypatch.delenv("PYTHONPATH", raising=False)
    return tmp_path


def _remote(name: str) -> control.Remote:
    return control.remote_for(name, Path("."))


def _run(name: str, args: list[str]) -> subprocess.CompletedProcess:
    argv = control.ssh_argv(_remote(name), args, tty=False)
    return subprocess.run(argv, capture_output=True, text=True, check=False)


def test_a_read_only_command_runs_from_the_releases_own_source(server):
    """No .venv/bin/uqs and no deploy.env: the configured interpreter runs
    `-m uqs` in current, with the environment file loaded and the release's
    source first on PYTHONPATH, so it is that release's CLI that runs."""
    expr = 'select from t where x like "it\'s"; $(rm -rf /)'
    done = _run("uat", ["query", "--proc", "rdb1", expr])
    assert done.returncode == 0, done.stderr
    first, *rest = done.stdout.splitlines()
    assert first.startswith("env=uat cwd=r9 path=")
    assert first.split("path=", 1)[1].split(":")[0].endswith("/current/" + launch.SOURCE_DIR)
    assert rest == ["[query]", "[--proc]", "[rdb1]", f"[{expr}]"]


def test_the_remote_exit_code_is_passed_through(server, monkeypatch):
    monkeypatch.setenv("FAKE_EXIT", "3")
    assert control.run(_remote("uat"), ["summary"], tty=False) == 3


@pytest.mark.parametrize(
    ("break_it", "says"),
    [
        (lambda t: (t / "opt dir" / "py it's" / "python3").unlink(), "launch_python"),
        (lambda t: (t / "uat runtime.env").unlink(), "launch_env_file"),
        (
            lambda t: (t / "srv/releases/r9" / launch.SOURCE_DIR / "uqs/__main__.py").unlink(),
            "no python/uqs/src/uqs",
        ),
        (lambda t: (t / "srv/current").unlink(), "no deployed release"),
    ],
)
def test_each_missing_prerequisite_is_named(server, break_it, says):
    break_it(server)
    with pytest.raises(UqsError, match=says):
        control.run(_remote("uat"), ["summary"], tty=False)


def test_an_interpreter_without_uqfs_dependencies_is_refused(server, monkeypatch):
    monkeypatch.setenv("FAKE_IMPORT_EXIT", "1")
    with pytest.raises(UqsError, match="cannot import uqf's dependencies"):
        control.run(_remote("uat"), ["summary"], tty=False)


def test_a_prepared_release_without_its_cli_is_not_run_from_source(server):
    """The same tree under a target without launch_mode: refused as a release
    missing its .venv - not reported absent, and never run from source."""
    with pytest.raises(UqsError, match=r"cannot launch uqs: its release has no \.venv/bin/uqs"):
        control.run(_remote("prepared"), ["summary"], tty=False)


def test_a_prepared_release_without_deploy_env_says_so(server):
    venv = server / "srv/releases/r9/.venv/bin"
    venv.mkdir(parents=True)
    (venv / "uqs").write_text("#!/bin/bash\necho ran\n")
    (venv / "uqs").chmod(0o755)
    with pytest.raises(UqsError, match="environment file is missing"):
        control.run(_remote("prepared"), ["summary"], tty=False)
    (server / "srv/releases/r9/deploy.env").write_text("")
    assert control.run(_remote("prepared"), ["summary"], tty=False) == 0


def test_the_service_account_runs_a_source_release_too():
    r = control.Remote(
        "uat", "h", "/srv/uqf", remote_user="svc",
        launch_mode=launch.SOURCE, launch_python="/opt/py/bin/python3",
        launch_env_file="/etc/uqf/uat.env",
    )  # fmt: skip
    command = control.remote_command(r, ["summary"])
    assert command.startswith("sudo -n -iu svc bash -c ")
    assert "/opt/py/bin/python3 -m uqs summary" in command


def test_the_shell_puts_the_interpreter_and_source_first(server):
    script = control.shell_command(_remote("uat")).replace(
        'exec "${SHELL:-bash}" -i', 'echo "$UQS_TARGET $FAKE_ENV"; command -v python3'
    )
    done = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    assert "uat uat" in done.stdout and "py it's/python3" in done.stdout


@pytest.mark.parametrize(
    ("settings", "says"),
    [
        ('launch_mode = "venv"', "launch_mode 'venv' is not one of release, source"),
        ('launch_mode = "source"', "needs launch_python and launch_env_file"),
        (
            'launch_mode = "source"\nlaunch_python = "bin/python3"\nlaunch_env_file = "/e"',
            "launch_python 'bin/python3' must be an absolute path",
        ),
        ('launch_python = "/opt/py"', "launch_python set, but launch_mode is release"),
    ],
)
def test_invalid_launch_settings_are_refused(tmp_path, monkeypatch, settings, says):
    declared = tmp_path / "deploy_targets.toml"
    declared.write_text(f'[targets.x]\nhost = "h"\ndest = "/srv/uqf"\n{settings}\n')
    monkeypatch.setenv(targets.DECLARATION_ENV, str(declared))
    with pytest.raises(UqsError, match=says):
        control.remote_for("x", Path("."))


def test_a_pushed_release_target_is_unchanged():
    r = control.Remote("uat", "h", "/srv/uqf", remote_user="svc")
    assert r.launch_mode == launch.RELEASE
    command = control.remote_command(r, ["summary"])
    assert command.startswith("sudo -n -iu svc bash -c ")
    assert ".venv/bin/uqs summary" in command and "-m uqs" not in command


def test_push_reads_a_source_target_without_refusing_its_launch_keys(server):
    """The launch keys are `uqs --target`'s: push neither refuses them as
    unknown nor passes them on as options."""
    (cfg,) = targets.configs("uat", "a.tar.gz", {"profile": "essential"}, True, Path("."))
    assert cfg.target == "uat"
    assert not targets.LAUNCH_KEYS & set(cfg.sources)


def test_an_unknown_target_key_is_still_refused(tmp_path, monkeypatch):
    declared = tmp_path / "deploy_targets.toml"
    declared.write_text('[targets.x]\nhost = "h"\ndest = "/srv/uqf"\nlaunch_pyhton = "/p"\n')
    monkeypatch.setenv(targets.DECLARATION_ENV, str(declared))
    with pytest.raises(DeployError, match="unknown setting"):
        targets.configs("x", "a.tar.gz", {"profile": "essential"}, True, Path("."))
