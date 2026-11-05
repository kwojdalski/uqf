"""A target's command_timeout and verify_timeout reach every deploy command
that reaches its server (#1892): push, rollback and prune alike, and the
`uqs deploy check --deadline` push and rollback run there. And each deploy
option's CLI default is make_config's own, or None so make_config supplies it:
a default written twice is one that drifts."""

from __future__ import annotations

import inspect
from typing import Any, cast

import pytest
import typer.main
from typer.testing import CliRunner

from uqs import cli
from uqs.cli import deploy as deploy_cli
from uqs.cli import deploy_connection, deploy_server
from uqs.deploy import config, stages, targets
from uqs.deploy.selection import Selection

FILE = """
[targets.slow]
ssh = "deploy@slow"
dest = "/srv/uqf"
profile = "essential"
command_timeout = 1800
verify_timeout = 600
"""


class _Checked(Exception):
    """Raised by the fake transport once it has seen the check command."""


class _Transport:
    """Records each remote command and its timeout, then stops the stage."""

    def __init__(self) -> None:
        self.runs: list[tuple[str, int]] = []

    def run(self, command, timeout, stage, **_kw):
        self.runs.append((command, timeout))
        raise _Checked


def _check_command(cfg: config.Config) -> tuple[str, int]:
    """The `uqs deploy check` a deployment of `cfg` runs on the server, and how
    long it waits for it: push's verify stage, which a rollback runs too."""
    transport = _Transport()
    with pytest.raises(_Checked):
        stages.Deployment(cfg, cast(Any, transport)).verify("r1", sel=Selection())
    (run,) = transport.runs
    return run


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.delenv(targets.DECLARATION_ENV, raising=False)
    monkeypatch.setenv("UQS_DEPLOY_HISTORY", str(tmp_path / "history.json"))
    (tmp_path / targets.DECLARATION).write_text(FILE)
    monkeypatch.setattr(deploy_connection, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(deploy_cli, "repo_root", lambda: tmp_path)
    return tmp_path


def _record(monkeypatch) -> dict[str, config.Config]:
    """Stub each command's server step: it records the Config it was given."""
    seen: dict[str, config.Config] = {}

    def fake(name):
        def step(cfg, remote, **_kw):
            seen[name] = cfg
            # push's driver returns its code; rollback and prune their document too
            return 0 if name == "push" else (0, {"ok": True})

        return step

    monkeypatch.setattr(deploy_cli.driver, "deploy", fake("push"))
    monkeypatch.setattr(deploy_server.rollback, "rollback", fake("rollback"))
    monkeypatch.setattr(deploy_server.prune, "prune", fake("prune"))
    monkeypatch.setattr(deploy_server.rollback, "render", lambda doc: "rolled back")
    monkeypatch.setattr(deploy_server.prune, "render", lambda doc: "pruned")
    return seen


def test_a_targets_timeouts_reach_push_rollback_prune_and_check(root, monkeypatch):
    seen = _record(monkeypatch)
    runner = CliRunner()
    for argv in (
        ["deploy", "push", "release.tar.gz", "slow"],
        ["deploy", "rollback", "slow"],
        ["deploy", "prune", "slow", "--keep", "3"],
    ):
        r = runner.invoke(cli.app, argv)
        assert r.exit_code == 0, (argv, r.output)
    timeouts = {name: (c.command_timeout, c.verify_timeout) for name, c in seen.items()}
    assert timeouts == {"push": (1800, 600), "rollback": (1800, 600), "prune": (1800, 600)}
    # push and rollback both run the release's own check with the target's deadline
    for name in ("push", "rollback"):
        command, waited = _check_command(seen[name])
        assert "--deadline 600" in command, (name, command)
        assert waited == 600 + 60, name


def test_a_flag_given_still_wins_over_the_targets_timeout(root, monkeypatch):
    seen = _record(monkeypatch)
    r = CliRunner().invoke(
        cli.app, ["deploy", "rollback", "slow", "--command-timeout", "60", "--verify-timeout", "30"]
    )
    assert r.exit_code == 0, r.output
    assert (seen["rollback"].command_timeout, seen["rollback"].verify_timeout) == (60, 30)


def test_with_no_target_the_timeouts_are_make_configs(root, monkeypatch):
    seen = _record(monkeypatch)
    defaults = inspect.signature(config.make_config).parameters
    runner = CliRunner()
    for argv in (
        ["deploy", "push", "release.tar.gz", "--ssh", "h", "--dest", "/opt/uqf", "--profile", "p"],
        ["deploy", "rollback", "--ssh", "h", "--dest", "/opt/uqf"],
        ["deploy", "prune", "--ssh", "h", "--dest", "/opt/uqf", "--keep", "3"],
    ):
        r = runner.invoke(cli.app, argv)
        assert r.exit_code == 0, (argv, r.output)
    for name, cfg in seen.items():
        for key in ("connect_timeout", "command_timeout", "verify_timeout"):
            assert getattr(cfg, key) == defaults[key].default, (name, key)


#: Deploy options that are not make_config's own, and the parameter they set
#: when they do set one: check's --deadline is the verify_timeout push passes it.
RENAMED = {("check", "deadline"): "verify_timeout"}


def test_every_deploy_options_default_is_none_or_make_configs():
    defaults = {k: p.default for k, p in inspect.signature(config.make_config).parameters.items()}
    group = cast(Any, typer.main.get_command(cli.app)).commands["deploy"]
    checked = []
    for name, command in group.commands.items():
        for param in command.params:
            key = RENAMED.get((name, param.name), param.name)
            if key not in defaults or param.default is None:
                continue
            checked.append((name, param.name))
            assert param.default == defaults[key], (
                f"uqs deploy {name} {param.opts[0]} defaults to {param.default!r}, "
                f"make_config's {key} to {defaults[key]!r}"
            )
    assert ("check", "deadline") in checked
