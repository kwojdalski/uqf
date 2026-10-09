"""Deployment targets in a file (#873): its values go through make_config
like the flags', a flag given on the command line wins, and --target a,b
deploys host by host, stopping at the first failure."""

from __future__ import annotations

import dataclasses
import io
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from uqs import cli
from uqs.deploy import driver, targets
from uqs.deploy.config import DeployError, make_config

FILE = """
[targets.prod-a]
host = "deploy@prod-a"
remote_user = "svc"
dest = "/srv/uqf"
runtime = "crypto"
profile = "essential"
torq_home = "/opt/torq"
live = true
live_check = ["deals_db", "quotes_db"]
jobs = ["piggy_spread"]
launcher_env = ["KDBDB_ORG=desk"]

[targets.prod-b]
host = "deploy@prod-b"
dest = "/srv/uqf"
profile = "essential"
"""

FLAGS: dict[str, Any] = dict(
    host="deploy@prod-a", remote_user="svc", dest="/srv/uqf", profile="essential",
    torq_home="/opt/torq", live=True, live_check="deals_db,quotes_db", jobs="piggy_spread",
    launcher_env=["KDBDB_ORG=desk"],
)  # fmt: skip


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.delenv(targets.DECLARATION_ENV, raising=False)
    (tmp_path / targets.DECLARATION).write_text(FILE)
    return tmp_path


def _same(a, b) -> bool:
    skip = {"target", "sources", "runtime"}
    fields = [f.name for f in dataclasses.fields(a) if f.name not in skip]
    return all(getattr(a, f) == getattr(b, f) for f in fields)


def test_a_targets_values_make_the_config_its_flags_would(root):
    (cfg,) = targets.configs("prod-a", "x.tar.gz", {}, False, root)
    assert _same(cfg, make_config(artifact="x.tar.gz", **FLAGS))
    assert (cfg.target, cfg.runtime) == ("prod-a", "crypto")
    assert cfg.sources["host"] == "target prod-a"


def test_a_flag_wins_over_the_file_and_says_so(root):
    (cfg,) = targets.configs("prod-a", "x.tar.gz", {"profile": "fx"}, True, root)
    assert cfg.profile == "fx" and cfg.dry_run
    assert cfg.sources["profile"] == "--profile" and cfg.sources["dest"] == "target prod-a"


def test_the_files_values_are_checked_like_flags(root):
    (root / targets.DECLARATION).write_text(
        '[targets.bad]\nhost = "h"\ndest = "rel/dir"\nprofile = "p"\n'
    )
    with pytest.raises(DeployError, match="--dest 'rel/dir' must be an absolute path"):
        targets.configs("bad", "x", {}, False, root)


@pytest.mark.parametrize(
    ("text", "names", "message"),
    [
        ('[targets.a]\nhost = "h"\ndest = "/d"\nprofile = "p"\nhots = "x"\n', "a",
         "unknown setting.*hots"),
        ('[targets.a]\nhost = "h"\ndest = "/d"\nprofile = "p"\nartifact = "x"\n', "a",
         "unknown setting.*artifact"),
        ('[targets.a]\nhost = "h"\nprofile = "p"\n', "a", "target a sets no dest"),
        ('[targets.a]\nhost = "h"\ndest = "/d"\nprofile = "p"\n', "b", "no target b.*declares: a"),
        ("not toml [", "a", "not TOML"),
        ('[server]\nhost = "h"\n', "a", r"declare targets as \[targets.<name>\]"),
    ],
)  # fmt: skip
def test_a_bad_file_or_name_is_refused(root, text, names, message):
    (root / targets.DECLARATION).write_text(text)
    with pytest.raises(DeployError, match=message):
        targets.configs(names, "x", {}, False, root)


def test_no_file_is_refused_naming_where_it_was_looked_for(tmp_path, monkeypatch):
    monkeypatch.delenv(targets.DECLARATION_ENV, raising=False)
    with pytest.raises(DeployError, match=f"needs {tmp_path}/deploy_targets.toml"):
        targets.configs("a", "x", {}, False, tmp_path)


def test_the_file_can_live_elsewhere(tmp_path, monkeypatch):
    elsewhere = tmp_path / "ops" / "targets.toml"
    elsewhere.parent.mkdir()
    elsewhere.write_text(FILE)
    monkeypatch.setenv(targets.DECLARATION_ENV, str(elsewhere))
    assert [c.host for c in targets.configs("prod-b", "x", {}, False, tmp_path)] == [
        "deploy@prod-b"
    ]


# --------------------------------------------------------- through the command


