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
import tarfile
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

BASE = ["--host", "uqf-server", "--dest", "/opt/uqf", "--profile", "essential"]


def _done(stdout: str = "", rc: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], rc, stdout=stdout, stderr=stderr)


class FakeRemote:
    """Answers each script by the first rule whose marker it contains."""

    def __init__(self, rules: dict[str, subprocess.CompletedProcess] | None = None):
        self.rules = rules or {}
        self.scripts: list[tuple[str, str]] = []
        self.puts: list[str] = []

    def run(self, script: str, timeout: int, stage: str) -> subprocess.CompletedProcess:
        self.scripts.append((stage, script))
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


def _git(files: list[str], dirty: str = ""):
    def runner(argv, **_):
        if "ls-files" in argv:
            return _done("\0".join(files) + "\0")
        if "rev-parse" in argv:
            return _done("0123456789abcdef0123456789abcdef01234567\n")
        if "status" in argv:
            return _done(dirty)
        raise AssertionError(argv)

    return runner


def _tree(tmp_path: Path) -> tuple[Path, list[str]]:
    files = ["pyproject.toml", "src/init.q", "scripts/deploy_smoke.q"]
    for f in files:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text(f"content of {f}\n")
    return tmp_path, files


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
        deploy.parse_args(["--host", "h", "--dest", dest, "--profile", "essential"])


@pytest.mark.parametrize("host", ["-oProxyCommand=evil", "a b", "h;x"])
def test_a_host_that_could_be_read_as_an_option_is_refused(host):
    with pytest.raises(deploy.DeployError, match="--host"):
        # --host=VALUE: argparse alone would refuse "--host -o..." as a missing value.
        deploy.parse_args([f"--host={host}", "--dest", "/opt/uqf", "--profile", "essential"])


def test_ssh_keeps_host_key_checking_and_never_prompts():
    remote = deploy.Remote("uqf-server", 7)
    argv = remote.ssh_argv()
    assert argv[:1] == ["ssh"] and "BatchMode=yes" in argv and "ConnectTimeout=7" in argv
    assert not any("StrictHostKeyChecking" in a or "UserKnownHostsFile" in a for a in argv)


def test_every_value_reaches_the_remote_shell_quoted():
    cfg = deploy.parse_args([*BASE, "--qcmd", "/opt/kx/bin/q", "--qhome", "/opt/kx"])
    text = deploy.Deployment(cfg, FakeRemote()).preflight_script()
    assert "dest=/opt/uqf\n" in text and "qcmd=/opt/kx/bin/q\n" in text
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


# --- packaging ----------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        ".envrc",
        "python/uqs/.env",
        "lib/torq/torq.q",
        "output/uqs/hdb/sym",
        "scripts/torqconfig/permissions/gateway_users.csv",
        "python/uqs/tests/test_x.py",
        "src/x.log",
        "keys/id_ed25519",
        "k4.lic",
    ],
)
def test_secrets_data_and_external_trees_are_never_packaged(path):
    assert deploy.is_excluded(path)


@pytest.mark.parametrize(
    "path", ["src/init.q", "uv.lock", "scripts/deploy_smoke.q", "python/uqs/pyproject.toml"]
)
def test_source_and_configuration_are_packaged(path):
    assert not deploy.is_excluded(path)


def test_only_tracked_files_under_the_allowlist_are_listed():
    files = deploy.tracked_files(ROOT, _git(["src/init.q", ".envrc", "lib/torq/torq.q"]))
    assert files == ["src/init.q"]


def test_the_package_carries_a_manifest_of_its_revision_and_file_hashes(tmp_path):
    root, files = _tree(tmp_path / "tree")
    out = tmp_path / "out"
    out.mkdir()
    pkg = deploy.build_package(root, out, "20261007T000000Z-0123", "0123", False, files)
    with tarfile.open(pkg.path) as tar:
        names = tar.getnames()
        member = tar.extractfile(deploy.MANIFEST)
        assert member is not None
        manifest = json.load(member)
    assert sorted(names) == sorted([*files, deploy.MANIFEST])
    assert manifest["revision"] == "0123" and set(manifest["files"]) == set(files)
    assert len(pkg.sha256) == 64


def test_uncommitted_changes_are_refused_unless_allowed(tmp_path):
    root, files = _tree(tmp_path)
    cfg = deploy.parse_args(BASE)
    with pytest.raises(deploy.DeployError, match="uncommitted"):
        deploy.deploy(cfg, FakeRemote(), root=root, git=_git(files, dirty=" M src/init.q"))


# --- preflight and dry run ---------------------------------------------------


def test_a_missing_data_directory_needs_init_data(tmp_path):
    root, files = _tree(tmp_path)
    remote = FakeRemote({"uv python find": _done("data=absent\n")})
    with pytest.raises(deploy.DeployError, match="--init-data"):
        deploy.deploy(deploy.parse_args(BASE), remote, root=root, git=_git(files))


def test_replacing_a_deployment_needs_restart(tmp_path):
    root, files = _tree(tmp_path)
    remote = FakeRemote({"uv python find": _done("data=present\ncurrent=OLD\n")})
    with pytest.raises(deploy.DeployError, match="--restart"):
        deploy.deploy(deploy.parse_args(BASE), remote, root=root, git=_git(files))


def test_a_dry_run_changes_nothing_and_shows_the_plan(tmp_path):
    root, files = _tree(tmp_path)
    remote = FakeRemote({"uv python find": _done("data=present\ncurrent=OLD\n")})
    out = io.StringIO()
    cfg = deploy.parse_args([*BASE, "--dry-run", "--restart"])
    assert deploy.deploy(cfg, remote, root=root, git=_git(files), out=out) == 0
    assert [stage for stage, _ in remote.scripts] == ["preflight"]
    assert remote.puts == []
    shown = out.getvalue()
    assert "payload: 3 files" in shown and "stop release OLD's processes" in shown


# --- the full run ---------------------------------------------------------------

_HEALTHY = {
    "uv python find": _done("data=present\n"),
    "deploy_smoke.q": _done("DEPLOY_SMOKE_OK\n"),
    "--ports-free": _done(json.dumps({"busy": {}}) + "\n" + verify.OK_MARKER + "\n"),
}


def _run(tmp_path, rules, args=()):
    root, files = _tree(tmp_path)
    remote = FakeRemote({**_HEALTHY, **rules})
    out = io.StringIO()
    code = deploy.deploy(
        deploy.parse_args([*BASE, *args]), remote, root=root, git=_git(files), out=out
    )
    return code, remote, json.loads(out.getvalue())


def test_a_healthy_deployment_activates_only_after_verification(tmp_path):
    code, remote, report = _run(tmp_path, {"deploy_verify.py --profile": _verified(True)})
    assert code == 0 and report["status"] == "deployed"
    stages = [stage for stage, _ in remote.scripts]
    assert stages.index("verify") < stages.index("activate")
    assert remote.ran(".current.new") and remote.ran("rm -rf /opt/uqf/deploy.lock")


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
        "uv python find": _done("data=present\ncurrent=OLD\n"),
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
