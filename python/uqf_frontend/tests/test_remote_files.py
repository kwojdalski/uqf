"""GET /services/{target}/files: the confined file browser's API (#1950).

ssh is stubbed two ways. A recording runner answers with a canned result,
for the gates that must refuse before ssh is opened and for each failure ssh
or the server can report. A loopback runner plays sshd: it runs the remote
command it was handed with a local shell, the helper's source on stdin, so
the real quoting, the real `[ -x python ]` check and the real helper run
against a temporary deployment - with no network and no ssh.
"""

from __future__ import annotations

import base64
import json
import secrets
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from uqf_frontend import remote_files, remote_helper
from uqf_frontend.app import create_app
from uqf_frontend.config import Settings
from uqf_frontend.gateway import FakeGateway

TOKEN = secrets.token_hex(32)
RELEASE = "20261101T090000Z-abc1234"

UNUSUAL = [
    "with space",
    "it's quoted",
    'double "quoted"',
    "new\nline",
    "-leading-dash",
    "zażółć gęślą jaźń",
    "$(touch pwned)",
    "`id`",
    "semi;colon",
    "a&b|c>d",
]


class Recorder:
    """A runner that records each call and answers with `result`."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.calls: list[tuple[list[str], dict]] = []
        self.result = (returncode, stdout, stderr)

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        code, out, err = self.result
        return subprocess.CompletedProcess(argv, code, out, err)


class Loopback(Recorder):
    """sshd, locally: the remote command through /bin/sh -c, as an sshd runs
    it through the login shell, with the stdin ssh would forward."""

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        assert argv[0] == "ssh"
        return subprocess.run(
            ["/bin/sh", "-c", argv[-1]],
            input=kwargs["input"],
            capture_output=True,
            text=True,
            timeout=kwargs["timeout"],
            check=False,
        )


def _deployment(dest: Path, python: bool = True) -> Path:
    rel = dest / "releases" / RELEASE
    rel.mkdir(parents=True)
    (rel / "deploy-report.json").write_text(
        json.dumps({"release": RELEASE, "revision": "abc1234", "status": "deployed"})
    )
    (dest / "current").symlink_to(f"releases/{RELEASE}")
    if python:
        (rel / ".venv" / "bin").mkdir(parents=True)
        (rel / ".venv" / "bin" / "python").symlink_to(sys.executable)
    (dest / "shared" / "config").mkdir(parents=True)
    (dest / "shared" / "config" / "sources.csv").write_text("a,b\n")
    return dest


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """Declare targets in a deploy_targets.toml where uqs reads it."""
    path = tmp_path / "deploy_targets.toml"
    monkeypatch.setenv("UQS_DEPLOY_TARGETS", str(path))
    monkeypatch.setenv("UQS_DEPLOY_HISTORY", str(tmp_path / "deploy_history.json"))

    def declare(**targets: dict) -> None:
        lines = []
        for name, t in targets.items():
            lines.append(f"[targets.{name}]")
            lines += [f"{k} = {json.dumps(v)}" for k, v in t.items()]
        path.write_text("\n".join(lines) + "\n")

    return declare


@pytest.fixture
def served(tmp_path, registry):
    """A target `box` whose deployment root is a local directory, the
    release's python a link to this interpreter. The registry holds a dest to
    letters, digits and `._-/`; test_the_dest_is_quoted_whatever_it_holds
    goes past that with the command alone."""
    dest = _deployment(tmp_path / "srv-1.x" / "uqf")
    registry(box={"ssh": "ops@box", "dest": str(dest)})
    return dest


def _client(runner, *, writes: bool = True, token: str | None = TOKEN) -> TestClient:
    settings = Settings(enable_writes=True, write_token=TOKEN) if writes else Settings()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return TestClient(
        create_app(gateway=FakeGateway(), settings=settings, remote_runner=runner),
        base_url="http://localhost",
        headers=headers,
    )


def _get(runner, target="box", **params):
    return _client(runner).get(f"/services/{target}/files", params=params)


# ------------------------------------------------------------ gates before ssh


def test_browsing_needs_remote_operations_enabled(served):
    runner = Recorder()
    resp = _client(runner, writes=False, token=None).get("/services/box/files")
    assert resp.status_code == 403
    assert resp.json()["error"] == "WritesDisabled"
    assert "UQF_FRONTEND_ENABLE_WRITES" in resp.json()["detail"]
    assert runner.calls == []


@pytest.mark.parametrize("token", [None, "wrong"])
def test_browsing_needs_the_write_token(served, token):
    """It reaches a server with this machine's ssh key, so a caller who
    could merely reach the port may not."""
    runner = Recorder()
    resp = _client(runner, token=token).get("/services/box/files")
    assert resp.status_code == 401
    assert resp.json()["error"] == "WriteTokenRequired"
    assert runner.calls == []


def test_a_host_not_allowed_is_refused_before_ssh(served):
    runner = Recorder()
    client = _client(runner)
    resp = client.get("/services/box/files", headers={"Host": "evil.example"})
    assert resp.status_code == 403
    assert resp.json()["error"] == "HostNotAllowed"
    assert runner.calls == []


def test_only_a_registered_target_can_be_browsed(served):
    runner = Recorder()
    # a host, even the registered one, is not a target name
    for name in ("ops@box", "box.example"):
        resp = _get(runner, target=name)
        assert resp.status_code == 404
        assert "only registered targets" in resp.json()["detail"]
    resp = _get(runner, target="nope")
    assert resp.status_code == 404
    assert runner.calls == []


def test_no_registry_means_no_target(registry):
    runner = Recorder()
    assert _get(runner).status_code == 404
    assert runner.calls == []


@pytest.mark.parametrize(
    "path", ["/etc", "/", "..", "../..", "shared/../../etc", "shared/..", "a\x00b"]
)
def test_an_escaping_path_is_refused_before_ssh(served, path):
    runner = Recorder()
    resp = _get(runner, path=path)
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"] == "BrowseRefused"
    assert runner.calls == []


@pytest.mark.parametrize("limit", [0, -5, remote_helper.MAX_LIMIT + 1])
def test_a_limit_out_of_bounds_is_refused(served, limit):
    runner = Recorder()
    resp = _get(runner, limit=limit)
    assert resp.status_code == 422
    assert runner.calls == []


def test_a_target_the_shape_check_refuses_is_not_reached(registry):
    registry(bad={"ssh": "ops@box", "dest": "/srv/uqf", "launch_python": "/usr/bin/python3"})
    runner = Recorder()
    resp = _get(runner, target="bad")
    assert resp.status_code in (404, 409), resp.text
    assert runner.calls == []


# ------------------------------------------------------------ the ssh call


def test_ssh_is_one_batch_mode_call_to_the_registered_host(served):
    runner = Loopback()
    resp = _get(runner, path="shared")
    assert resp.status_code == 200, resp.text
    assert len(runner.calls) == 1
    argv, kwargs = runner.calls[0]
    assert argv[:5] == ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    assert argv[5] == "ops@box"
    assert len(argv) == 7
    assert kwargs["input"] == Path(remote_helper.__file__).read_text()
    assert kwargs["timeout"] == remote_files.TIMEOUT


def test_the_remote_command_runs_only_the_release_python_and_the_helper(served):
    """Browsing never deploys or restarts: the one thing the server execs is
    the release's own interpreter reading the helper from stdin."""
    runner = Recorder(stdout=json.dumps(remote_helper.handle({})) + "\n")
    _get(runner, path="shared")
    words = shlex.split(runner.calls[0][0][-1])
    assert words[:2] == ["bash", "-c"] and len(words) == 3
    inner = shlex.split(words[2])
    python = f"{served}/current/.venv/bin/python"
    exec_at = inner.index("exec")
    assert inner[exec_at + 1 : -1] == [python, "-I", "-"]
    request = json.loads(base64.b64decode(inner[-1]))
    assert request == {"protocol": 1, "op": "list", "root": str(served),
                       "path": "shared", "limit": remote_files.DEFAULT_LIMIT}  # fmt: skip
    # every command the shell runs: the word at the start of each one
    tokens = list(shlex.shlex(words[2], posix=True, punctuation_chars=True))
    starts = {tokens[0]} | {
        tokens[i + 1] for i, t in enumerate(tokens[:-1]) if t in (";", "then", "if")
    }
    assert starts == {"if", "[", "then", "echo", "exit", "fi", "exec"}


