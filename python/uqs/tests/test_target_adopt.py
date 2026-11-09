"""`uqs target adopt`: a release copied onto a server by hand (#1756).

The "server" is a temporary directory and `ssh` a stand-in that runs the
remote command here (as in test_remote_targets.py), so the survey's and the
move's shell text run for real. The copied tree is a uqf-shaped source tree
with no .venv and no deploy.env - what scp of a checkout leaves - whose
`python -m uqs` echoes what it was asked, and whose `uqs.cli` imports or not
as each test needs.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from uqs.cli import app
from uqs.deploy import control, history, target_changelog, targets

FAKE_SSH = """#!/bin/bash
cmd="${@: -1}"; host="${@: -2:1}"
[ "$host" = unreachable ] && { echo "ssh: connect to host unreachable" >&2; exit 255; }
exec bash -c "$cmd"
"""
MAIN = 'import sys\nprint("copied uqs ran:", " ".join(sys.argv[1:]))\n'
DECLARED = "# this workstation's targets\n"


@pytest.fixture
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """<tmp>/srv: a uqf tree copied by hand, and the workstation's files."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "ssh").write_text(FAKE_SSH)
    (bin_dir / "ssh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    srv = tmp_path / "srv"
    pkg = srv / "python" / "uqs" / "src" / "uqs"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "__main__.py").write_text(MAIN)
    (pkg / "cli.py").write_text("")
    (srv / "output" / "uqs").mkdir(parents=True)
    (srv / "output" / "uqs" / "setenv.sh").write_text("export COPIED=1\n")
    (srv / "src").mkdir()
    (srv / ".hidden").write_text("dotfiles move too\n")
    declared = tmp_path / "deploy_targets.toml"
    declared.write_text(DECLARED)
    monkeypatch.setenv(targets.DECLARATION_ENV, str(declared))
    monkeypatch.delenv(history.HISTORY_ENV, raising=False)
    return srv


@pytest.fixture
def logged():
    from loguru import logger

    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="WARNING", format="{message}")
    yield lines
    logger.remove(sink)


