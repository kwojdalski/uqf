"""`uqs deploy push` and `uqs deploy verify` (#773, #835), with no server.

ssh and scp are replaced by a fake that records every script and answers by
what the script does, so the stages, their order and their failures are
tested here; a real server is the integration check's job.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import typer
from typer.main import get_group

from uqs.cli.deploy import deploy_app
from uqs.deploy import artifact, driver, verify
from uqs.deploy import build as release_build
from uqs.deploy.config import Config, DeployError, make_config, redact
from uqs.deploy.remote import Remote, q
from uqs.deploy.server import PREFLIGHT
from uqs.deploy.stages import Deployment

ROOT = Path(__file__).resolve().parents[3]


_CLI = get_group(deploy_app)


def parse_args(argv: list[str]) -> Config:
    """The Config `uqs deploy push` makes of `argv`: parsed by the command's
    own options, checked by the make_config it calls."""
    return make_config(**_CLI.commands["push"].make_context("push", list(argv)).params)


ARGS = ["--host", "uqf-server", "--dest", "/opt/uqf", "--profile", "essential"]
BASE = ["dist/uqf-x.tar.gz", *ARGS]

#: What a healthy server says about itself in preflight.
SERVER = (
    "os=Linux\narch=x86_64\npython=3.14\n"
    "qhome=/opt/kx home\nqhome_from=svc's QHOME\nqcmd=/opt/kx home/bin/q\nqcmd_from=svc's QCMD\n"
)


def _done(stdout: str = "", rc: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], rc, stdout=stdout, stderr=stderr)


class FakeRemote:
    """Answers each script by the first rule whose marker it contains."""

    def __init__(self, rules: dict[str, subprocess.CompletedProcess] | None = None):
        self.rules = rules or {}
        self.scripts: list[tuple[str, str]] = []
        #: the scripts run as the ssh login user rather than the service user
        self.login: list[str] = []
        self.puts: list[str] = []

    def run(
        self, script: str, timeout: int, stage: str, as_login: bool = False
    ) -> subprocess.CompletedProcess:
        self.scripts.append((stage, script))
        if as_login:
            self.login.append(script)
        for marker, result in self.rules.items():
            if marker in script:
                return result
        return _done()

    def put(self, local: Path, remote_path: str, timeout: int, stage: str) -> None:
        self.puts.append(remote_path)

    def ran(self, text: str) -> bool:
        return any(text in s for _, s in self.scripts)

    def stage(self, name: str) -> list[str]:
        """Every script run at stage `name`, in order."""
        return [script for stage, script in self.scripts if stage == name]


def _verified(passed: bool, reason: str = "") -> subprocess.CompletedProcess:
    body = {"passed": passed, "reason": reason, "processes": [{"process": "rdb1", "ok": passed}]}
    marker = verify.OK_MARKER if passed else verify.FAILED_MARKER
    return _done(json.dumps(body) + "\n" + marker + "\n", rc=0 if passed else 1)


def _artifact(tmp_path: Path, **target) -> Path:
    """A small real artifact: three files, a requirements file and two wheels."""
    root = tmp_path / "tree"
    files = ["pyproject.toml", "src/init.q", "scripts/deploy_smoke.q"]
    for f in files:
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        (root / f).write_text(f"content of {f}\n")
    py = tmp_path / "py"
    (py / "wheels").mkdir(parents=True)
    (py / "requirements.txt").write_text("rich==15.0.0 --hash=sha256:00\n")
    (py / "wheels" / "uqs-0.1.0-py3-none-any.whl").write_bytes(b"uqs")
    (py / "wheels" / "rich-15.0.0-py3-none-any.whl").write_bytes(b"rich")
    t = artifact.Target(**{"os": "linux", "arch": "x86_64", "python": "3.14", **target})
    art = release_build.build_artifact(
        root,
        tmp_path / "dist",
        rid="20261007T000000Z-0123456789ab",
        rev="0123456789abcdef",
        dirty=False,
        files=files,
        target=t,
        python_dir=py,
    )
    return art.path


def _args(path: Path, *extra: str) -> list[str]:
    return [str(path), *ARGS, *extra]


# --- arguments and quoting ------------------------------------------------------


def test_the_required_arguments_are_host_dest_and_profile():
    with pytest.raises(typer.BadParameter, match="profile"):
        parse_args(["a", "--host", "h", "--dest", "/opt/uqf"])
    cfg = parse_args(BASE)
    assert (cfg.host, cfg.dest, cfg.profile) == ("uqf-server", "/opt/uqf", "essential")
    assert cfg.data_root == "/opt/uqf/shared/data"


@pytest.mark.parametrize(
    "dest", ["opt/uqf", "/", "/opt/uqf; rm -rf /", "/opt/../etc", "/opt//uqf", "/opt/u qf"]
)
def test_a_destination_that_is_not_a_plain_absolute_path_is_refused(dest):
    with pytest.raises(DeployError, match="--dest"):
        parse_args(["a", "--host", "h", "--dest", dest, "--profile", "essential"])


@pytest.mark.parametrize("host", ["-oProxyCommand=evil", "a b", "h;x"])
def test_a_host_that_could_be_read_as_an_option_is_refused(host):
    with pytest.raises(DeployError, match="--host"):
        # --host=VALUE: argparse alone would refuse "--host -o..." as a missing value.
        parse_args(["a", f"--host={host}", "--dest", "/opt/uqf", "--profile", "essential"])


def test_ssh_keeps_host_key_checking_and_never_prompts():
    remote = Remote("uqf-server", 7)
    argv = remote.ssh_argv()
    assert argv[:1] == ["ssh"] and "BatchMode=yes" in argv and "ConnectTimeout=7" in argv
    assert not any("StrictHostKeyChecking" in a or "UserKnownHostsFile" in a for a in argv)


def test_every_value_reaches_the_remote_shell_quoted():
    cfg = parse_args([*BASE, "--qcmd", "/opt/kx/bin/q", "--qhome", "/opt/kx"])
    text = Deployment(cfg, FakeRemote()).preflight_script()
    assert "dest=/opt/uqf\n" in text and "qcmd_flag=/opt/kx/bin/q\n" in text
    assert q("a b;c") == "'a b;c'"


def test_a_remote_step_that_overruns_its_timeout_fails_naming_the_stage():
    def runner(*_, **kwargs):
        raise subprocess.TimeoutExpired("ssh", kwargs["timeout"])

    remote = Remote("h", 5, runner=runner)
    with pytest.raises(DeployError, match="within 30s") as err:
        remote.run("true", 30, "prepare")
    assert err.value.stage == "prepare"


def test_secrets_are_masked_in_anything_printed():
    assert "hunter2" not in redact("DRIVER=x;PWD=hunter2 and password: s3cret")


# --- the artifact -------------------------------------------------------------


def test_the_artifact_is_required():
    with pytest.raises(typer.BadParameter, match="artifact"):
        parse_args(ARGS)


def test_a_tampered_artifact_is_refused_before_the_server_is_touched(tmp_path):
    path = _artifact(tmp_path)
    Path(f"{path}.sha256").write_text("0" * 64 + f"  {path.name}\n")
    remote = FakeRemote()
    with pytest.raises(DeployError, match="does not match") as err:
        driver.deploy(parse_args(_args(path)), remote)
    assert err.value.stage == "artifact" and remote.scripts == []


@pytest.mark.parametrize(
    ("facts", "said"),
    [
        ("os=Darwin\narch=x86_64\npython=3.14\n", "not linux"),
        ("os=Linux\narch=aarch64\npython=3.14\n", "built for x86_64"),
        ("os=Linux\narch=x86_64\npython=3.13\n", "wheels are for 3.14"),
        (
            "os=Linux\narch=x86_64\npython=3.14\nqversion=4.1\n",
            "kdb\\+ 4.1, which has no nested contexts.*--q-target 4.0",
        ),
    ],
)
def test_a_server_the_artifact_was_not_built_for_is_refused(tmp_path, facts, said):
    remote = FakeRemote({"uv python find": _done(facts + "data=present\n")})
    with pytest.raises(DeployError, match=said) as err:
        driver.deploy(parse_args(_args(_artifact(tmp_path))), remote)
    assert err.value.stage == "preflight"


def test_an_artifact_already_on_the_server_is_refused_in_preflight(tmp_path):
    remote = FakeRemote({"uv python find": _done(SERVER + "data=present\nrelease_exists=yes\n")})
    with pytest.raises(DeployError, match="already on uqf-server") as err:
        driver.deploy(parse_args(_args(_artifact(tmp_path))), remote)
    assert err.value.stage == "preflight" and "/opt/uqf/releases/2026" in remote.scripts[0][1]


def test_amd64_is_x86_64():
    target = {"os": "linux", "arch": "x86_64", "python": "3.14"}
    assert artifact.compatible(target, {"os": "Linux", "arch": "amd64", "python": "3.14"}) == []


def test_preflight_asks_uv_for_the_artifacts_python(tmp_path):
    remote = FakeRemote({"uv python find": _done(SERVER + "data=present\n")})
    driver.deploy(parse_args(_args(_artifact(tmp_path, python="3.14"), "--dry-run")), remote)
    assert (
        "python=3.14\n" in remote.scripts[0][1]
        and 'uv python find "$python"' in remote.scripts[0][1]
    )


# --- preflight and dry run ---------------------------------------------------


def test_a_missing_data_directory_needs_init_data(tmp_path):
    remote = FakeRemote({"uv python find": _done(SERVER + "data=absent\n")})
    with pytest.raises(DeployError, match="--init-data"):
        driver.deploy(parse_args(_args(_artifact(tmp_path))), remote)


def test_replacing_a_deployment_needs_restart(tmp_path):
    remote = FakeRemote({"uv python find": _done(SERVER + "data=present\ncurrent=OLD\n")})
    with pytest.raises(DeployError, match="--restart"):
        driver.deploy(parse_args(_args(_artifact(tmp_path))), remote)


def test_a_dry_run_changes_nothing_and_shows_the_plan(tmp_path):
    remote = FakeRemote({"uv python find": _done(SERVER + "data=present\ncurrent=OLD\n")})
    out = io.StringIO()
    cfg = parse_args(_args(_artifact(tmp_path), "--dry-run", "--restart"))
    assert driver.deploy(cfg, remote, out=out) == 0
    assert [stage for stage, _ in remote.scripts] == ["preflight"]
    assert remote.puts == []
    shown = out.getvalue()
    # three tree files, the requirements and two wheels
    assert "6 files" in shown and "stop release OLD's processes" in shown
    assert "offline install of 2 wheels" in shown and "linux/x86_64, Python 3.14" in shown


# --- the full run ---------------------------------------------------------------

_HEALTHY = {
    "uv python find": _done(SERVER + "data=present\n"),
    "deploy_smoke.q": _done("DEPLOY_SMOKE_OK\n"),
    "--ports-free": _done(json.dumps({"busy": {}}) + "\n" + verify.OK_MARKER + "\n"),
}


def _run(tmp_path, rules, args=()):
    remote = FakeRemote({**_HEALTHY, **rules})
    out = io.StringIO()
    code = driver.deploy(parse_args(_args(_artifact(tmp_path), *args)), remote, out=out)
    return code, remote, json.loads(out.getvalue())


def test_a_healthy_deployment_activates_only_after_verification(tmp_path):
    code, remote, report = _run(tmp_path, {"uqs deploy verify --profile": _verified(True)})
    assert code == 0 and report["status"] == "deployed"
    stages = [stage for stage, _ in remote.scripts]
    assert stages.index("verify") < stages.index("activate")
    assert remote.ran(".current.new") and remote.ran("rm -rf /opt/uqf/deploy.lock")


def test_the_release_environment_installs_offline_from_the_artifacts_wheels(tmp_path):
    _, remote, _ = _run(tmp_path, {"uqs deploy verify --profile": _verified(True)})
    prepare = next(s for stage, s in remote.scripts if stage == "prepare")
    assert "export UV_OFFLINE=1" in prepare and "uv sync" not in prepare
    assert "--no-index" in prepare and "--require-hashes -r .release/requirements.txt" in prepare
    assert "--no-deps .release/wheels/uqs-0.1.0-py3-none-any.whl" in prepare
    assert "uv venv --quiet --python 3.14 .venv" in prepare
    assert not remote.ran("uv run")


def test_the_archive_is_checked_on_the_server_before_extraction(tmp_path):
    rules = {"checksum mismatch": _done(rc=1, stderr="checksum mismatch: the archive arrived as x")}
    code, remote, report = _run(tmp_path, rules)
    assert code == 1 and report["stage"] == "transfer"
    assert not remote.ran("deploy_smoke.q") and not remote.ran(".current.new")


def test_a_failed_smoke_test_stops_before_anything_starts(tmp_path):
    code, remote, report = _run(tmp_path, {"deploy_smoke.q": _done("loaded\n")})
    assert code == 1 and report["stage"] == "smoke"
    assert not remote.ran("uqs start")


def test_failed_verification_stops_the_new_processes_and_never_activates(tmp_path):
    code, remote, report = _run(
        tmp_path, {"uqs deploy verify --profile": _verified(False, "no answer from rdb1")}
    )
    assert code == 1 and report["stage"] == "verify" and "rdb1" in report["error"]
    assert remote.ran("uqs stop all") and not remote.ran(".current.new")


def test_a_failed_upgrade_restores_the_previous_release(tmp_path):
    previous = json.dumps({"profile": "fx", "processes": [{"process": "rdb1"}]})
    rules = {
        "uv python find": _done(SERVER + "data=present\ncurrent=OLD\n"),
        "deploy-report.json\n": _done(previous),
        "readlink": _done("current=OLD\n"),
        "uqs deploy verify --profile": _verified(False, "no answer from rdb1"),
    }
    code, remote, report = _run(tmp_path, rules, args=["--restart"])
    assert code == 1
    assert remote.ran("cd /opt/uqf/releases/OLD") and remote.ran("uqs stop rdb1")
    assert remote.ran("uqs start --profile fx")
    assert "FAILED to verify release OLD" in report["rollback"]
    assert "current still names OLD" in report["rollback"]
    assert not remote.ran(".current.new")


def test_a_busy_port_fails_before_the_profile_starts(tmp_path):
    busy = _done(json.dumps({"busy": {"rdb1": 6052}}) + "\n" + verify.FAILED_MARKER + "\n", rc=1)
    code, remote, report = _run(tmp_path, {"--ports-free": busy})
    assert code == 1 and report["stage"] == "ports" and "rdb1 (6052)" in report["error"]
    assert not remote.ran("uqs start")


def test_runtime_data_is_never_touched_without_init_data(tmp_path):
    _, remote, _ = _run(tmp_path, {"uqs deploy verify --profile": _verified(True)})
    data = "/opt/uqf/shared/data"
    assert not any(f"rm -rf {data}" in s or f"mkdir -p {data}" in s for _, s in remote.scripts)


# --- deploy_verify ------------------------------------------------------------


def _query(answers: dict[int, dict[str, object]] | dict[int, Exception]):
    def query(expr: str, port: int):
        found = answers[port]
        if isinstance(found, Exception):
            raise found
        assert isinstance(found, dict)
        return found[expr]

    return query


def _healthy(name: str, library=0.0, etl=None) -> dict[str, object]:
    return {
        verify.IDENTITY_EXPR: name,
        verify.LIBRARY_EXPR: library or float("nan"),
        verify.ETL_EXPR: etl,
    }


def test_every_process_answering_as_itself_passes():
    answers = {
        6052: _healthy("rdb1"),
        6060: _healthy("posbook1", verify.LIBRARY_EXPECTED, True),
    }
    passed, results, _ = verify.verify(
        {"rdb1": 6052, "posbook1": 6060}, {"posbook1"}, _query(answers), 5
    )
    assert passed and [r.library for r in results] == ["not loaded", "ok"]


def test_a_port_held_by_another_process_fails():
    passed, _, why = verify.verify({"rdb1": 6052}, set(), _query({6052: _healthy("hdb1")}), 5)
    assert not passed and "rdb1" in why


def test_a_process_that_never_answers_fails_at_the_deadline():
    clock = iter(range(100)).__next__
    passed, _, why = verify.verify(
        {"rdb1": 6052},
        set(),
        _query({6052: ConnectionRefusedError()}),
        3,
        clock=lambda: float(clock()),
        sleep=lambda _: None,
    )
    assert not passed and "no answer within 3s from rdb1" in why


def test_a_pipeline_profile_without_an_etl_check_fails():
    passed, _, why = verify.verify(
        {"posbook1": 6060}, {"posbook1"}, _query({6060: _healthy("posbook1")}), 5
    )
    assert not passed and "ETL" in why


def test_a_wrong_library_answer_fails():
    answers = {6060: _healthy("posbook1", 2.0, True)}
    passed, results, _ = verify.verify({"posbook1": 6060}, {"posbook1"}, _query(answers), 5)
    assert not passed and results[0].library.startswith("wrong")


def test_busy_ports_are_the_ones_something_listens_on():
    assert verify.busy_ports({"a": 1, "b": 2}, lambda port: port == 2) == {"b": 2}


# --- a service user through sudo (#780) ----------------------------------------

UPLOAD = "/tmp/uqf-upload.Ab12Cd"
_AS_SVC = {
    "sudo -n -iu svc id -un": _done("svc\n"),
    "mktemp -d /tmp/uqf-upload": _done(UPLOAD + "\n"),
    "uv python find": _done("user=svc\n" + SERVER + "data=present\n"),
}


def _run_as(tmp_path, rules=(), args=()):
    remote = FakeRemote({**_AS_SVC, **_HEALTHY, **dict(rules)})
    remote.rules["uv python find"] = dict(rules).get("uv python find", _AS_SVC["uv python find"])
    out = io.StringIO()
    cfg = parse_args(_args(_artifact(tmp_path), "--remote-user", "svc", *args))
    code = driver.deploy(cfg, remote, out=out)
    return code, remote, out.getvalue()


@pytest.mark.parametrize("user", ["root;id", "Svc", "-n", "a b", "x" * 40, "svc$"])
def test_a_remote_user_that_is_not_an_account_name_is_refused(user):
    with pytest.raises(DeployError, match="--remote-user"):
        parse_args([*BASE, f"--remote-user={user}"])


def test_without_a_remote_user_ssh_runs_the_script_in_the_logins_own_login_shell():
    assert Remote("deploy@uqf-server", 5).ssh_argv()[-1] == "bash -l -s"


def test_with_a_remote_user_every_step_runs_through_non_interactive_sudo():
    remote = Remote("deploy@uqf-server", 5, remote_user="svc")
    assert remote.ssh_argv()[-2:] == ["deploy@uqf-server", "sudo -n -iu svc bash -s"]
    assert remote.ssh_argv(as_login=True)[-1] == "bash -s"


def test_a_missing_sudo_rule_fails_before_anything_changes(tmp_path):
    rules = {"sudo -n -iu svc id -un": _done(rc=1, stderr="sudo: a password is required")}
    with pytest.raises(DeployError, match="without a password") as err:
        _run_as(tmp_path, rules)
    assert err.value.stage == "preflight"


def test_sudo_landing_in_another_account_is_refused(tmp_path):
    with pytest.raises(DeployError, match="runs as root, not svc"):
        _run_as(tmp_path, {"sudo -n -iu svc id -un": _done("root\n")})


def test_preflight_checks_the_identity_it_runs_as(tmp_path):
    facts = _done("user=deploy\n" + SERVER + "data=present\n")
    with pytest.raises(DeployError, match="run as deploy, not svc"):
        _run_as(tmp_path, {"uv python find": facts})


def test_the_archive_reaches_the_service_user_through_a_private_upload(tmp_path):
    code, remote, _ = _run_as(tmp_path, {"uqs deploy verify --profile": _verified(True)})
    assert code == 0
    assert remote.puts == [f"{UPLOAD}/uqf-20261007T000000Z-0123456789ab.tar.gz"]
    handoff = next(s for s in remote.login if "handing" in s or "sudo -n -u svc" in s)
    assert "sudo -n -u svc -- python3 -c" in handoff and "'xb'" in handoff
    assert f"< {UPLOAD}/uqf-" in handoff
    assert not any("chmod" in s or "chown" in s for _, s in remote.scripts)
    assert any(f"rm -rf {UPLOAD}" in s for s in remote.login)


def test_the_upload_is_removed_when_the_handoff_fails(tmp_path):
    rules = {"sudo -n -u svc --": _done(rc=1, stderr="sudo: not allowed")}
    code, remote, _ = _run_as(tmp_path, rules)
    assert code == 1
    assert any(f"rm -rf {UPLOAD}" in s for s in remote.login)


def test_only_the_upload_steps_run_as_the_login_user(tmp_path):
    _, remote, _ = _run_as(tmp_path, {"uqs deploy verify --profile": _verified(True)})
    for s in remote.login:
        assert any(m in s for m in ("id -un", "mktemp -d", "sudo -n -u svc --", "rm -rf /tmp/"))
    service = [s for _, s in remote.scripts if s not in remote.login]
    assert any("uqs start --profile essential" in s for s in service)
    assert any(".current.new" in s for s in service)


def test_settings_cross_sudo_inside_the_script_not_the_environment(tmp_path):
    _, remote, _ = _run_as(tmp_path, {"uqs deploy verify --profile": _verified(True)})
    pre = next(s for st, s in remote.scripts if st == "preflight" and "uv python find" in s)
    assert "dest=/opt/uqf\n" in pre and "expected_user=svc\n" in pre


def test_a_dry_run_as_a_service_user_says_who_does_what(tmp_path):
    code, remote, shown = _run_as(tmp_path, args=["--dry-run"])
    assert (
        code == 0
        and remote.puts == []
        and remote.login == ["set -euo pipefail\nsudo -n -iu svc id -un\n"]
    )
    assert "every step below as svc (sudo -n -iu svc)" in shown
    assert "private upload directory" in shown


# --- QHOME and QCMD from the deploying account (#782) ----------------------------


def test_q_flags_are_optional_and_may_hold_spaces():
    cfg = parse_args(BASE)
    assert cfg.qcmd is None and cfg.qhome is None
    cfg = parse_args([*BASE, "--qhome", "/opt/kx home", "--qcmd", "/opt/kx home/bin/q"])
    assert (cfg.qhome, cfg.qcmd) == ("/opt/kx home", "/opt/kx home/bin/q")


@pytest.mark.parametrize("value", ["bin/q", "/opt/../q", "/opt/kx\nq"])
def test_an_explicit_q_path_must_be_absolute_and_plain(value):
    with pytest.raises(DeployError, match="--qcmd"):
        parse_args([*BASE, "--qcmd", value])


def test_the_local_environment_never_reaches_the_server(monkeypatch):
    monkeypatch.setenv("QHOME", "/home/me/local-kx")
    monkeypatch.setenv("QCMD", "/home/me/local-kx/q")
    text = Deployment(parse_args(BASE), FakeRemote()).preflight_script()
    assert "local-kx" not in text
    assert "qhome_flag=''\n" in text and "qcmd_flag=''\n" in text


def test_explicit_flags_reach_preflight_quoted():
    cfg = parse_args([*BASE, "--qhome", "/opt/kx home"])
    text = Deployment(cfg, FakeRemote()).preflight_script()
    assert "qhome_flag='/opt/kx home'\n" in text and "qcmd_flag=''\n" in text


def test_preflight_resolves_from_the_deploying_accounts_environment():
    text = Deployment(parse_args(BASE), FakeRemote()).preflight_script()
    assert 'elif [ -n "${QHOME:-}" ]' in text and 'elif [ -n "${QCMD:-}" ]' in text
    assert 'command -v -- "$qcmd_want"' in text and "pass --qhome" in text


def test_the_resolved_q_is_what_the_release_runs_with(tmp_path):
    _, remote, _ = _run(tmp_path, {"uqs deploy verify --profile": _verified(True)})
    prepare = next(s for stage, s in remote.scripts if stage == "prepare")
    assert "export QHOME='/opt/kx home'" in prepare
    assert "export QCMD='/opt/kx home/bin/q'" in prepare
    smoke = next(s for stage, s in remote.scripts if stage == "smoke")
    assert "source ./deploy.env" in smoke and '"$QCMD" scripts/deploy_smoke.q' in smoke


def test_a_preflight_that_reports_no_q_is_refused(tmp_path):
    remote = FakeRemote({"uv python find": _done("os=Linux\narch=x86_64\npython=3.14\n")})
    with pytest.raises(DeployError, match="did not report the q"):
        driver.deploy(parse_args(_args(_artifact(tmp_path))), remote)


def test_a_rollback_uses_the_previous_releases_own_settings(tmp_path):
    previous = json.dumps({"profile": "fx", "processes": [{"process": "rdb1"}]})
    rules = {
        "uv python find": _done(SERVER + "data=present\ncurrent=OLD\n"),
        "deploy-report.json\n": _done(previous),
        "readlink": _done("current=OLD\n"),
        "uqs deploy verify --profile": _verified(False, "no answer"),
    }
    _, remote, _ = _run(tmp_path, rules, args=["--restart"])
    restart = next(s for st, s in remote.scripts if st == "rollback" and "start --profile fx" in s)
    assert "cd /opt/uqf/releases/OLD\nsource ./deploy.env" in restart


def test_the_dry_run_names_where_each_q_setting_came_from(tmp_path):
    remote = FakeRemote({"uv python find": _done(SERVER + "data=present\n")})
    out = io.StringIO()
    driver.deploy(parse_args(_args(_artifact(tmp_path), "--dry-run")), remote, out=out)
    assert "QHOME=/opt/kx home (svc's QHOME)" in out.getvalue()
    assert "resolved and run once in preflight" in out.getvalue()


def test_help_states_the_resolution_rules():
    shown = " ".join(
        " ".join(getattr(p, "help", None) or "" for p in _CLI.commands["push"].params).split()
    )
    assert "deploying account's $QHOME" in shown and "never this machine's" in shown


# --- hardening: lock, readiness, report, rollback ---------------------------------


class SequencedRemote(FakeRemote):
    """A FakeRemote whose rule for `marker` answers differently on later calls."""

    def __init__(self, rules, marker, answers):
        super().__init__(rules)
        self.marker, self.answers = marker, list(answers)

    def run(self, script, timeout, stage, as_login=False):
        if self.marker in script and self.answers:
            self.scripts.append((stage, script))
            return self.answers.pop(0)
        return super().run(script, timeout, stage, as_login)


def test_a_release_activated_meanwhile_without_restart_is_refused_under_the_lock(tmp_path):
    remote = FakeRemote({**_HEALTHY, "readlink": _done("current=OLD\n")})
    with pytest.raises(DeployError, match="now runs release OLD") as err:
        driver.deploy(parse_args(_args(_artifact(tmp_path))), remote, out=io.StringIO())
    assert err.value.stage == "lock"
    assert remote.puts == [] and remote.ran("rm -rf /opt/uqf/deploy.lock")


def test_with_restart_the_release_current_under_the_lock_is_the_one_stopped(tmp_path):
    previous = json.dumps({"profile": "fx", "processes": [{"process": "rdb9"}]})
    rules = {
        "readlink": _done("current=NEWER\n"),
        "deploy-report.json\n": _done(previous),
        "uqs deploy verify --profile": _verified(True),
    }
    code, remote, report = _run(tmp_path, rules, args=["--restart"])
    assert code == 0 and report["previous_release"] == "NEWER"
    assert remote.ran("cd /opt/uqf/releases/NEWER") and remote.ran("uqs stop rdb9")


def test_the_report_is_written_before_activation(tmp_path):
    code, remote, _ = _run(tmp_path, {"uqs deploy verify --profile": _verified(True)})
    stages = [st for st, _ in remote.scripts]
    assert code == 0 and stages.index("report") < stages.index("activate")


def test_a_report_that_cannot_be_written_fails_the_deployment_and_never_activates(tmp_path):
    rules = {
        "uqs deploy verify --profile": _verified(True),
        "DEPLOYREPORT": _done(rc=1, stderr="No space left on device"),
    }
    code, remote, report = _run(tmp_path, rules)
    assert code == 1 and report["stage"] == "report"
    assert not remote.ran(".current.new") and remote.ran("uqs stop all")


def test_a_recovered_previous_release_is_verified_with_its_own_verifier(tmp_path):
    previous = json.dumps({"profile": "fx", "processes": [{"process": "rdb1"}]})
    rules = {
        "uv python find": _done(SERVER + "data=present\ncurrent=OLD\n"),
        "readlink": _done("current=OLD\n"),
        "deploy-report.json\n": _done(previous),
    }
    remote = SequencedRemote(
        {**_HEALTHY, **rules},
        "uqs deploy verify --profile",
        [_verified(False, "no answer from rdb1"), _verified(True)],
    )
    out = io.StringIO()
    code = driver.deploy(parse_args(_args(_artifact(tmp_path), "--restart")), remote, out=out)
    report = json.loads(out.getvalue())
    assert code == 1 and "restarted and verified release OLD's profile fx" in report["rollback"]
    check = [s for st, s in remote.scripts if st == "rollback" and "uqs deploy verify" in s]
    assert check and "cd /opt/uqf/releases/OLD" in check[0] and "--profile fx" in check[0]


# --- deploy_verify: every pipeline process is checked ------------------------------


def test_a_pipeline_process_missing_its_library_or_etl_fails_even_if_another_passes():
    answers = {
        6060: _healthy("posbook1", verify.LIBRARY_EXPECTED, True),
        6061: _healthy("flow1"),  # a pipeline process with neither loaded
    }
    passed, results, why = verify.verify(
        {"posbook1": 6060, "flow1": 6061}, {"posbook1", "flow1"}, _query(answers), 5
    )
    assert not passed and "flow1" in why and "library and ETL" in why
    assert [r.ok for r in results] == [True, False]


def test_a_pipeline_process_with_the_library_but_no_etl_fails():
    answers = {6060: _healthy("posbook1", verify.LIBRARY_EXPECTED, None)}
    passed, _, why = verify.verify({"posbook1": 6060}, {"posbook1"}, _query(answers), 5)
    assert not passed and "ETL check not ok" in why


def test_a_non_pipeline_process_without_the_library_still_passes():
    passed, _, _ = verify.verify({"rdb1": 6052}, {"posbook1"}, _query({6052: _healthy("rdb1")}), 5)
    assert passed


# --- a site-managed TorQ launcher outside TORQHOME ------------------------------

SITE = [
    "--torq-home",
    "/opt/site/torq/core/current",
    "--torq-app-home",
    "/opt/site/torq/TorQApp",
    "--torq-launcher",
    "/opt/site/torq/bin/torq.sh",
]


def _torq_checks(text: str) -> str:
    """PREFLIGHT's TorQ checks alone, so they can run without q or uv."""
    start = text.index('if [ -n "$torq_launcher" ]')
    return text[start : text.index('if [ -n "$torq_app_home" ]')]