def test_a_service_account_is_reached_through_sudo_as_uqs_target_does(served, registry):
    registry(svc={"ssh": "ops@box", "dest": str(served), "remote_user": "svc-uqf"})
    runner = Recorder(stdout=json.dumps(remote_helper.handle({})) + "\n")
    _get(runner, target="svc")
    words = shlex.split(runner.calls[0][0][-1])
    assert words[:5] == ["sudo", "-n", "-iu", "svc-uqf", "bash"]
    assert words[5] == "-c" and len(words) == 7


@pytest.mark.parametrize("name", UNUSUAL)
def test_an_unusual_path_never_reaches_the_shell(served, name):
    """The path travels as base64; the remote command holds only fixed text,
    the registry's dest and that one base64 word."""
    (served / "shared" / name).mkdir()
    (served / "shared" / name / name).write_text("12345")
    runner = Loopback()
    resp = _get(runner, path=f"shared/{name}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["path"] == f"shared/{name}"
    assert body["parent"] == "shared"
    assert {e["name"]: e["size"] for e in body["entries"]} == {name: 5}
    command = runner.calls[0][0][-1]
    assert name not in command
    assert not list(Path.cwd().glob("pwned")) and not (served / "pwned").exists()


def test_the_dest_is_quoted_whatever_it_holds(tmp_path):
    """The registry refuses a dest with shell metacharacters, but the command
    does not lean on that: one holding quotes, `$(...)`, backquotes, `;` and
    a space runs, through a real shell, and nothing it names is executed."""
    dest = _deployment(tmp_path / "srv'\"$(id>pwned); `id>pwned`" / "uqf")
    request = {"protocol": 1, "op": "list", "root": str(dest), "path": "shared"}
    command = remote_files.remote_command(str(dest), None, remote_helper.encode(request))
    done = subprocess.run(
        ["/bin/sh", "-c", command], input=remote_files.helper_source(),
        capture_output=True, text=True, cwd=tmp_path, check=False,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    answer = json.loads(done.stdout)
    assert answer["ok"] is True
    assert [e["name"] for e in answer["entries"]] == ["config"]
    assert not list(tmp_path.rglob("pwned"))


def test_a_registry_dest_with_shell_characters_is_never_reached(tmp_path, registry):
    registry(box={"ssh": "ops@box", "dest": "/srv/$(id)"})
    runner = Recorder()
    assert _get(runner).status_code == 409
    assert runner.calls == []


# ------------------------------------------------------------ the answer


def test_the_root_lists_entries_and_the_current_release(served):
    resp = _get(Loopback())
    body = resp.json()
    assert body["target"] == "box"
    assert body["path"] == "" and body["parent"] is None
    entries = {e["name"]: e for e in body["entries"]}
    assert entries["releases"]["kind"] == "dir"
    assert entries["current"]["kind"] == "symlink"
    assert entries["current"]["blocked"] is True
    assert body["release"]["release"] == RELEASE
    assert body["release"]["report"] == {
        "release": RELEASE,
        "revision": "abc1234",
        "status": "deployed",
    }


def test_a_symlink_escape_is_refused_by_the_server(served, tmp_path):
    (tmp_path / "outside").mkdir()
    (served / "shared" / "out").symlink_to(tmp_path / "outside")
    resp = _get(Loopback(), path="shared/out")
    assert resp.status_code == 400
    assert "symbolic link" in resp.json()["detail"]


def test_a_missing_directory_is_404(served):
    resp = _get(Loopback(), path="shared/nope")
    assert resp.status_code == 404
    assert resp.json()["error"] == "PathNotFound"


def test_a_long_listing_is_truncated_at_the_limit(served):
    for i in range(12):
        (served / "shared" / "config" / f"f{i:02d}").write_text("")
    body = _get(Loopback(), path="shared/config", limit=5).json()
    assert len(body["entries"]) == 5
    assert body["truncated"] is True
    assert body["total"] == 13


# ------------------------------------------------------------ failures


def test_a_release_without_its_python_is_refused_with_no_fallback(tmp_path, registry):
    dest = _deployment(tmp_path / "srv", python=False)
    registry(box={"ssh": "ops@box", "dest": str(dest)})
    runner = Loopback()
    resp = _get(runner)
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert f"{dest}/current/.venv/bin/python is missing or not executable" in detail
    assert "installs nothing" in detail
    # one attempt, and it ran no other interpreter
    assert len(runner.calls) == 1
    assert "python3" not in runner.calls[0][0][-1]


def test_a_target_with_no_release_says_so(tmp_path, registry):
    (tmp_path / "empty").mkdir()
    registry(box={"ssh": "ops@box", "dest": str(tmp_path / "empty")})
    resp = _get(Loopback())
    assert resp.status_code == 409
    assert "no deployed release" in resp.json()["detail"]


def test_ssh_that_cannot_connect_is_told_apart(served):
    resp = _get(Recorder(255, stderr="ssh: connect to host box port 22: Connection refused"))
    assert resp.status_code == 502
    assert "could not reach target box" in resp.json()["detail"]


def test_a_timeout_is_a_transient_504(served):
    def slow(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    resp = _get(slow)
    assert resp.status_code == 504
    assert resp.json()["transient"] is True


def test_no_ssh_binary_is_a_502(served):
    def missing(argv, **kwargs):
        raise FileNotFoundError("ssh")

    assert _get(missing).status_code == 502


@pytest.mark.parametrize("stdout", ["", "not json\n", '{"protocol": 99, "ok": true}\n'])
def test_an_answer_the_api_does_not_understand_is_a_502(served, stdout):
    resp = _get(Recorder(stdout=stdout))
    assert resp.status_code == 502
    assert "no answer it understands" in resp.json()["detail"]


def test_a_crashed_helper_is_a_502_with_what_it_said(served):
    resp = _get(Recorder(1, stderr="Traceback ...\nOSError: boom"))
    assert resp.status_code == 502
    assert "boom" in resp.json()["detail"]


def test_a_login_banner_before_the_answer_is_skipped(served):
    answer = remote_helper.handle({})  # a refusal is still an answer
    resp = _get(Recorder(stdout="Welcome to box\n" + json.dumps(answer) + "\n"))
    assert resp.status_code == 422  # bad_request -> ValidationFailed


def test_api_confirmation_and_auth_before_ssh(served):
    runner = Recorder()
    for payload in (
        {"paths": ["shared/config/sources.csv"]},
        {"paths": ["shared/config/sources.csv"], "confirmed": False},
    ):
        assert _client(runner).post("/services/box/files/delete", json=payload).status_code in (
            400,
            422,
        )
    assert (
        _client(runner, token=None)
        .post("/services/box/files/delete", json={"paths": ["x"], "confirmed": True})
        .status_code
        == 401
    )
    assert (
        _client(runner, writes=False)
        .post("/services/box/files/delete", json={"paths": ["x"], "confirmed": True})
        .status_code
        == 403
    )
    assert not runner.calls


def test_api_loopback_reports_each_result(served):
    response = _client(Loopback()).post(
        "/services/box/files/delete",
        json={
            "paths": ["shared/config/sources.csv", "shared/config", "missing"],
            "confirmed": True,
        },
    )
    assert response.status_code == 200, response.text
    assert [r["deleted"] for r in response.json()["results"]] == [True, True, False]
    assert not (served / "shared/config").exists()


@pytest.mark.parametrize("paths", [["../escape"], ["/etc/passwd"], [""], ["x"] * 101])
def test_deletion_invalid_paths_are_refused_before_ssh(served, paths):
    runner = Recorder()
    response = _client(runner).post(
        "/services/box/files/delete", json={"paths": paths, "confirmed": True}
    )
    assert response.status_code in (400, 422)
    assert runner.calls == []


def test_deletion_checks_host_before_ssh(served):
    runner = Recorder()
    response = _client(runner).post(
        "/services/box/files/delete",
        json={"paths": ["x"], "confirmed": True},
        headers={"Host": "evil.example"},
    )
    assert response.status_code == 403
    assert not runner.calls


def test_deletion_audit_preserves_partial_results(served):
    client = _client(Loopback())
    response = client.post(
        "/services/box/files/delete",
        json={"paths": ["shared/config/sources.csv", "missing"], "confirmed": True},
    )
    assert response.status_code == 200
    rows = client.get("/activity", params={"action": "files.delete"}).json()["events"]
    assert len(rows) == 1
    assert rows[0]["outcome"] == "failed"
    assert [item["outcome"] for item in rows[0]["items"]] == ["succeeded", "failed"]


def test_deletion_timeout_is_audited_as_unknown_and_not_retried(served):
    calls = []

    def timeout(argv, **kwargs):
        calls.append(argv)
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    client = _client(timeout)
    response = client.post(
        "/services/box/files/delete",
        json={"paths": ["shared/config/sources.csv"], "confirmed": True},
    )
    assert response.status_code == 504
    assert "unknown" in response.json()["detail"]
    (row,) = client.get("/activity", params={"action": "files.delete"}).json()["events"]
    assert row["outcome"] == "unknown"
    assert len(calls) == 1
