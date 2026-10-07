"""scripts/deploy.py and scripts/deploy_verify.py (#773), with no server.

ssh and scp are replaced by a fake that records every script and answers by
what the script does, so the stages, their order and their failures are
tested here; a real server is the integration check's job.
"""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / file)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Registered first: a dataclass looks its module up in sys.modules.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


deploy = _load("uqf_deploy_under_test", "deploy.py")
verify = _load("uqf_deploy_verify_under_test", "deploy_verify.py")
release = deploy.build_release

ARGS = ["--host", "uqf-server", "--dest", "/opt/uqf", "--profile", "essential"]
BASE = ["--artifact", "dist/uqf-x.tar.gz", *ARGS]

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
    t = release.Target(**{"os": "linux", "arch": "x86_64", "python": "3.14", **target})
    art = release.build_artifact(
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
    return ["--artifact", str(path), *ARGS, *extra]


def test_deploy_parses_on_the_oldest_python_it_runs_under():
    """deploy.py runs under the operator's python3, not the workspace's 3.14."""
    import ast

    ast.parse((ROOT / "scripts" / "deploy.py").read_text(), feature_version=(3, 10))


# --- arguments and quoting ------------------------------------------------------


def test_the_required_arguments_are_host_dest_and_profile():
    with pytest.raises(SystemExit):
        deploy.parse_args(["--host", "h", "--dest", "/opt/uqf"])
    cfg = deploy.parse_args(BASE)
    assert (cfg.host, cfg.dest, cfg.profile) == ("uqf-server", "/opt/uqf", "essential")
    assert cfg.data_root == "/opt/uqf/shared/data"


@pytest.mark.parametrize(
    "dest", ["opt/uqf", "/", "/opt/uqf; rm -rf /", "/opt/../etc", "/opt//uqf", "/opt/u qf"]
)
def test_a_destination_that_is_not_a_plain_absolute_path_is_refused(dest):
    with pytest.raises(deploy.DeployError, match="--dest"):
        deploy.parse_args(
            ["--artifact", "a", "--host", "h", "--dest", dest, "--profile", "essential"]
        )


@pytest.mark.parametrize("host", ["-oProxyCommand=evil", "a b", "h;x"])
def test_a_host_that_could_be_read_as_an_option_is_refused(host):
    with pytest.raises(deploy.DeployError, match="--host"):
        # --host=VALUE: argparse alone would refuse "--host -o..." as a missing value.
        deploy.parse_args(
            ["--artifact", "a", f"--host={host}", "--dest", "/opt/uqf", "--profile", "essential"]
        )


def test_ssh_keeps_host_key_checking_and_never_prompts():
    remote = deploy.Remote("uqf-server", 7)
    argv = remote.ssh_argv()
    assert argv[:1] == ["ssh"] and "BatchMode=yes" in argv and "ConnectTimeout=7" in argv
    assert not any("StrictHostKeyChecking" in a or "UserKnownHostsFile" in a for a in argv)


def test_every_value_reaches_the_remote_shell_quoted():
    cfg = deploy.parse_args([*BASE, "--qcmd", "/opt/kx/bin/q", "--qhome", "/opt/kx"])
    text = deploy.Deployment(cfg, FakeRemote()).preflight_script()
    assert "dest=/opt/uqf\n" in text and "qcmd_flag=/opt/kx/bin/q\n" in text
    assert deploy.q("a b;c") == "'a b;c'"


def test_a_remote_step_that_overruns_its_timeout_fails_naming_the_stage():
    def runner(*_, **kwargs):
        raise subprocess.TimeoutExpired("ssh", kwargs["timeout"])

    remote = deploy.Remote("h", 5, runner=runner)
    with pytest.raises(deploy.DeployError, match="within 30s") as err:
        remote.run("true", 30, "prepare")
    assert err.value.stage == "prepare"


def test_secrets_are_masked_in_anything_printed():
    assert "hunter2" not in deploy.redact("DRIVER=x;PWD=hunter2 and password: s3cret")


# --- the artifact -------------------------------------------------------------


def test_the_artifact_is_required():
    with pytest.raises(SystemExit):
        deploy.parse_args(ARGS)


def test_a_tampered_artifact_is_refused_before_the_server_is_touched(tmp_path):
    path = _artifact(tmp_path)
    Path(f"{path}.sha256").write_text("0" * 64 + f"  {path.name}\n")
    remote = FakeRemote()
    with pytest.raises(deploy.DeployError, match="does not match") as err:
        deploy.deploy(deploy.parse_args(_args(path)), remote)
    assert err.value.stage == "artifact" and remote.scripts == []


@pytest.mark.parametrize(
    ("facts", "said"),
    [
        ("os=Darwin\narch=x86_64\npython=3.14\n", "not linux"),
        ("os=Linux\narch=aarch64\npython=3.14\n", "built for x86_64"),
        ("os=Linux\narch=x86_64\npython=3.13\n", "wheels are for 3.14"),
    ],
)
def test_a_server_the_artifact_was_not_built_for_is_refused(tmp_path, facts, said):
    remote = FakeRemote({"uv python find": _done(facts + "data=present\n")})
    with pytest.raises(deploy.DeployError, match=said) as err:
        deploy.deploy(deploy.parse_args(_args(_artifact(tmp_path))), remote)
    assert err.value.stage == "preflight"


def test_an_artifact_already_on_the_server_is_refused_in_preflight(tmp_path):
    remote = FakeRemote({"uv python find": _done(SERVER + "data=present\nrelease_exists=yes\n")})
    with pytest.raises(deploy.DeployError, match="already on uqf-server") as err:
        deploy.deploy(deploy.parse_args(_args(_artifact(tmp_path))), remote)
    assert err.value.stage == "preflight" and "/opt/uqf/releases/2026" in remote.scripts[0][1]


def test_amd64_is_x86_64():
    target = {"os": "linux", "arch": "x86_64", "python": "3.14"}
    assert release.compatible(target, {"os": "Linux", "arch": "amd64", "python": "3.14"}) == []


def test_preflight_asks_uv_for_the_artifacts_python(tmp_path):
    remote = FakeRemote({"uv python find": _done(SERVER + "data=present\n")})
    deploy.deploy(deploy.parse_args(_args(_artifact(tmp_path, python="3.14"), "--dry-run")), remote)
    assert (
        "python=3.14\n" in remote.scripts[0][1]
        and 'uv python find "$python"' in remote.scripts[0][1]
    )


# --- preflight and dry run ---------------------------------------------------


def test_a_missing_data_directory_needs_init_data(tmp_path):
    remote = FakeRemote({"uv python find": _done(SERVER + "data=absent\n")})
    with pytest.raises(deploy.DeployError, match="--init-data"):
        deploy.deploy(deploy.parse_args(_args(_artifact(tmp_path))), remote)


def test_replacing_a_deployment_needs_restart(tmp_path):
    remote = FakeRemote({"uv python find": _done(SERVER + "data=present\ncurrent=OLD\n")})
    with pytest.raises(deploy.DeployError, match="--restart"):
        deploy.deploy(deploy.parse_args(_args(_artifact(tmp_path))), remote)


def test_a_dry_run_changes_nothing_and_shows_the_plan(tmp_path):
    remote = FakeRemote({"uv python find": _done(SERVER + "data=present\ncurrent=OLD\n")})
    out = io.StringIO()
    cfg = deploy.parse_args(_args(_artifact(tmp_path), "--dry-run", "--restart"))
    assert deploy.deploy(cfg, remote, out=out) == 0
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
    code = deploy.deploy(deploy.parse_args(_args(_artifact(tmp_path), *args)), remote, out=out)
    return code, remote, json.loads(out.getvalue())


def test_a_healthy_deployment_activates_only_after_verification(tmp_path):
    code, remote, report = _run(tmp_path, {"deploy_verify.py --profile": _verified(True)})
    assert code == 0 and report["status"] == "deployed"
    stages = [stage for stage, _ in remote.scripts]
    assert stages.index("verify") < stages.index("activate")
    assert remote.ran(".current.new") and remote.ran("rm -rf /opt/uqf/deploy.lock")


def test_the_release_environment_installs_offline_from_the_artifacts_wheels(tmp_path):
    _, remote, _ = _run(tmp_path, {"deploy_verify.py --profile": _verified(True)})
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
        tmp_path, {"deploy_verify.py --profile": _verified(False, "no answer from rdb1")}
    )
    assert code == 1 and report["stage"] == "verify" and "rdb1" in report["error"]
    assert remote.ran("uqs stop all") and not remote.ran(".current.new")


