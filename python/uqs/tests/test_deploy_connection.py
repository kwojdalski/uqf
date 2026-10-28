"""`uqs deploy status|rollback|prune TARGET` (#1736): the server named once, in
deploy_targets.toml, rather than by --ssh/--dest/--remote-user on every
command - those flags kept as the escape for a server no target declares, and
declared once (cli/deploy_connection.py) so every command spells them alike."""

from __future__ import annotations

from typing import Any, cast

import pytest
import typer.main
from typer.testing import CliRunner

from uqs import cli
from uqs.cli import deploy_connection, deploy_server
from uqs.deploy import targets

FILE = """
[targets.prod-a]
host = "deploy@prod-a"
remote_user = "svc"
dest = "/srv/uqf"
profile = "essential"
connect_timeout = 7
"""

#: Each server command, and the deploy function it hands its Config to.
COMMANDS = {
    "status": ("status", ["--no-health"]),
    "rollback": ("rollback", []),
    "prune": ("prune", ["--keep", "3"]),
}


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.delenv(targets.DECLARATION_ENV, raising=False)
    monkeypatch.delenv("UQS_DEPLOY_HISTORY", raising=False)
    (tmp_path / targets.DECLARATION).write_text(FILE)
    monkeypatch.setattr(deploy_connection, "repo_root", lambda: tmp_path)
    return tmp_path


def _run(monkeypatch, command: str, argv: list[str]):
    """`uqs deploy <command> ...` with the server step replaced: it records
    the Config and reports success."""
    module, extra = COMMANDS[command]
    seen = []

    def fake(cfg, remote, **_kw):
        seen.append(cfg)
        if command == "status":
            return {"health": {"checked": False, "passed": True}}
        return 0

    monkeypatch.setattr(getattr(deploy_server, module), module, fake)
    monkeypatch.setattr(deploy_server.status, "render", lambda result: "rendered")
    r = CliRunner().invoke(cli.app, ["deploy", command, *argv, *extra])
    return r, seen


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_a_target_names_the_server(root, monkeypatch, command):
    r, seen = _run(monkeypatch, command, ["prod-a"])
    assert r.exit_code == 0, r.output
    (cfg,) = seen
    assert (cfg.host, cfg.dest, cfg.remote_user, cfg.connect_timeout) == (
        "deploy@prod-a",
        "/srv/uqf",
        "svc",
        7,
    )


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_a_flag_given_wins_over_the_targets_value(root, monkeypatch, command):
    r, seen = _run(monkeypatch, command, ["prod-a", "--dest", "/srv/other"])
    assert r.exit_code == 0, r.output
    assert (seen[0].host, seen[0].dest) == ("deploy@prod-a", "/srv/other")
    assert seen[0].connect_timeout == 7, "a flag left at its default does not override"


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_the_flags_still_name_a_server_no_target_declares(root, monkeypatch, command):
    r, seen = _run(monkeypatch, command, ["--ssh", "h", "--dest", "/opt/uqf"])
    assert r.exit_code == 0, r.output
    assert (seen[0].host, seen[0].dest, seen[0].remote_user) == ("h", "/opt/uqf", None)


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_no_target_and_no_flags_is_refused(root, monkeypatch, command):
    r, seen = _run(monkeypatch, command, [])
    assert r.exit_code == 1 and not seen
    assert "give a TARGET, or --ssh and --dest" in r.output


def test_an_unknown_target_is_refused_naming_the_known_ones(root, monkeypatch):
    r, seen = _run(monkeypatch, "status", ["prod-z"])
    assert r.exit_code == 1 and not seen
    assert "no target or deployed server 'prod-z'" in r.output and "prod-a" in r.output


def test_the_connection_flags_read_the_same_wherever_they_are_accepted():
    """One helper declares them: every command that reaches a server takes the
    same names with the same help."""
    # typer vendors its own click, so the group is reached by attribute, not class
    deploy = cast(Any, typer.main.get_command(cli.app)).commands["deploy"]
    flags = ("--ssh", "--dest", "--remote-user", "--connect-timeout")
    seen = {}
    for name in ("push", "status", "rollback", "prune"):
        params = {p.opts[0]: p for p in deploy.commands[name].params}
        assert set(flags) <= set(params), name
        seen[name] = {f: params[f].help for f in flags}
    assert len({tuple(v.items()) for v in seen.values()}) == 1, seen


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_the_old_host_spelling_still_names_the_ssh_destination(root, monkeypatch, command):
    """#1768: `--host` is `--ssh` now, since every other --host is a q host;
    the old spelling parses for one release, saying what to type instead."""
    from uqs.cli import renames

    said: list[tuple[str, str]] = []
    monkeypatch.setattr(renames, "warn_renamed", lambda old, new: said.append((old, new)))
    r, seen = _run(monkeypatch, command, ["--host", "h", "--dest", "/opt/uqf"])
    assert r.exit_code == 0, r.output
    assert seen[0].host == "h"
    assert said == [("--host", "--ssh")]


def test_rollback_to_is_release_and_the_old_spelling_still_parses(root, monkeypatch):
    """#1768: `--to` ends a time range everywhere else, so rollback names its
    release with `--release`; `--to` parses for one release."""
    from uqs.cli import renames

    said: list[tuple[str, str]] = []
    monkeypatch.setattr(renames, "warn_renamed", lambda old, new: said.append((old, new)))
    to = []
    monkeypatch.setattr(
        deploy_server.rollback, "rollback", lambda cfg, remote, **kw: to.append(kw["to"]) or 0
    )
    release = "20260101T000000Z-abc123456789"
    for flag in ("--release", "--to"):
        r = CliRunner().invoke(cli.app, ["deploy", "rollback", "prod-a", flag, release])
        assert r.exit_code == 0, r.output
    assert to == [release, release]
    assert said == [("--to", "--release")]