def _run_torq_checks(torq_home: Path, launcher: str) -> subprocess.CompletedProcess:
    body = f"torq_home={torq_home}\ntorq_launcher={launcher}\n"
    body += 'fail() { echo "$*" >&2; exit 1; }\n' + _torq_checks(PREFLIGHT) + "echo ok\n"
    return subprocess.run(["bash", "-c", body], capture_output=True, text=True, check=False)


def test_the_launcher_and_its_variables_parse():
    cfg = parse_args([*BASE, *SITE, "--launcher-env", "KDBDB_ORG=uqf desk"])
    assert cfg.torq_launcher == "/opt/site/torq/bin/torq.sh"
    assert cfg.launcher_env == {"KDBDB_ORG": "uqf desk"}


@pytest.mark.parametrize("value", ["bin/torq.sh", "torq.sh", "/opt/../torq.sh", "/opt/t q.sh"])
def test_a_launcher_that_is_not_a_plain_absolute_path_is_refused(value):
    with pytest.raises(DeployError, match="--torq-launcher .* must be an absolute path"):
        parse_args([*BASE, "--torq-launcher", value])


@pytest.mark.parametrize(
    ("item", "message"),
    [
        ("TORQHOME=/x", "set by the deployment itself"),
        ("SETENV=/x", "set by the deployment itself"),
        ("TORQPROCESSES=/x", "set by the deployment itself"),
        ("UQS_DATA_ROOT=/x", "set by the deployment itself"),
        ("KDBDB_ORG", "must be NAME=VALUE"),
        ("1BAD=x", "must be NAME=VALUE"),
        ("KDBDB_ORG=a\nb", "control character"),
    ],
)
def test_a_launcher_variable_the_deployment_owns_or_cannot_carry_is_refused(item, message):
    with pytest.raises(DeployError, match=message):
        parse_args([*BASE, *SITE, "--launcher-env", item])