def test_a_failed_upgrade_restores_the_previous_release(tmp_path):
    previous = json.dumps({"profile": "fx", "processes": [{"process": "rdb1"}]})
    rules = {
        "uv python find": _done(SERVER + "data=present\ncurrent=OLD\n"),
        "deploy-report.json\n": _done(previous),
        "deploy_verify.py --profile": _verified(False, "no answer from rdb1"),
    }
    code, remote, report = _run(tmp_path, rules, args=["--restart"])
    assert code == 1
    assert remote.ran("cd /opt/uqf/releases/OLD") and remote.ran("uqs stop rdb1")
    assert remote.ran("uqs start --profile fx")
    assert "restarted release OLD" in report["rollback"]
    assert "current still names OLD" in report["rollback"]
    assert not remote.ran(".current.new")


def test_a_busy_port_fails_before_the_profile_starts(tmp_path):
    busy = _done(json.dumps({"busy": {"rdb1": 6052}}) + "\n" + verify.FAILED_MARKER + "\n", rc=1)
    code, remote, report = _run(tmp_path, {"--ports-free": busy})
    assert code == 1 and report["stage"] == "ports" and "rdb1 (6052)" in report["error"]
    assert not remote.ran("uqs start")


def test_runtime_data_is_never_touched_without_init_data(tmp_path):
    _, remote, _ = _run(tmp_path, {"deploy_verify.py --profile": _verified(True)})
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
    cfg = deploy.parse_args(_args(_artifact(tmp_path), "--remote-user", "svc", *args))
    code = deploy.deploy(cfg, remote, out=out)
    return code, remote, out.getvalue()


