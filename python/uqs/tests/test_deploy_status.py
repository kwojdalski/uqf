"""uqs deploy status (#868): what a server runs, read from what its pushes
recorded - and read-only: it never takes the lock or changes a file. ssh is a
fake that records every script, as in test_deploy.py.
"""

from __future__ import annotations

import json
import subprocess

from typer.testing import CliRunner

from uqs import cli
from uqs.deploy import status, verify
from uqs.deploy.config import make_config

NEW = "20261008T120000Z-0123456789ab"
OLD = "20261007T120000Z-ba9876543210"
OLDEST = "20261006T120000Z-cccccccccccc"
FAILED = "20261008T130000Z-dddddddddddd"


def _done(stdout: str = "", rc: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], rc, stdout=stdout, stderr="")


def _facts(current: str | None, releases: list[str], lock_owner: str | None = None) -> str:
    lines = [f"current={current}"] if current else []
    lines += [f"release={r}" for r in releases]
    if lock_owner is not None:
        lines += ["lock=held", f"lock_owner={lock_owner}"]
    return "\n".join(lines) + "\n"


def _report(status_: str = "deployed", previous: str | None = OLD, **kw) -> str:
    return json.dumps(
        {
            "release": NEW,
            "profile": "essential",
            "processes": [{"process": "rdb1"}],
            "extra_processes": ["piggy1"],
            "previous_release": previous,
            "status": status_,
            "stage": kw.get("stage", ""),
            "error": kw.get("error", ""),
            "rollback": kw.get("rollback", "not needed"),
            "live": False,
        }
    )


def _manifest(rev: str, **kw) -> str:
    return json.dumps(
        {
            "release": "x",
            "revision": rev,
            "dirty": kw.get("dirty", False),
            "created_at": "2026-10-08T12:00:00+00:00",
            "target": {"os": "linux", "q": kw.get("q")},
            "runtime": "uqf",
            "bundles": {"fx": {"version": "1"}},
        }
    )


def _verified(passed: bool) -> subprocess.CompletedProcess:
    procs = [{"process": "rdb1", "ok": True}, {"process": "piggy1", "ok": passed}]
    body = {
        "passed": passed,
        "reason": "" if passed else "piggy1 did not answer",
        "processes": procs,
    }
    marker = verify.OK_MARKER if passed else verify.FAILED_MARKER
    return _done(json.dumps(body) + "\n" + marker + "\n", rc=0 if passed else 1)


class FakeRemote:
    """Answers each script by the first rule whose marker it contains."""

    def __init__(self, rules: dict[str, subprocess.CompletedProcess]):
        self.rules = rules
        self.scripts: list[str] = []

    def run(self, script: str, timeout: int, stage: str, as_login: bool = False):
        self.scripts.append(script)
        for marker, result in self.rules.items():
            if marker in script:
                return result
        return _done()

    def put(self, *a, **kw) -> None:
        raise AssertionError("status copies nothing to the server")


def _server(*, facts: str | None = None, health=True, failing="{}", **extra):
    rules = {
        "lock_owner=": _done(facts or _facts(NEW, [OLDEST, OLD, NEW])),
        f"releases/{NEW}/deploy-report.json": _done(_report()),
        f"releases/{OLD}/deploy-report.json": _done(_report(previous=None)),
        f"releases/{NEW}/RELEASE_MANIFEST.json": _done(_manifest("a" * 40, q="4.0")),
        f"releases/{OLD}/RELEASE_MANIFEST.json": _done(_manifest("b" * 40, dirty=True)),
        "uqs deploy verify --profile essential": _verified(health),
        "stream_health.read": _done(failing + "\n"),
    }
    return FakeRemote({**extra, **rules})


def _cfg():
    return make_config(artifact="", host="uqf-server", dest="/opt/uqf", profile="unused")


def test_it_reports_current_previous_and_how_many_others_are_kept():
    s = status.status(_cfg(), _server())
    assert s["format"] == status.FORMAT
    cur, prev = s["current"], s["previous"]
    assert (cur["release"], cur["revision"], cur["dirty"], cur["q"]) == (
        NEW,
        "a" * 40,
        False,
        "4.0",
    )
    assert cur["bundles"] == {"fx": {"version": "1"}} and cur["runtime"] == "uqf"
    assert (prev["release"], prev["dirty"]) == (OLD, True)
    assert s["releases"] == {"kept": 3, "others": 1, "names": [OLDEST, OLD, NEW]}
    assert s["lock"] == {"held": False, "owner": None}


def test_the_json_keys_are_the_documented_ones():
    """--json is for scripts: a renamed or removed key must bump FORMAT."""
    s = status.status(_cfg(), _server())
    assert set(s) == {
        "format", "host", "dest", "current", "previous", "releases",
        "last_deployment", "health", "lock",
    }  # fmt: skip
    assert set(s["health"]) == {"checked", "passed", "reason", "processes", "failing_jobs"}
    json.dumps(s)  # and all of it serialises