def test_the_launcher_reaches_preflight():
    text = Deployment(parse_args([*BASE, *SITE]), FakeRemote()).preflight_script()
    assert "torq_launcher=/opt/site/torq/bin/torq.sh\n" in text
    assert "torq_home=/opt/site/torq/core/current\n" in text


def test_preflight_accepts_a_core_without_torq_sh_given_an_executable_launcher(tmp_path):
    core = tmp_path / "core"
    core.mkdir()
    (core / "torq.q").write_text("")
    launcher = tmp_path / "bin" / "torq.sh"
    launcher.parent.mkdir()
    launcher.write_text("#!/bin/sh\n")
    launcher.chmod(0o755)
    r = _run_torq_checks(core, str(launcher))
    assert (r.returncode, r.stdout) == (0, "ok\n"), r.stderr


def test_preflight_refuses_a_missing_or_non_executable_launcher(tmp_path):
    core = tmp_path / "core"
    core.mkdir()
    (core / "torq.q").write_text("")
    r = _run_torq_checks(core, str(tmp_path / "absent.sh"))
    assert r.returncode == 1 and "no TorQ launcher" in r.stderr
    plain = tmp_path / "torq.sh"
    plain.write_text("#!/bin/sh\n")
    plain.chmod(0o644)
    r = _run_torq_checks(core, str(plain))
    assert r.returncode == 1 and "is not executable" in r.stderr


