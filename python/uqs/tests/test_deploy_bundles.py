"""`uqs deploy push --jobs/--live` and `uqs deploy verify`'s bundle checks (#800),
with no server: a fake remote answers each script by a marker it contains.
"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest
from typer.main import get_group

from uqs.cli.deploy import deploy_app
from uqs.deploy import artifact, driver, payload, verify
from uqs.deploy import build as release_build
from uqs.deploy.config import Config, DeployError, make_config
from uqs.deploy.selection import Selection, verify_command

ROOT = Path(__file__).resolve().parents[3]


_CLI = get_group(deploy_app)


def parse_args(argv: list[str]) -> Config:
    """The Config `uqs deploy push` makes of `argv`: parsed by the command's
    own options, checked by the make_config it calls."""
    return make_config(**_CLI.commands["push"].make_context("push", list(argv)).params)


ARGS = ["--host", "uqf-server", "--dest", "/opt/uqf", "--profile", "essential"]
SERVER = "os=Linux\narch=x86_64\npython=3.14\nqhome=/opt/kx\nqcmd=/opt/kx/bin/q\ndata=present\n"

BUNDLES = {
    "piggybank": {
        "version": "1.4.0",
        "revision": {"commit": "feedfacecafebeef0000", "dirty": False},
        "tables": ["piggy_tape"],
        "jobs": [
            {"kind": "source", "file": "piggy.q"},
            {"kind": "worker", "name": "piggy_backfill", "procname": "piggy_backfill1"},
            {
                "kind": "streaming",
                "name": "piggy_spread",
                "procname": "piggy_spread1",
                "needs": ["fxfeed1", "piggy_spread1"],
            },
        ],
    },
    "marketwarehouse": {
        "version": "0.9.1",
        "revision": None,
        "tables": ["mw_quote"],
        "jobs": [
            {
                "kind": "streaming",
                "name": "mw_quotes",
                "procname": "mw_quotes1",
                "needs": ["mw_quotes1"],
            }
        ],
    },
}


def _done(stdout: str = "", rc: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], rc, stdout=stdout, stderr=stderr)


class FakeRemote:
    def __init__(self, rules: dict[str, subprocess.CompletedProcess]):
        self.rules = rules
        self.scripts: list[tuple[str, str]] = []

    def run(self, script: str, timeout: int, stage: str, as_login: bool = False):
        self.scripts.append((stage, script))
        for marker, result in self.rules.items():
            if marker in script:
                return result
        return _done()

    def put(self, local: Path, remote_path: str, timeout: int, stage: str) -> None:
        pass

    def stage(self, name: str) -> list[str]:
        return [s for st, s in self.scripts if st == name]


def _verified(passed: bool, reason: str = "") -> subprocess.CompletedProcess:
    body = {"passed": passed, "reason": reason, "processes": [{"process": "rdb1", "ok": passed}]}
    marker = verify.OK_MARKER if passed else verify.FAILED_MARKER
    return _done(json.dumps(body) + "\n" + marker + "\n", rc=0 if passed else 1)


def _artifact(tmp_path: Path, bundles: dict | None = BUNDLES) -> Path:
    root = tmp_path / "tree"
    files = ["pyproject.toml", "src/init.q"]
    for f in files:
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        (root / f).write_text(f"content of {f}\n")
    py = tmp_path / "py"
    (py / "wheels").mkdir(parents=True)
    (py / "requirements.txt").write_text("rich==15.0.0 --hash=sha256:00\n")
    (py / "wheels" / "uqs-0.1.0-py3-none-any.whl").write_bytes(b"uqs")
    art = release_build.build_artifact(
        root,
        tmp_path / "dist",
        rid="20261007T000000Z-0123456789ab",
        rev="0123456789abcdef",
        dirty=False,
        files=files,
        target=artifact.Target(os="linux", arch="x86_64", python="3.14"),
        python_dir=py,
        bundles=bundles,
    )
    return art.path


_HEALTHY = {
    "uv python find": _done(SERVER),
    "deploy_smoke.q": _done("DEPLOY_SMOKE_OK\n"),
    # a server with no HDB yet: nothing for the release's hdb-check to judge (#870)
    "uqs data hdb-check": _done('{"hdb": "/data/hdb", "present": false}\n'),
    "--ports-free": _done(json.dumps({"busy": {}}) + "\n" + verify.OK_MARKER + "\n"),
    "uqs deploy verify --profile": _verified(True),
}


def _deploy(tmp_path, *args, rules=None, bundles=BUNDLES):
    # the test's own rules first: the first marker a script contains decides
    merged = dict(rules or {})
    for marker, result in _HEALTHY.items():
        merged.setdefault(marker, result)
    remote = FakeRemote(merged)
    out = io.StringIO()
    cfg = parse_args([str(_artifact(tmp_path, bundles)), *ARGS, *args])
    code = driver.deploy(cfg, remote, out=out)
    return code, remote, out.getvalue()


# ------------------------------------------------------------- selection


def test_no_job_is_started_unless_selected(tmp_path):
    code, remote, out = _deploy(tmp_path)
    assert code == 0
    (start,) = remote.stage("start")
    assert start.rstrip().endswith(".venv/bin/uqs start --profile essential")
    report = json.loads(out)
    assert report["jobs"] == [] and report["extra_processes"] == []
    assert set(report["bundles"]) == {"piggybank", "marketwarehouse"}


def test_selected_jobs_start_with_their_dependency_closure(tmp_path):
    code, remote, out = _deploy(tmp_path, "--jobs", "piggy_spread,mw_quotes")
    assert code == 0
    (start,) = remote.stage("start")
    assert "uqs start --profile essential piggy_spread1 fxfeed1 mw_quotes1" in start
    (ports,) = remote.stage("ports")
    assert "--ports-free --procs piggy_spread1,fxfeed1,mw_quotes1" in ports
    (check,) = remote.stage("verify")
    assert "--procs piggy_spread1,fxfeed1,mw_quotes1" in check
    assert "--tables mw_quote,piggy_tape" in check
    report = json.loads(out)
    assert report["jobs"] == ["piggy_spread", "mw_quotes"]
    assert report["extra_processes"] == ["piggy_spread1", "fxfeed1", "mw_quotes1"]


def test_a_worker_is_never_run_by_a_deployment(tmp_path):
    with pytest.raises(DeployError, match="bounded worker.*uqs backfill piggy_backfill"):
        _deploy(tmp_path, "--jobs", "piggy_backfill")


def test_an_unknown_job_is_refused_naming_the_choices(tmp_path):
    with pytest.raises(DeployError, match="mw_quotes, piggy_spread"):
        _deploy(tmp_path, "--jobs", "piggy_typo")


def test_jobs_need_an_artifact_with_bundles(tmp_path):
    with pytest.raises(DeployError, match="carries no bundle"):
        _deploy(tmp_path, "--jobs", "piggy_spread", bundles=None)


def test_an_artifact_without_bundles_deploys_as_before(tmp_path):
    code, remote, out = _deploy(tmp_path, bundles=None)
    assert code == 0
    (check,) = remote.stage("verify")
    assert "--procs" not in check and "--tables" not in check and "--live" not in check
    assert json.loads(out)["bundles"] == {}


def test_a_bad_job_name_is_refused():
    with pytest.raises(DeployError, match="not a job name"):
        parse_args(["a", *ARGS, "--jobs", "x;rm -rf /"])


def test_the_dry_run_reports_the_resolved_processes(tmp_path):
    code, remote, out = _deploy(tmp_path, "--dry-run", "--jobs", "piggy_spread")
    assert code == 0 and [st for st, _ in remote.scripts] == ["preflight"]
    assert "bundle: piggybank 1.4.0 (feedfacecafe)" in out
    assert "bundle: marketwarehouse 0.9.1 (no revision)" in out
    assert "jobs: piggy_spread -> beside the profile: piggy_spread1 fxfeed1" in out
    assert "workers installed, never run: piggy_backfill" in out
    assert "uqs start --profile essential piggy_spread1 fxfeed1" in out


# ------------------------------------------------------------------ live


def test_live_requires_live_sources_on_the_server_and_in_verification(tmp_path):
    code, remote, _ = _deploy(tmp_path, "--live")
    assert code == 0
    (prepare,) = remote.stage("prepare")
    assert "export UQS_REQUIRE_LIVE_SOURCES=1" in prepare
    (check,) = remote.stage("verify")
    assert check.rstrip().endswith("--live; fi")


def test_without_live_nothing_is_required(tmp_path):
    _, remote, _ = _deploy(tmp_path)
    (prepare,) = remote.stage("prepare")
    assert "UQS_REQUIRE_LIVE_SOURCES" not in prepare


# -------------------------------------------------------------- rollback


def test_a_failed_upgrade_restores_the_previous_sidecar_processes(tmp_path):
    previous = json.dumps(
        {
            "profile": "fx",
            "processes": [{"process": "rdb1"}, {"process": "piggy_spread1"}],
            "extra_processes": ["piggy_spread1"],
        }
    )
    rules = {
        "uv python find": _done(SERVER + "current=OLD\n"),
        "deploy-report.json\n": _done(previous),
        "readlink": _done("current=OLD\n"),
        "uqs deploy verify --profile essential": _verified(False, "no answer from mw_quotes1"),
        "uqs deploy verify --profile fx": _verified(True),
    }
    code, remote, out = _deploy(tmp_path, "--restart", "--jobs", "mw_quotes", rules=rules)
    assert code == 1
    rollback = remote.stage("rollback")
    assert any("uqs start --profile fx piggy_spread1" in s for s in rollback)
    assert any("--profile fx" in s and "--procs piggy_spread1" in s for s in rollback)
    assert "restarted and verified release OLD" in json.loads(out)["rollback"]


# ---------------------------------------------------------- the verifier


def _query(answers):
    def query(expr, port):
        for key, value in answers.items():
            if key in expr:
                return value(port) if callable(value) else value
        raise AssertionError(expr)

    return query


def _answers(missing="`symbol$()", live=True):
    names = {6000: "stp1", 6001: "piggy_spread1"}
    return {
        verify.IDENTITY_EXPR: lambda port: names[port],
        "fwd_simple": verify.LIBRARY_EXPECTED,
        "verify_all": True,
        "live_required": live,
        "except tables[]": missing,
    }


def test_the_verifier_checks_bundle_tables_on_the_plant():
    expected = {"stp1": 6000, "piggy_spread1": 6001}
    passed, _, why = verify.verify(
        expected, {"piggy_spread1"}, _query(_answers()), 1, tables=["piggy_tape"]
    )
    assert passed, why
    passed, _, why = verify.verify(
        expected, {"piggy_spread1"}, _query(_answers(missing=",`piggy_tape")), 1,
        tables=["piggy_tape"],
    )  # fmt: skip
    assert not passed and "piggy_tape" in why


def test_tables_without_the_plant_started_fail():
    passed, _, why = verify.verify(
        {"piggy_spread1": 6001}, {"piggy_spread1"}, _query(_answers()), 1, tables=["t"]
    )
    assert not passed and "stp1" in why


def test_live_verification_fails_a_process_that_would_read_a_fixture():
    expected = {"piggy_spread1": 6001}
    passed, results, _ = verify.verify(
        expected, {"piggy_spread1"}, _query(_answers(live=False)), 1, live=True
    )
    assert not passed and "live-sources" in results[0].error
    passed, _, why = verify.verify(expected, {"piggy_spread1"}, _query(_answers()), 1, live=True)
    assert passed, why


def test_the_missing_tables_query_is_a_symbol_list():
    assert verify.missing_tables_expr(["a", "b"]) == "-3!((),`a`b) except tables[]"
    assert verify.missing_tables("`symbol$()") == ""
    assert verify.missing_tables(",`b") == ",`b"


# --------------------------------------------------------------- secrets


def test_server_secrets_are_sourced_never_shipped_and_must_be_private(tmp_path):
    _, remote, _ = _deploy(tmp_path, "--live")
    (prepare,) = remote.stage("prepare")
    secrets = "/opt/uqf/shared/config/secrets.env"
    # deploy.env loads them on the server, exporting each for the processes
    assert f"if [ -f {secrets} ]; then set -a; . {secrets}; set +a; fi" in prepare
    # a file others may read is refused before anything runs
    assert f"find {secrets} -perm /077" in prepare
    # sources.csv is the server's, linked in like gateway_users.csv
    assert "/opt/uqf/shared/config/scripts/torqconfig/sources.csv" in prepare


def test_an_env_file_in_a_bundle_never_reaches_an_artifact():
    assert payload.is_excluded("src/etl/streaming/.env")
    assert payload.is_excluded("python/uqs/.env.local")
    assert payload.is_excluded("scripts/torqconfig/secrets.env")


def test_a_release_from_before_835_is_verified_by_its_own_script():
    """A rollback can return to a release that has scripts/deploy_verify.py
    and no `uqs deploy verify`; the command asks the release which it has,
    and gives the old script the selection space-separated, as it parsed it."""
    sel = Selection(processes=["piggy_spread1", "fxfeed1"], tables=["piggy_tape"])
    command = verify_command("fx", sel, True, "--deadline", "60")
    assert command.startswith("if [ -f scripts/deploy_verify.py ]; then ")
    assert (
        ".venv/bin/python scripts/deploy_verify.py --profile fx --deadline 60 "
        "--procs piggy_spread1 fxfeed1 --tables piggy_tape --live;" in command
    )
    assert (
        "else .venv/bin/uqs deploy verify --profile fx --deadline 60 "
        "--procs piggy_spread1,fxfeed1 --tables piggy_tape --live; fi" in command
    )
