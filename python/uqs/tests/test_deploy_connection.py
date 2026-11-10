"""`uqs deploy status|rollback|prune TARGET` (#1736): the server named once, in
deploy_targets.toml, rather than by --ssh/--dest/--remote-user on every
command - those flags kept as the escape for a server no target declares, and
declared once (cli/deploy_connection.py) so every command spells them alike."""

from __future__ import annotations

import json
from typing import Any, cast

import pytest
import typer.main
from typer.testing import CliRunner

from uqs import cli
from uqs.cli import deploy_connection, deploy_server
from uqs.deploy import targets

FILE = """
[targets.prod-a]
ssh = "deploy@prod-a"
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
        return 0, {"ok": True}

    monkeypatch.setattr(getattr(deploy_server, module), module, fake)
    monkeypatch.setattr(getattr(deploy_server, module), "render", lambda result: "rendered")
    r = CliRunner().invoke(cli.app, ["deploy", command, *argv, *extra])
    return r, seen


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_a_target_names_the_server(root, monkeypatch, command):
    r, seen = _run(monkeypatch, command, ["prod-a"])
    assert r.exit_code == 0, r.output
    (cfg,) = seen
    assert (cfg.ssh, cfg.dest, cfg.remote_user, cfg.connect_timeout) == (
        "deploy@prod-a",
        "/srv/uqf",
        "svc",
        7,
    )


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_a_flag_given_wins_over_the_targets_value(root, monkeypatch, command):
    r, seen = _run(monkeypatch, command, ["prod-a", "--dest", "/srv/other"])
    assert r.exit_code == 0, r.output
    assert (seen[0].ssh, seen[0].dest) == ("deploy@prod-a", "/srv/other")
    assert seen[0].connect_timeout == 7, "a flag left at its default does not override"


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_the_flags_still_name_a_server_no_target_declares(root, monkeypatch, command):
    r, seen = _run(monkeypatch, command, ["--ssh", "h", "--dest", "/opt/uqf"])
    assert r.exit_code == 0, r.output
    assert (seen[0].ssh, seen[0].dest, seen[0].remote_user) == ("h", "/opt/uqf", None)


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_half_a_server_is_refused_naming_the_missing_half(root, monkeypatch, command, log_text):
    r, seen = _run(monkeypatch, command, ["--ssh", "h"])
    assert r.exit_code == 2 and not seen, "a usage error (#1885)"
    assert "give a TARGET, or --dest" in log_text()


@pytest.mark.parametrize(
    ("command", "said"),
    [
        ("rollback", "name the server to roll back: `uqs deploy rollback TARGET`"),
        ("prune", "name the server to prune: `uqs deploy prune TARGET`"),
    ],
)
def test_a_command_that_changes_a_server_never_guesses_which(
    root, monkeypatch, command, said, log_text
):
    """#2243: rollback and prune stop processes and delete releases, so a bare call is a
    usage error - even with a deployment recorded, it is not the server they act on."""
    _deployed(root)
    r, seen = _run(monkeypatch, command, [])
    assert r.exit_code == 2 and not seen, "a usage error (#1885)"
    assert said in log_text() and "--ssh and --dest" in log_text()


def _deployed(root) -> None:
    entry = {"status": "deployed", "ssh": "deploy@last", "dest": "/srv/last", "target": None}
    (root / "deploy_history.json").write_text(json.dumps({"deployments": [entry]}))


def test_status_with_no_target_reads_the_most_recent_deployment(root, monkeypatch):
    """#2243: `deploy status` only reads a server, so - as `target check` does - with
    nothing named it reads the one deployed to last, as its help says."""
    _deployed(root)
    r, seen = _run(monkeypatch, "status", [])
    assert r.exit_code == 0, r.output
    assert (seen[0].ssh, seen[0].dest) == ("deploy@last", "/srv/last")


def test_status_with_no_target_and_no_deployment_is_refused(root, monkeypatch, log_text):
    r, seen = _run(monkeypatch, "status", [])
    assert r.exit_code == 1 and not seen, "nothing to fall back to: a refusal, not a usage error"
    assert "no deployment recorded" in log_text()


def test_an_unknown_target_is_refused_naming_the_known_ones(root, monkeypatch, log_text):
    r, seen = _run(monkeypatch, "status", ["prod-z"])
    assert r.exit_code == 1 and not seen
    assert "no target or deployed server prod-z" in log_text() and "prod-a" in log_text()


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


def test_rollback_names_its_release_with_release(root, monkeypatch):
    """#1768: `--to` ends a time range everywhere else, so rollback names its
    release with `--release`."""
    to = []
    monkeypatch.setattr(
        deploy_server.rollback,
        "rollback",
        lambda cfg, remote, **kw: (0, to.append(kw["to"]) or {"ok": True}),
    )
    monkeypatch.setattr(deploy_server.rollback, "render", lambda result: "rendered")
    release = "20260101T000000Z-abc123456789"
    r = CliRunner().invoke(cli.app, ["deploy", "rollback", "prod-a", "--release", release])
    assert r.exit_code == 0, r.output
    assert to == [release]