def test_preflight_still_wants_torq_q_in_the_core_and_torq_sh_without_a_launcher(tmp_path):
    core = tmp_path / "core"
    core.mkdir()
    r = _run_torq_checks(core, "")
    assert r.returncode == 1 and f"no torq.q in {core}" in r.stderr
    (core / "torq.q").write_text("")
    r = _run_torq_checks(core, "")
    assert r.returncode == 1 and "no torq.sh" in r.stderr and "--torq-launcher" in r.stderr
    (core / "torq.sh").write_text("")
    assert _run_torq_checks(core, "").returncode == 0


def test_the_release_environment_carries_the_launcher_and_owns_its_data():
    cfg = parse_args([*BASE, *SITE, "--launcher-env", "KDBDB_ORG=uqf"])
    lines = Deployment(cfg, FakeRemote()).env_lines()
    assert "export TORQHOME=/opt/site/torq/core/current" in lines
    assert "export UQS_TORQ_LAUNCHER=/opt/site/torq/bin/torq.sh" in lines
    assert "export TORQDATAHOME=/opt/uqf/shared/data" in lines
    assert "export KDBDB_ORG=uqf" in lines


def test_a_launcher_variable_can_name_its_own_data_directory():
    cfg = parse_args([*BASE, *SITE, "--launcher-env", "TORQDATAHOME=/srv/torqdata"])
    lines = Deployment(cfg, FakeRemote()).env_lines()
    assert [ln for ln in lines if "TORQDATAHOME" in ln] == ["export TORQDATAHOME=/srv/torqdata"]