def _push(monkeypatch, root, argv, codes):
    """Run `uqs deploy push` with driver.deploy replaced: each call records its
    Config and returns the next of `codes`."""
    from uqs.cli import deploy as cli_deploy

    seen = []
    answers = iter(codes)
    monkeypatch.setattr(cli_deploy, "repo_root", lambda: root)
    monkeypatch.setattr(
        driver, "deploy", lambda cfg, remote, **_k: seen.append(cfg) or next(answers)
    )
    r = CliRunner().invoke(cli.app, ["deploy", "push", "x.tar.gz", *argv])
    return r, seen


def test_targets_are_deployed_in_order(root, monkeypatch):
    r, seen = _push(monkeypatch, root, ["--target", "prod-a,prod-b"], [0, 0])
    assert r.exit_code == 0 and [c.host for c in seen] == ["deploy@prod-a", "deploy@prod-b"]


def test_a_failed_target_stops_the_rest(root, monkeypatch):
    r, seen = _push(monkeypatch, root, ["--target", "prod-a,prod-b"], [1])
    assert r.exit_code == 1 and [c.target for c in seen] == ["prod-a"]


def test_only_a_flag_given_on_the_command_line_overrides(root, monkeypatch):
    """--verify-timeout left at its default must not erase the file's value -
    and a flag given, even at the default's value, still wins."""
    (root / targets.DECLARATION).write_text(FILE + "verify_timeout = 600\n")
    _, seen = _push(monkeypatch, root, ["--target", "prod-b"], [0])
    assert seen[0].verify_timeout == 600
    _, seen = _push(monkeypatch, root, ["--target", "prod-b", "--verify-timeout", "180"], [0])
    assert seen[0].verify_timeout == 180 and seen[0].sources["verify_timeout"] == "--verify-timeout"


class Untouched:
    """A server that must not be reached: the refusal comes first."""

    def run(self, script, timeout, stage, as_login=False):
        raise AssertionError(f"reached the server at {stage}")

    def put(self, local, remote_path, timeout, stage):
        raise AssertionError("copied to the server")


def _artifact(tmp_path: Path) -> Path:
    """A small real artifact, built for the default (uqf) runtime."""
    from uqs.deploy import artifact, build

    root = tmp_path / "tree"
    (root / "src").mkdir(parents=True)
    (root / "src" / "init.q").write_text("/ q\n")
    py = tmp_path / "py"
    (py / "wheels").mkdir(parents=True)
    (py / "requirements.txt").write_text("")
    (py / "wheels" / "uqs-0.1.0-py3-none-any.whl").write_bytes(b"uqs")
    target = artifact.Target(os="linux", arch="x86_64", python="3.14")
    return build.build_artifact(
        root, tmp_path / "dist", rid="20261008T000000Z-0123456789ab", rev="0" * 16,
        dirty=False, files=["src/init.q"], target=target, python_dir=py,
    ).path  # fmt: skip


def test_a_target_whose_runtime_is_not_the_artifacts_is_refused(root, tmp_path):
    (cfg,) = targets.configs("prod-a", str(_artifact(tmp_path)), {}, True, root)
    with pytest.raises(DeployError, match="runs the crypto runtime, but .* was built for uqf"):
        driver.deploy(cfg, Untouched(), out=io.StringIO())


def test_the_plan_says_where_each_setting_came_from(root):
    (cfg,) = targets.configs("prod-b", "x", {"profile": "fx"}, True, root)
    lines = driver._source_lines(cfg)
    assert "settings: profile from --profile" in lines
    assert "settings: dest, host from target prod-b" in lines


def test_a_push_records_itself_and_registers_its_server(root, monkeypatch, tmp_path):
    """#956: a push by flags leaves a history entry and a target to drive it by."""
    from types import SimpleNamespace

    from uqs.cli import deploy as cli_deploy
    from uqs.deploy import history

    monkeypatch.delenv(targets.DECLARATION_ENV, raising=False)
    monkeypatch.delenv(history.HISTORY_ENV, raising=False)
    monkeypatch.setattr(cli_deploy, "repo_root", lambda: tmp_path)

    def deploy(cfg, remote, *, on_report=None, **_k):
        report = {"release": "r42", "revision": "abc", "profile": cfg.profile, "status": "deployed"}
        assert on_report is not None
        on_report(SimpleNamespace(as_dict=lambda: report))
        return 0

    monkeypatch.setattr(driver, "deploy", deploy)
    argv = ["deploy", "push", "x.tar.gz", "--host", "svc@uat.example", "--dest", "/srv/uat"]
    r = CliRunner().invoke(cli.app, [*argv, "--profile", "essential"])
    assert r.exit_code == 0, r.output
    (entry,) = history.read(tmp_path)
    assert (entry["target"], entry["release"]) == ("uat-example-uat", "r42")
    assert targets.read(tmp_path)["uat-example-uat"]["dest"] == "/srv/uat"