def _tree(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def _adopt(srv: Path, *extra: str, input: str | None = None):
    argv = ["target", "adopt", "lvc", "--ssh", "svc@lvc", "--dest", str(srv)]
    return CliRunner().invoke(app, [*argv, *extra], input=input, terminal_width=200)


def _flat(text: str) -> str:
    """Without whitespace: rich wraps long paths wherever the console ends."""
    return "".join(text.split())


def _has(said: str, *parts: str) -> bool:
    return all(_flat(p) in _flat(said) for p in parts)


def test_a_dry_run_reports_source_mode_and_writes_nothing(server, tmp_path):
    before = _tree(tmp_path)
    r = _adopt(server, "--launch-python", sys.executable)
    assert r.exit_code == 0, r.output
    assert _has(
        r.output,
        "launches from source: no .venv/bin/uqs",
        f"environment {server}/output/uqs/setenv.sh (from output/uqs/setenv.sh)",
        f"--launch-python: {sys.executable} - imports uqf",
        "would move what",
        f"link {server}/current -> releases/adopted-",
        'launch_mode = "source"',
        f'launch_env_file = "{server}/current/output/uqs/setenv.sh"',
        "dry run: nothing was changed. Run it again with --apply",
    )
    assert _tree(tmp_path) == before, "nothing written on the server or here"
    assert (tmp_path / "deploy_targets.toml").read_text() == DECLARED


def test_apply_links_the_release_saves_the_target_and_it_then_runs(server, tmp_path):
    r = _adopt(server, "--launch-python", sys.executable, "--apply", "--yes")
    assert r.exit_code == 0, r.output
    (release,) = (server / "releases").iterdir()
    assert release.name.startswith("adopted-")
    assert (server / "current").readlink() == Path("releases") / release.name
    assert sorted(p.name for p in server.iterdir()) == ["current", "releases"]
    assert (release / ".hidden").is_file() and (release / "python" / "uqs").is_dir()
    saved = targets.read(Path("."))["lvc"]
    assert saved == {
        "ssh": "svc@lvc",
        "dest": str(server),
        "launch_mode": "source",
        "launch_python": sys.executable,
        "launch_env_file": f"{server}/current/output/uqs/setenv.sh",
    }
    assert (tmp_path / "deploy_targets.toml").read_text().startswith(DECLARED)
    assert target_changelog.read_log(Path("."))[-1]["kind"] == target_changelog.ADOPTED
    (entry,) = history.read(Path("."))
    assert (entry["target"], entry["status"], entry["release"]) == (
        "lvc", "adopted", release.name,
    )  # fmt: skip
    assert history.latest(Path("."), "lvc") == entry, "it is the server's default"
    shown = CliRunner().invoke(app, ["deploy", "history", "--json"])
    assert json.loads(shown.output)["deployments"][0]["status"] == "adopted"
    # `uqs --target lvc ...` now reaches it, from source, through the entry point
    env = {**os.environ, "PYTHONPATH": str(Path(control.__file__).parents[2])}
    done = subprocess.run(
        [sys.executable, "-c", "from uqs.cli import main; main()", "--target", "lvc", "summary"],
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, check=False,
        timeout=60,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    assert "copied uqs ran: summary" in done.stdout


def test_it_refuses_when_no_python_imports_uqfs_dependencies(server, tmp_path, logged):
    pkg = server / "python" / "uqs" / "src" / "uqs"
    (pkg / "cli.py").write_text("import a_dependency_nobody_installed\n")
    before = _tree(tmp_path)
    r = _adopt(server, "--launch-python", sys.executable, "--apply", "--yes")
    said = r.output + "".join(logged)
    assert r.exit_code == 1
    assert _has(
        said,
        "no Python on the server imports uqf's dependencies (import uqs.cli)",
        f"--launch-python {sys.executable}: ModuleNotFoundError",
        "PATH ",  # every probe is named
    ), said
    assert _tree(tmp_path) == before


def test_a_pushed_release_already_linked_is_saved_as_is(server, tmp_path):
    release = server / "releases" / "20260101T000000Z-abcdefabcdef"
    (release / ".venv" / "bin").mkdir(parents=True)
    (release / ".venv" / "bin" / "uqs").write_text("#!/bin/bash\n")
    (release / ".venv" / "bin" / "uqs").chmod(0o755)
    (release / "deploy.env").write_text("")
    (server / "current").symlink_to(Path("releases") / release.name)
    r = _adopt(server, "--apply")
    assert r.exit_code == 0, r.output
    assert _has(r.output, "launches as pushed")
    assert targets.read(Path("."))["lvc"] == {"ssh": "svc@lvc", "dest": str(server)}
    assert history.read(Path("."))[0]["release"] == release.name


def _pushed(release: Path) -> Path:
    (release / ".venv" / "bin").mkdir(parents=True)
    (release / ".venv" / "bin" / "uqs").write_text("#!/bin/bash\n")
    (release / ".venv" / "bin" / "uqs").chmod(0o755)
    (release / "deploy.env").write_text("")
    return release


def test_a_pushed_release_under_releases_without_current_is_linked_not_moved(server, tmp_path):
    """#2581: moving it broke its .venv's absolute shebangs."""
    release = _pushed(server / "releases" / "20261001T000000Z-abcdefabcdef")
    r = _adopt(server, "--release-dir", str(release), "--apply", "--yes")
    assert r.exit_code == 0, r.output
    assert sorted(p.name for p in (server / "releases").iterdir()) == [release.name]
    assert (server / "current").readlink() == Path("releases") / release.name
    assert history.read(Path("."))[0]["release"] == release.name


def test_a_pushed_release_elsewhere_is_refused_rather_than_moved(server, tmp_path, logged):
    release = _pushed(tmp_path / "elsewhere")
    before = _tree(tmp_path)
    r = _adopt(server, "--release-dir", str(release), "--apply", "--yes")
    assert r.exit_code == 1
    assert _has(r.output + "".join(logged), "is a pushed release, whose .venv would break")
    assert _tree(tmp_path) == before


def test_without_yes_it_asks_and_a_no_changes_nothing(server, tmp_path, logged, terminal):
    before = _tree(tmp_path)
    r = _adopt(server, "--launch-python", sys.executable, "--apply", input="n\n")
    assert r.exit_code == 1 and _has(r.output + "".join(logged), "nothing was changed")
    assert _tree(tmp_path) == before


def test_a_declared_name_is_refused(server, logged):
    (Path(os.environ[targets.DECLARATION_ENV])).write_text(
        f'[targets.lvc]\nssh = "x"\ndest = "{server}"\n'
    )
    r = _adopt(server)
    assert r.exit_code == 1 and "target lvc is already declared" in "".join(logged)


def test_a_relative_path_is_refused(server, logged):
    r = _adopt(server, "--launch-python", "bin/python3")
    assert r.exit_code == 1 and "must be an absolute path on the server" in "".join(logged)


def test_apply_onto_an_inspection_servers_ssh_and_dest_is_refused(
    server, tmp_path, logged, monkeypatch
):
    """#2231: adopting under a NEW name is still the inspection-only server -
    it is matched by its ssh and dest - and --yes does not get past it."""
    from uqs.deploy import adopt

    Path(os.environ[targets.DECLARATION_ENV]).write_text(
        f'[targets.prd]\nssh = "deploy@lvc"\ndest = "{server}"\ninspection = true\n'
    )

    def never(*_a, **_k):
        raise AssertionError("an inspection-only server was changed")

    monkeypatch.setattr(adopt, "apply", never)
    before = _tree(tmp_path)
    r = _adopt(server, "--launch-python", sys.executable, "--apply", "--yes")
    assert r.exit_code == 1, r.output
    said = "".join(logged)
    assert "target prd is in inspection mode" in said
    assert "`uqs target adopt --apply` is refused" in said
    assert _tree(tmp_path) == before
    # the dry run still reports what the server holds
    assert _adopt(server, "--launch-python", sys.executable).exit_code == 0