def test_without_a_launcher_the_release_environment_is_unchanged():
    lines = Deployment(parse_args(BASE), FakeRemote()).env_lines()
    assert not [ln for ln in lines if "LAUNCHER" in ln or "TORQDATAHOME" in ln]


def test_every_stage_runs_the_launcher_its_release_persisted(tmp_path):
    _, remote, _ = _run(tmp_path, {"uqs deploy verify --profile": _verified(True)}, args=SITE)
    prepare = next(s for stage, s in remote.scripts if stage == "prepare")
    assert "export UQS_TORQ_LAUNCHER=/opt/site/torq/bin/torq.sh" in prepare
    for stage in ("start", "verify"):
        ran = [s for st, s in remote.scripts if st == stage]
        assert ran and all("source ./deploy.env" in s for s in ran), stage


def test_the_dry_run_names_the_launcher(tmp_path):
    remote = FakeRemote({"uv python find": _done(SERVER + "data=present\n")})
    out = io.StringIO()
    argv = _args(_artifact(tmp_path), *SITE, "--dry-run")
    driver.deploy(parse_args(argv), remote, out=out)
    shown = out.getvalue()
    assert "TORQHOME=/opt/site/torq/core/current, launcher /opt/site/torq/bin/torq.sh" in shown
    assert "UQS_TORQ_LAUNCHER" in shown and "TORQDATAHOME" in shown