@pytest.mark.parametrize("user", ["root;id", "Svc", "-n", "a b", "x" * 40, "svc$"])
def test_a_remote_user_that_is_not_an_account_name_is_refused(user):
    with pytest.raises(deploy.DeployError, match="--remote-user"):
        deploy.parse_args([*BASE, f"--remote-user={user}"])


def test_without_a_remote_user_ssh_runs_the_script_in_the_logins_own_login_shell():
    assert deploy.Remote("deploy@uqf-server", 5).ssh_argv()[-1] == "bash -l -s"


def test_with_a_remote_user_every_step_runs_through_non_interactive_sudo():
    remote = deploy.Remote("deploy@uqf-server", 5, remote_user="svc")
    assert remote.ssh_argv()[-2:] == ["deploy@uqf-server", "sudo -n -iu svc bash -s"]
    assert remote.ssh_argv(as_login=True)[-1] == "bash -s"


def test_a_missing_sudo_rule_fails_before_anything_changes(tmp_path):
    rules = {"sudo -n -iu svc id -un": _done(rc=1, stderr="sudo: a password is required")}
    with pytest.raises(deploy.DeployError, match="without a password") as err:
        _run_as(tmp_path, rules)
    assert err.value.stage == "preflight"


def test_sudo_landing_in_another_account_is_refused(tmp_path):
    with pytest.raises(deploy.DeployError, match="runs as root, not svc"):
        _run_as(tmp_path, {"sudo -n -iu svc id -un": _done("root\n")})


def test_preflight_checks_the_identity_it_runs_as(tmp_path):
    facts = _done("user=deploy\n" + SERVER + "data=present\n")
    with pytest.raises(deploy.DeployError, match="run as deploy, not svc"):
        _run_as(tmp_path, {"uv python find": facts})


def test_the_archive_reaches_the_service_user_through_a_private_upload(tmp_path):
    code, remote, _ = _run_as(tmp_path, {"deploy_verify.py --profile": _verified(True)})
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
    _, remote, _ = _run_as(tmp_path, {"deploy_verify.py --profile": _verified(True)})
    for s in remote.login:
        assert any(m in s for m in ("id -un", "mktemp -d", "sudo -n -u svc --", "rm -rf /tmp/"))
    service = [s for _, s in remote.scripts if s not in remote.login]
    assert any("uqs start --profile essential" in s for s in service)
    assert any(".current.new" in s for s in service)


