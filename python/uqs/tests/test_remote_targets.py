"""`uqs --target NAME ...` and `uqs target ...` (#956).

The remote command is run for real: a stand-in `ssh` on PATH executes the
string the real ssh would hand the remote login shell, against a fake
deployment (`<dest>/current` -> a release with a deploy.env and a
`.venv/bin/uqs` that echoes what it was given). So quoting, the deploy.env
load and the release check are all exercised, not just the argv built.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from uqs.cli import app
from uqs.cli import target as target_cli
from uqs.deploy import control, history, targets
from uqs.paths import UqsError

FAKE_SSH = """#!/bin/bash
# ${@: -1}, not ${args[-1]}: macOS's bash 3.2 has no negative array index.
cmd="${@: -1}"; host="${@: -2:1}"
[ "$host" = unreachable ] && { echo "ssh: connect to host unreachable" >&2; exit 255; }
exec bash -c "$cmd"
"""
FAKE_UQS = """#!/bin/bash
echo "release=$FAKE_RELEASE cwd=$(basename "$(pwd -P)")"
for a in "$@"; do echo "[$a]"; done
exit "${FAKE_EXIT:-0}"
"""


@pytest.fixture
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "ssh").write_text(FAKE_SSH)
    (bin_dir / "ssh").chmod(0o755)
    release = tmp_path / "srv" / "releases" / "r7"
    (release / ".venv" / "bin").mkdir(parents=True)
    (release / "deploy.env").write_text("export FAKE_RELEASE=r7\n")
    (release / ".venv" / "bin" / "uqs").write_text(FAKE_UQS)
    (release / ".venv" / "bin" / "uqs").chmod(0o755)
    (tmp_path / "srv" / "current").symlink_to(release)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    declared = tmp_path / "deploy_targets.toml"
    declared.write_text(
        "# the operator's own comment\n"
        f'[targets.uat]\nhost = "svc@uat.example"\ndest = "{tmp_path / "srv"}"\n\n'
        '[targets.down]\nhost = "unreachable"\ndest = "/opt/uqf"\n\n'
        f'[targets.bare]\nhost = "h"\ndest = "{tmp_path / "none"}"\n'
    )
    monkeypatch.setenv(targets.DECLARATION_ENV, str(declared))
    return tmp_path


def _run(name: str, args: list[str], **kw) -> subprocess.CompletedProcess:
    r = control.remote_for(name, Path("."))
    return subprocess.run(
        control.ssh_argv(r, args, tty=False), capture_output=True, text=True, **kw
    )


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["--target", "uat", "summary"], ("uat", False, ["summary"])),
        (
            ["--debug", "--target=uat", "--yes", "stop", "x"],
            ("uat", True, ["--debug", "stop", "x"]),
        ),
        (
            ["--runtime", "fx", "--target", "uat", "logs"],
            ("uat", False, ["--runtime", "fx", "logs"]),
        ),
        # after the command it is the command's own, and nothing is forwarded
        (["query", "--target", "x"], (None, False, ["query", "--target", "x"])),
        (["summary"], (None, False, ["summary"])),
    ],
)
def test_the_target_is_read_from_the_global_options_only(argv, expected):
    assert control.split_global(argv) == expected


@pytest.mark.parametrize(
    ("args", "changes"),
    [
        (["summary"], False),
        (["logs", "rdb1", "-f"], False),
        (["query", "--proc", "rdb1", "1+1"], False),
        (["run", "show", "abc"], False),
        (["config", "sources", "check"], False),
        (["--runtime", "fx", "graph"], False),
        (["start", "--help"], False),
        (["start", "piggy1"], True),
        (["stop"], True),
        (["run", "migrate"], True),
        (["config", "set", "rdb1", "port", "1"], True),
        (["config", "sources", "stub"], True),
        (["data", "seed"], True),
        (["some-command-added-later"], True),
    ],
)
def test_what_changes_the_server_asks_first(args, changes):
    assert control.changes_the_server(args) is changes


def test_every_read_only_entry_names_a_real_command():
    """A renamed command must not leave a stale entry that silently classifies
    nothing - nor one that classifies a new, writing command as read-only."""
    runner = CliRunner()
    for command, sub in sorted(control.READ_ONLY, key=str):
        words = [command, *([sub] if sub else [])]
        result = runner.invoke(app, [*words, "--help"])
        assert result.exit_code == 0, f"uqs {' '.join(words)}: {result.output}"


def test_arguments_reach_the_release_intact_with_its_deploy_env(server):
    expr = 'select [20] from t where sym=`a, x like "it\'s" ; $(rm -rf /)'
    done = _run("uat", ["query", "--proc", "rdb1", expr])
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines() == [
        "release=r7 cwd=r7",
        "[query]",
        "[--proc]",
        "[rdb1]",
        f"[{expr}]",
    ]


def test_the_remote_exit_code_is_passed_through(server, monkeypatch):
    monkeypatch.setenv("FAKE_EXIT", "3")
    r = control.remote_for("uat", Path("."))
    assert control.run(r, ["summary"], tty=False) == 3


def test_an_unreachable_target_says_ssh_failed(server):
    with pytest.raises(UqsError, match="could not reach target down .* over ssh"):
        control.run(control.remote_for("down", Path(".")), ["summary"], tty=False)


def test_a_target_without_a_release_says_so(server):
    with pytest.raises(UqsError, match="target bare has no deployed release"):
        control.run(control.remote_for("bare", Path(".")), ["summary"], tty=False)
    with pytest.raises(UqsError, match="target bare has no deployed release"):
        control.current_release(control.remote_for("bare", Path(".")))


def test_check_names_the_running_release(server):
    assert control.current_release(control.remote_for("uat", Path("."))) == "r7"


def test_an_unknown_target_lists_the_declared_ones(server):
    with pytest.raises(UqsError, match="'nope' - targets in .*: bare, down, uat"):
        control.remote_for("nope", Path("."))


def test_a_service_account_runs_it_as_push_does():
    r = control.Remote("uat", "h", "/srv/uqf", remote_user="svc")
    assert control.remote_command(r, ["summary"]).startswith("sudo -n -iu svc bash -c ")


def test_ssh_never_prompts_and_never_skips_host_key_checks():
    argv = control.ssh_argv(control.Remote("uat", "h", "/srv"), ["summary"], tty=True)
    assert argv[:6] == ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-t"]
    assert not any("StrictHostKeyChecking" in a for a in argv)


def test_a_change_without_a_terminal_needs_yes(server):
    with pytest.raises(UqsError, match="may change uat .* pass --yes"):
        target_cli.forward("uat", False, ["stop", "rdb1"], interactive=False)


def test_a_change_with_yes_runs(server, capfd):
    assert target_cli.forward("uat", True, ["stop", "rdb1"], interactive=False) == 0
    assert "[stop]" in capfd.readouterr().out


def test_a_declined_confirmation_runs_nothing(server, monkeypatch):
    monkeypatch.setattr(target_cli.typer, "confirm", lambda *_a, **_k: False)
    with pytest.raises(UqsError, match="not run"):
        target_cli.forward("uat", False, ["stop"], interactive=True)


def test_target_management_is_never_forwarded(server):
    with pytest.raises(UqsError, match="manages this machine's targets"):
        target_cli.forward("uat", True, ["target", "list"], interactive=False)


def test_add_appends_and_remove_deletes_only_that_table(server):
    declared = Path(os.environ[targets.DECLARATION_ENV])
    before = declared.read_text()
    runner = CliRunner()
    added = runner.invoke(
        app, ["target", "add", "prod", "--ssh", "svc@prod", "--dest", "/srv/uqf",
              "--remote-user", "efx", "--label", "env=prod"],
    )  # fmt: skip
    assert added.exit_code == 0, added.output
    assert targets.read(Path("."))["prod"] == {
        "host": "svc@prod",
        "dest": "/srv/uqf",
        "remote_user": "efx",
        "labels": {"env": "prod"},
    }
    assert runner.invoke(app, ["target", "add", "prod", "--ssh", "x", "--dest", "/x"]).exit_code
    removed = runner.invoke(app, ["target", "remove", "prod"])
    assert removed.exit_code == 0, removed.output
    assert declared.read_text().rstrip("\n") == before.rstrip("\n"), "comments and others kept"


def test_add_refuses_a_destination_that_could_reach_a_shell(server):
    declared = Path(os.environ[targets.DECLARATION_ENV])
    before = declared.read_text()
    bad = CliRunner().invoke(
        app, ["target", "add", "x", "--ssh", "-oProxyCommand=sh", "--dest", "/d"]
    )
    assert bad.exit_code and declared.read_text() == before


def test_a_pushed_target_may_carry_labels(server, tmp_path):
    declared = Path(os.environ[targets.DECLARATION_ENV])
    declared.write_text(
        '[targets.prod]\nhost = "h"\ndest = "/srv"\nprofile = "essential"\n'
        'labels = { env = "prod" }\n'
    )
    (cfg,) = targets.configs("prod", str(tmp_path / "a.tar.gz"), {}, True, tmp_path)
    assert cfg.target == "prod"


def test_target_through_the_app_is_refused_rather_than_run_here():
    """Only the entry point forwards; reached any other way, --target must not
    run the command on this machine."""
    result = CliRunner().invoke(app, ["--target", "uat", "summary"])
    assert result.exit_code == 1


def test_the_entry_point_forwards(server):
    env = {**os.environ, "PYTHONPATH": str(Path(control.__file__).parents[2])}
    done = subprocess.run(
        [
            sys.executable,
            "-c",
            "from uqs.cli import main; main()",
            "--target",
            "uat",
            "logs",
            "rdb1",
        ],
        capture_output=True,
        text=True,
        env=env,
        stdin=subprocess.DEVNULL,
        check=False,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert "[logs]" in done.stdout and "[rdb1]" in done.stdout


# ------------------------------------------------ history and defaults


def _cfg(host: str, dest: str, target: str | None = None, user: str | None = None):
    from types import SimpleNamespace

    return SimpleNamespace(
        host=host, dest=dest, remote_user=user, target=target, runtime="uqf", profile="essential"
    )


def _report(release: str, status: str = "deployed"):
    from types import SimpleNamespace

    return SimpleNamespace(
        as_dict=lambda: {
            "release": release, "revision": "abc", "profile": "essential",
            "status": status, "stage": "done" if status == "deployed" else "verify", "error": "",
        }
    )  # fmt: skip


def test_a_push_is_recorded_and_its_server_registered(server):
    root = Path(".")
    history.record(root, _cfg("svc@new.example", "/srv/new", user="efx"), _report("r1"))
    (entry,) = history.read(root)
    assert (entry["target"], entry["release"], entry["status"]) == (
        "new-example-new",
        "r1",
        "deployed",
    )
    assert targets.read(root)["new-example-new"] == {
        "host": "svc@new.example",
        "dest": "/srv/new",
        "remote_user": "efx",
    }
    # a second push there reuses that target rather than declaring another
    history.record(root, _cfg("svc@new.example", "/srv/new", user="efx"), _report("r2"))
    assert sorted(targets.read(root)) == ["bare", "down", "new-example-new", "uat"]
    assert [e["target"] for e in history.read(root)] == ["new-example-new", "new-example-new"]


def test_a_failed_push_is_recorded_and_registers_nothing(server):
    root = Path(".")
    history.record(root, _cfg("svc@other", "/srv/o"), _report("r1", status="failed"))
    assert history.read(root)[0]["status"] == "failed"
    assert "other" not in targets.read(root)


def test_a_push_to_a_declared_target_keeps_its_name(server):
    root = Path(".")
    uat = targets.read(root)["uat"]
    history.record(root, _cfg(uat["host"], uat["dest"]), _report("r9"))
    assert history.read(root)[0]["target"] == "uat"


def test_a_server_resolves_to_its_latest_successful_deployment(server):
    root = Path(".")
    history.record(root, _cfg("svc@box", "/srv/a"), _report("r1"))
    history.record(root, _cfg("svc@box", "/srv/b"), _report("r2"))
    history.record(root, _cfg("svc@box", "/srv/c"), _report("r3", status="failed"))
    r = control.remote_for("box", root)
    assert (r.host, r.dest) == ("svc@box", "/srv/b"), "the latest that DEPLOYED"
    assert control.remote_for(None, root).dest == "/srv/b", "no name: the latest of all"
    assert history.defaults(root) == {0, 1}, "each host and dest's latest is a default"


def test_with_nothing_deployed_no_name_is_refused(server):
    with pytest.raises(UqsError, match="no deployment recorded"):
        control.remote_for(None, Path("."))


def test_recording_never_fails_the_deployment(server, monkeypatch):
    monkeypatch.setenv(history.HISTORY_ENV, "/proc/no/such/dir/history.json")
    history.record(Path("."), _cfg("svc@x", "/srv/x"), _report("r1"))  # warns, does not raise


def test_deploy_list_marks_each_servers_default(server):
    root = Path(".")
    history.record(root, _cfg("svc@box", "/srv/a"), _report("r1"))
    history.record(root, _cfg("svc@box", "/srv/a"), _report("r2"))
    out = CliRunner().invoke(app, ["deploy", "list"], terminal_width=200).output
    lines = [ln for ln in out.splitlines() if " r1 " in ln or " r2 " in ln]
    assert "*" in lines[0] and "r2" in lines[0], "newest first, and it is the default"
    assert "*" not in lines[1]


def test_shell_opens_in_the_release_with_its_environment(server):
    r = control.remote_for("uat", Path("."))
    script = control.shell_command(r).replace(
        'exec "${SHELL:-bash}" -i', 'echo "$UQS_TARGET $FAKE_RELEASE"; command -v uqs'
    )
    done = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    assert "uat r7" in done.stdout and "/current/.venv/bin/uqs" in done.stdout