# --- live source checks and a private ODBC setup (#840) -----------------------

_CHECK = ["--live-check", "deals_db,quotes_db", "--odbc-home", "/opt/odbc"]


def test_a_live_check_runs_after_verification_and_before_activation(tmp_path):
    code, remote, report = _run(
        tmp_path,
        {"uqs deploy verify --profile": _verified(True), "sources check": _done("ok\n")},
        args=_CHECK,
    )
    assert code == 0 and report["checks"]["live-check"] == "ok"
    stages = [stage for stage, _ in remote.scripts]
    assert stages.index("verify") < stages.index("live-check") < stages.index("activate")
    (check,) = remote.stage("live-check")
    assert ".venv/bin/uqs config sources check deals_db quotes_db --timeout 120" in check
    assert "source ./deploy.env" in check, "with the release's own environment"


def test_a_failed_live_check_blocks_activation_and_rolls_back(tmp_path):
    failed = _done("deals_db failed connect login failed: PWD=hunter2\n", rc=1)
    code, remote, report = _run(
        tmp_path,
        {"uqs deploy verify --profile": _verified(True), "sources check": failed},
        args=_CHECK,
    )
    assert code == 1 and report["stage"] == "live-check"
    assert "deals_db failed connect" in report["error"] and "hunter2" not in report["error"]
    assert remote.ran("uqs stop all") and not remote.ran(".current.new")