def test_settings_cross_sudo_inside_the_script_not_the_environment(tmp_path):
    _, remote, _ = _run_as(tmp_path, {"deploy_verify.py --profile": _verified(True)})
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
    cfg = deploy.parse_args(BASE)
    assert cfg.qcmd is None and cfg.qhome is None
    cfg = deploy.parse_args([*BASE, "--qhome", "/opt/kx home", "--qcmd", "/opt/kx home/bin/q"])
    assert (cfg.qhome, cfg.qcmd) == ("/opt/kx home", "/opt/kx home/bin/q")


@pytest.mark.parametrize("value", ["bin/q", "/opt/../q", "/opt/kx\nq"])
def test_an_explicit_q_path_must_be_absolute_and_plain(value):
    with pytest.raises(deploy.DeployError, match="--qcmd"):
        deploy.parse_args([*BASE, "--qcmd", value])


def test_the_local_environment_never_reaches_the_server(monkeypatch):
    monkeypatch.setenv("QHOME", "/home/me/local-kx")
    monkeypatch.setenv("QCMD", "/home/me/local-kx/q")
    text = deploy.Deployment(deploy.parse_args(BASE), FakeRemote()).preflight_script()
    assert "local-kx" not in text
    assert "qhome_flag=''\n" in text and "qcmd_flag=''\n" in text


def test_explicit_flags_reach_preflight_quoted():
    cfg = deploy.parse_args([*BASE, "--qhome", "/opt/kx home"])
    text = deploy.Deployment(cfg, FakeRemote()).preflight_script()
    assert "qhome_flag='/opt/kx home'\n" in text and "qcmd_flag=''\n" in text


def test_preflight_resolves_from_the_deploying_accounts_environment():
    text = deploy.Deployment(deploy.parse_args(BASE), FakeRemote()).preflight_script()
    assert 'elif [ -n "${QHOME:-}" ]' in text and 'elif [ -n "${QCMD:-}" ]' in text
    assert 'command -v -- "$qcmd_want"' in text and "pass --qhome" in text


def test_the_resolved_q_is_what_the_release_runs_with(tmp_path):
    _, remote, _ = _run(tmp_path, {"deploy_verify.py --profile": _verified(True)})
    prepare = next(s for stage, s in remote.scripts if stage == "prepare")
    assert "export QHOME='/opt/kx home'" in prepare
    assert "export QCMD='/opt/kx home/bin/q'" in prepare
    smoke = next(s for stage, s in remote.scripts if stage == "smoke")
    assert "source ./deploy.env" in smoke and '"$QCMD" scripts/deploy_smoke.q' in smoke


def test_a_preflight_that_reports_no_q_is_refused(tmp_path):
    remote = FakeRemote({"uv python find": _done("os=Linux\narch=x86_64\npython=3.14\n")})
    with pytest.raises(deploy.DeployError, match="did not report the q"):
        deploy.deploy(deploy.parse_args(_args(_artifact(tmp_path))), remote)


def test_a_rollback_uses_the_previous_releases_own_settings(tmp_path):
    previous = json.dumps({"profile": "fx", "processes": [{"process": "rdb1"}]})
    rules = {
        "uv python find": _done(SERVER + "data=present\ncurrent=OLD\n"),
        "deploy-report.json\n": _done(previous),
        "deploy_verify.py --profile": _verified(False, "no answer"),
    }
    _, remote, _ = _run(tmp_path, rules, args=["--restart"])
    restart = next(s for st, s in remote.scripts if st == "rollback" and "start --profile fx" in s)
    assert "cd /opt/uqf/releases/OLD\nsource ./deploy.env" in restart


def test_the_dry_run_names_where_each_q_setting_came_from(tmp_path):
    remote = FakeRemote({"uv python find": _done(SERVER + "data=present\n")})
    out = io.StringIO()
    deploy.deploy(deploy.parse_args(_args(_artifact(tmp_path), "--dry-run")), remote, out=out)
    assert "QHOME=/opt/kx home (svc's QHOME)" in out.getvalue()
    assert "resolved and run once in preflight" in out.getvalue()


def test_help_states_the_resolution_rules(capsys):
    with pytest.raises(SystemExit):
        deploy.parse_args(["--help"])
    shown = " ".join(capsys.readouterr().out.split())
    assert "deploying account's $QHOME" in shown and "never this machine's" in shown