def test_it_never_takes_the_lock_or_changes_anything():
    remote = _server()
    status.status(_cfg(), remote)
    for script in remote.scripts:
        for writes in ("mkdir", "rm ", "ln -s", " > ", "mv ", "uqs start", "uqs stop"):
            assert writes not in script, f"status ran {writes!r}:\n{script}"


def test_health_runs_currents_own_verifier_with_its_extra_processes():
    remote = _server(health=False)
    s = status.status(_cfg(), remote)
    h = s["health"]
    assert h["checked"] and not h["passed"] and h["reason"] == "piggy1 did not answer"
    check = next(sc for sc in remote.scripts if "uqs deploy verify" in sc)
    assert f"cd /opt/uqf/releases/{NEW}\n" in check and "piggy1" in check
    assert "--deadline 180" in check, "the verifier gets the configured deadline"


def test_the_health_timeout_is_the_verifiers_deadline():
    """`--health-timeout` reaches the verifier, so a dead process costs
    seconds, not a push's three-minute readiness wait."""
    remote = _server()
    cfg = make_config(
        artifact="", host="uqf-server", dest="/opt/uqf", profile="unused", verify_timeout=7
    )
    status.status(cfg, remote)
    check = next(sc for sc in remote.scripts if "uqs deploy verify" in sc)
    assert "--deadline 7" in check


def test_failing_streaming_jobs_are_named():
    failing = json.dumps(
        {
            "fx_positions": {"process": "fx_positions", "failing": True, "failed": 3},
            "vectorize": {"process": "vectorize", "failing": False},
        }
    )
    s = status.status(_cfg(), _server(failing=failing))
    assert s["health"]["failing_jobs"] == ["fx_positions"]
    assert "batches failing: fx_positions" in status.render(s)


def test_a_release_from_before_stream_health_says_it_cannot_tell():
    s = status.status(_cfg(), _server(failing="null"))
    assert s["health"]["failing_jobs"] is None
    assert "cannot report them" in status.render(s)


def test_the_last_deployment_is_the_newest_release_even_when_it_failed():
    """A failed push keeps its release directory and never becomes current."""
    remote = _server(
        facts=_facts(NEW, [OLD, NEW, FAILED]),
        **{
            f"releases/{FAILED}/deploy-report.json": _done(
                _report(
                    "failed",
                    stage="verify",
                    error="rdb1 never answered",
                    rollback="restarted and verified release " + OLD,
                )  # fmt: skip
            )
        },
    )
    s = status.status(_cfg(), remote)
    assert s["current"]["release"] == NEW
    last = s["last_deployment"]
    assert (last["release"], last["status"], last["stage"]) == (FAILED, "failed", "verify")
    text = status.render(s)
    assert f"{FAILED}: failed at verify" in text and "rdb1 never answered" in text


def test_a_push_that_failed_before_writing_its_report():
    remote = _server(facts=_facts(NEW, [NEW, FAILED]))
    s = status.status(_cfg(), remote)
    assert s["last_deployment"] == {"release": FAILED, "report": False}


def test_a_held_lock_and_its_owner_are_reported():
    owner = "2026-10-08T13:00:00+00:00 release-pending"
    s = status.status(_cfg(), _server(facts=_facts(NEW, [OLD, NEW], lock_owner=owner)))
    assert s["lock"] == {"held": True, "owner": owner}
    assert status.render(s).splitlines()[1] == f"  LOCK      held: {owner}"


def test_a_server_with_nothing_deployed():
    s = status.status(_cfg(), _server(facts=_facts(None, [])))
    assert s["current"] is None and s["last_deployment"] is None
    assert s["health"] == {"checked": False, "reason": "no current release"}
    assert "current   none" in status.render(s)


def test_no_health_skips_the_verifier():
    remote = _server()
    s = status.status(_cfg(), remote, health=False)
    assert s["health"]["checked"] is False
    assert not any("uqs deploy verify" in sc for sc in remote.scripts)


def test_the_cli_is_registered():
    result = CliRunner().invoke(cli.app, ["deploy", "status", "--help"])
    assert result.exit_code == 0 and "--json" in result.output


def test_the_cli_prints_json_and_exits_1_only_when_health_fails(monkeypatch):
    """The exit code is for scripts: 0 healthy (or not checked), 1 failing."""
    from uqs.cli import deploy_server

    args = ["deploy", "status", "--host", "uqf-server", "--dest", "/opt/uqf", "--json"]
    for healthy, code in ((True, 0), (False, 1)):
        monkeypatch.setattr(deploy_server, "Remote", lambda *a, h=healthy, **kw: _server(health=h))
        result = CliRunner().invoke(cli.app, args)
        assert result.exit_code == code, result.output
        assert json.loads(result.output)["health"]["passed"] is healthy
    result = CliRunner().invoke(cli.app, [*args[:-1], "--no-health"])
    assert result.exit_code == 0 and "health    not checked: --no-health" in result.output