def test_the_odbc_setup_is_loaded_by_every_process_the_release_starts(tmp_path):
    _, remote, _ = _run(
        tmp_path,
        {"uqs deploy verify --profile": _verified(True), "sources check": _done("ok\n")},
        args=_CHECK,
    )
    (prepare,) = remote.stage("prepare")
    assert ". /opt/odbc/current/env.sh" in prepare
    assert "export UQS_ODBC_HOME=/opt/odbc" in prepare
    preflight = remote.scripts[0][1]
    assert "odbc_home=/opt/odbc\n" in preflight and '"$odbc_home/current/env.sh"' in preflight


def test_without_a_live_check_nothing_is_checked_and_no_odbc_is_loaded(tmp_path):
    _, remote, report = _run(tmp_path, {"uqs deploy verify --profile": _verified(True)})
    assert "live-check" not in report["checks"] and not remote.stage("live-check")
    (prepare,) = remote.stage("prepare")
    assert "env.sh" not in prepare and "UQS_ODBC_HOME" not in prepare


def test_a_live_check_names_only_sources():
    with pytest.raises(DeployError, match="--live-check"):
        parse_args([*BASE, "--live-check", "deals;rm -rf /"])


def test_the_dry_run_plans_the_live_check(tmp_path):
    remote = FakeRemote({"uv python find": _done(SERVER + "data=present\n")})
    out = io.StringIO()
    driver.deploy(parse_args(_args(_artifact(tmp_path), "--dry-run", *_CHECK)), remote, out=out)
    assert (
        "live-chk  uqs config sources check deals_db quotes_db --timeout 120 "
        "(ODBC from /opt/odbc)" in out.getvalue()
    )


def test_deploy_logs_like_every_command_but_on_stderr(tmp_path):
    """Through uqs.logger's format, on stderr: stdout is for the path, plan or
    JSON another program reads."""
    uqs = str(Path(sys.executable).parent / "uqs")
    r = subprocess.run(
        [uqs, "deploy", "build", "--dry-run", "--bundle", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "NO_COLOR": "1"},
    )
    assert (r.returncode, r.stdout) == (1, "")
    assert r.stderr.startswith("ERROR    | uqs deploy build: FAILED at bundle: "), r.stderr


@pytest.mark.parametrize(("q", "version"), [("4.0", "4.1"), ("4.0", "5"), (None, "5"), (None, "")])
def test_a_converted_release_or_a_5_0_server_passes_the_q_check(tmp_path, q, version):
    facts = SERVER + f"qversion={version}\ndata=present\n"
    code, _, report = _run_with_facts(tmp_path, facts, q)
    assert code == 0 and report["status"] == "deployed", report


def _run_with_facts(tmp_path, facts, q):
    remote = FakeRemote(
        {**_HEALTHY, "uv python find": _done(facts), "uqs deploy verify --profile": _verified(True)}
    )
    out = io.StringIO()
    target = {"q": q} if q else {}
    code = driver.deploy(parse_args(_args(_artifact(tmp_path, **target))), remote, out=out)
    return code, remote, json.loads(out.getvalue())


def test_preflight_asks_the_servers_q_for_its_version():
    assert '-1 "DEPLOY_Q_OK ",string .z.K' in PREFLIGHT
    assert 'echo "qversion=$qversion"' in PREFLIGHT


# --- an upgrade's downtime (#871) -------------------------------------------------


def _upgrade(tmp_path, monkeypatch, verifies: bool):
    from uqs.deploy import driver as driver_mod

    clock = iter([100.0, 142.5])
    monkeypatch.setattr(driver_mod.time, "monotonic", lambda: next(clock))
    previous = json.dumps({"profile": "fx", "processes": [{"process": "rdb1"}]})
    rules = {
        "uv python find": _done(SERVER + "data=present\ncurrent=OLD\n"),
        "deploy-report.json\n": _done(previous),
        "readlink": _done("current=OLD\n"),
        "uqs deploy verify --profile essential": _verified(verifies),
        "uqs deploy verify --profile fx": _verified(True),
    }
    return _run(tmp_path, rules, args=["--restart"])


def test_an_upgrade_records_its_downtime_from_the_stop_to_the_new_release_verified(
    tmp_path, monkeypatch
):
    code, _, report = _upgrade(tmp_path, monkeypatch, verifies=True)
    assert code == 0 and report["downtime"]["seconds"] == 42.5
    assert report["downtime"]["stopped_at"] <= report["downtime"]["verified_at"]


def test_a_failed_upgrade_records_when_the_outage_began_and_no_end(tmp_path, monkeypatch):
    code, _, report = _upgrade(tmp_path, monkeypatch, verifies=False)
    assert code == 1 and report["downtime"]["stopped_at"]
    assert (report["downtime"]["verified_at"], report["downtime"]["seconds"]) == (None, None)


def test_a_first_deployment_replaces_nothing_and_has_no_downtime(tmp_path):
    _, _, report = _run(tmp_path, {"uqs deploy verify --profile": _verified(True)})
    assert report["downtime"] is None
