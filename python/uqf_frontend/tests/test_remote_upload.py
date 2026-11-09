"""PUT /services/{target}/files: uploads to a registered target (#1951).

ssh is stubbed as in test_remote_files.py: a Recorder answers with a canned
result, for the gates that refuse before ssh and for each failure ssh or the
server can report; a byte-clean loopback plays sshd, running the remote
command with a local shell and the framed stdin, so the real quoting,
bootstrap and helper write into a temporary deployment.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import shlex
import sqlite3
import stat
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
RELEASE = "20261101T090000000Z-abc1234"
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

API_TS = Path(__file__).resolve().parents[3] / "web" / "src" / "api" / "services.ts"


class Recorder:
    """A runner that records each call and answers with `result`."""

    def __init__(self, returncode: int = 0, stdout: bytes | str = "", stderr: bytes | str = ""):
        self.calls: list[tuple[list[str], dict]] = []
        self.result = (returncode, stdout, stderr)

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        code, out, err = self.result
        return subprocess.CompletedProcess(argv, code, out, err)


class Loopback(Recorder):
    """sshd, locally, carrying stdin as bytes the way ssh does."""

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        assert argv[0] == "ssh"
        return subprocess.run(
            ["/bin/sh", "-c", argv[-1]],
            input=kwargs["input"],
            capture_output=True,
            text=kwargs.get("text", False),
            timeout=kwargs["timeout"],
            check=False,
        )


def _deployment(dest: Path, python: bool = True) -> Path:
    """A deployment root as `uqs deploy push` lays it out, its python a link
    to this interpreter, and shared/config holding one file."""
    rel = dest / "releases" / RELEASE
    rel.mkdir(parents=True)
    (dest / "current").symlink_to(f"releases/{RELEASE}")
    if python:
        (rel / ".venv" / "bin").mkdir(parents=True)
        (rel / ".venv" / "bin" / "python").symlink_to(sys.executable)
    (dest / "shared" / "config").mkdir(parents=True)
    (dest / "shared" / "config" / "sources.csv").write_text("a,b\n")
    return dest


def _client(
    runner, *, writes: bool = True, token: str | None = TOKEN, activity_db: Path | None = None
) -> TestClient:
    settings = (
        Settings(enable_writes=True, write_token=TOKEN, activity_db=activity_db)
        if writes
        else Settings()
    )
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return TestClient(
        create_app(gateway=FakeGateway(), settings=settings, remote_runner=runner),
        base_url="http://localhost",
        headers=headers,
    )


@pytest.fixture
def served(tmp_path, monkeypatch):
    dest = _deployment(tmp_path / "srv-1.x" / "uqf")
    registry = tmp_path / "deploy_targets.toml"
    registry.write_text(f'[targets.box]\nssh = "ops@box"\ndest = "{dest}"\n')
    monkeypatch.setenv("UQS_DEPLOY_TARGETS", str(registry))
    monkeypatch.setenv("UQS_DEPLOY_HISTORY", str(tmp_path / "deploy_history.json"))
    monkeypatch.setattr(sys.modules[__name__], "SERVED", dest)
    return dest


#: The deployment `served` made: _put opens each upload's push in it, in
#: process, so a test's runner sees only the upload (#2002).
SERVED: Path | None = None
#: The push an upload names when no deployment is served - a canned runner
#: never reads it.
CANNED_PUSH = "20261104T000000000Z-00000000"


def _open_push() -> str:
    if SERVED is None:
        return CANNED_PUSH
    push_id = remote_files.new_push_id()
    remote_helper.handle({"protocol": 1, "op": "push_open", "root": str(SERVED),
                          "push_id": push_id, "config_dir": "shared/config"})  # fmt: skip
    return push_id


def _put(runner, path, data=b"a,b\n", target="box", overwrite=None, push=None, **kw):
    params = {"path": path, "push_id": push or _open_push()}
    if overwrite is not None:
        params["overwrite"] = overwrite
    client = kw.pop("client", None) or _client(runner)
    return client.put(f"/services/{target}/files", params=params, content=data, **kw)


def _ok_answer(path, data, **over):
    return json.dumps({
        "protocol": 1, "ok": True, "path": path, "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(), "replaced": False, "mode": 0o644,
        "created_dirs": [], "swept": 0, "push_id": CANNED_PUSH, "previous_size": None,
        "previous_sha256": None, **over,
    }) + "\n"  # fmt: skip


# ------------------------------------------------------------ gates before ssh


def test_uploading_needs_writes_enabled(served):
    runner = Recorder()
    client = _client(runner, writes=False, token=None)
    resp = _put(runner, "shared/config/x", client=client)
    assert resp.status_code == 403
    assert resp.json()["error"] == "WritesDisabled"
    assert runner.calls == []


@pytest.mark.parametrize("token", [None, "wrong"])
def test_uploading_needs_the_write_token(served, token):
    runner = Recorder()
    resp = _put(runner, "shared/config/x", client=_client(runner, token=token))
    assert resp.status_code == 401
    assert runner.calls == []


def test_a_host_not_allowed_is_refused_before_ssh(served):
    runner = Recorder()
    resp = _put(runner, "shared/config/x", headers={"Host": "evil.example"})
    assert resp.status_code == 403
    assert resp.json()["error"] == "HostNotAllowed"
    assert runner.calls == []


def test_only_a_registered_target_takes_an_upload(served):
    runner = Recorder()
    for name in ("nope", "ops@box"):
        assert _put(runner, "shared/config/x", target=name).status_code == 404
    assert runner.calls == []


@pytest.mark.parametrize(
    ("path", "error"),
    [
        ("/etc/passwd", "UploadRefused"),
        ("shared/config/../../etc/x", "UploadRefused"),
        ("../x", "UploadRefused"),
        ("shared/config/a\x00b", "UploadRefused"),
        ("shared/data/hdb/x", "UploadRefused"),
        ("releases/r1/config/x", "UploadRefused"),
        ("current/x", "UploadRefused"),
        ("shared/config", "UploadRefused"),
        (f"shared/config/{remote_helper.TEMP_PREFIX}x.tmp", "UploadRefused"),
    ],
)
def test_unsafe_paths_and_unsupported_destinations_are_refused_before_ssh(served, path, error):
    runner = Recorder()
    resp = _put(runner, path)
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == error
    assert runner.calls == []


def test_a_path_is_required(served):
    runner = Recorder()
    resp = _client(runner).put("/services/box/files", content=b"x")
    assert resp.status_code == 422
    assert runner.calls == []


def test_a_body_over_the_limit_is_refused_before_ssh(served, monkeypatch):
    monkeypatch.setattr(remote_helper, "MAX_UPLOAD", 10)
    runner = Recorder()
    resp = _put(runner, "shared/config/x", data=b"x" * 11)
    assert resp.status_code == 413
    assert resp.json()["error"] == "UploadTooLarge"
    assert runner.calls == []


def test_a_body_that_grows_past_the_limit_without_a_length_is_refused(served, monkeypatch):
    monkeypatch.setattr(remote_helper, "MAX_UPLOAD", 10)
    runner = Recorder()

    def chunks():
        for _ in range(5):
            yield b"xxxx"

    resp = _put(runner, "shared/config/x", data=chunks())
    assert resp.status_code == 413
    assert runner.calls == []


def test_the_upload_function_refuses_too_large_data_itself(served, monkeypatch):
    monkeypatch.setattr(remote_helper, "MAX_UPLOAD", 3)
    from uqf_frontend.config import Settings

    with pytest.raises(remote_files.UploadTooLarge):
        remote_files.upload(
            Settings(), "box", "shared/config/x", b"xxxx", False, CANNED_PUSH, Recorder()
        )


# ------------------------------------------------------------ the ssh call


def test_one_batch_mode_call_framed_as_the_helper_then_the_bytes(served):
    data = b"\x00\x01binary\r\n"
    runner = Loopback()
    push_id = _open_push()
    resp = _put(runner, "shared/config/x.bin", data=data, push=push_id)
    assert resp.status_code == 200, resp.text
    assert len(runner.calls) == 1
    argv, kwargs = runner.calls[0]
    assert argv[:5] == ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    assert argv[5] == "ops@box" and len(argv) == 7
    source = Path(remote_helper.__file__).read_bytes()
    assert kwargs["input"] == b"%d\n" % len(source) + source + data
    assert kwargs["timeout"] == remote_files.UPLOAD_TIMEOUT
    assert "text" not in kwargs
    # what it ran: the release's python with the fixed bootstrap and one base64 word
    inner = shlex.split(shlex.split(argv[-1])[2])
    at = inner.index("exec")
    assert inner[at + 1 : at + 5] == [
        f"{served}/current/.venv/bin/python", "-I", "-c", remote_files.BOOTSTRAP,
    ]  # fmt: skip
    request = json.loads(base64.b64decode(inner[-1]))
    assert request == {
        "protocol": 1, "op": "upload", "root": str(served), "path": "shared/config/x.bin",
        "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "overwrite": False,
        "push_id": push_id,
    }  # fmt: skip
    assert (served / "shared/config/x.bin").read_bytes() == data


def test_the_answer_says_what_was_written(served):
    resp = _put(Loopback(), "shared/config/scripts/torqconfig/sources.csv", data=b"a,b\n")
    body = resp.json()
    assert body == {
        "target": "box",
        "dest": str(served),
        "path": "shared/config/scripts/torqconfig/sources.csv",
        "size": 4,
        "sha256": hashlib.sha256(b"a,b\n").hexdigest(),
        "replaced": False,
        "mode": body["mode"],
        "created_dirs": ["shared/config/scripts", "shared/config/scripts/torqconfig"],
        "push_id": body["push_id"],
        "previous_size": None,
        "previous_sha256": None,
    }


@pytest.mark.parametrize("name", UNUSUAL)
def test_an_unusual_name_never_reaches_the_shell(served, name):
    runner = Loopback()
    resp = _put(runner, f"shared/config/{name}/{name}", data=b"12345")
    assert resp.status_code == 200, resp.text
    assert (served / "shared/config" / name / name).read_bytes() == b"12345"
    assert name not in runner.calls[0][0][-1]
    assert not list(served.parent.rglob("pwned")) and not list(Path.cwd().glob("pwned"))


def test_a_service_account_is_reached_through_sudo(served, tmp_path, monkeypatch):
    registry = tmp_path / "deploy_targets.toml"
    registry.write_text(
        f'[targets.svc]\nssh = "ops@box"\ndest = "{served}"\nremote_user = "svc-uqf"\n'
    )
    runner = Recorder(stdout=_ok_answer("shared/config/x", b"a,b\n"))
    assert _put(runner, "shared/config/x", target="svc").status_code == 200
    words = shlex.split(runner.calls[0][0][-1])
    assert words[:5] == ["sudo", "-n", "-iu", "svc-uqf", "bash"]


def test_the_bootstrap_is_one_line(served):
    """`sudo -i` escapes a newline in its command into a line continuation."""
    assert "\n" not in remote_files.BOOTSTRAP


# ------------------------------------------------------------ overwrite


def test_an_existing_file_is_refused_unless_overwrite_is_asked(served):
    existing = served / "shared/config/sources.csv"
    os_mode = 0o640
    existing.chmod(os_mode)
    resp = _put(Loopback(), "shared/config/sources.csv", data=b"new\n")
    assert resp.status_code == 409
    assert resp.json()["error"] == "UploadExists"
    assert existing.read_text() == "a,b\n"
    resp = _put(Loopback(), "shared/config/sources.csv", data=b"new\n", overwrite="false")
    assert resp.status_code == 409
    resp = _put(Loopback(), "shared/config/sources.csv", data=b"new\n", overwrite="true")
    assert resp.status_code == 200, resp.text
    assert resp.json()["replaced"] is True
    assert existing.read_text() == "new\n"
    assert stat.S_IMODE(existing.stat().st_mode) == os_mode
    assert resp.json()["mode"] == os_mode


def test_a_link_out_of_the_root_is_refused_by_the_server(served, tmp_path):
    (tmp_path / "outside").mkdir()
    (served / "shared/config/out").symlink_to(tmp_path / "outside")
    resp = _put(Loopback(), "shared/config/out/x.csv")
    assert resp.status_code == 422
    assert "symbolic link" in resp.json()["detail"]
    assert list((tmp_path / "outside").iterdir()) == []


# ------------------------------------------------------------ certain failures


def test_a_release_without_its_python_writes_nothing(tmp_path, monkeypatch):
    dest = _deployment(tmp_path / "srv", python=False)
    registry = tmp_path / "deploy_targets.toml"
    registry.write_text(f'[targets.box]\nssh = "ops@box"\ndest = "{dest}"\n')
    monkeypatch.setenv("UQS_DEPLOY_TARGETS", str(registry))
    runner = Loopback()
    resp = _put(runner, "shared/config/x")
    assert resp.status_code == 409
    assert "missing or not executable" in resp.json()["detail"]
    assert len(runner.calls) == 1
    assert not (dest / "shared/config/x").exists()


def test_bytes_that_arrive_damaged_publish_nothing(served):
    """The helper checks the size and sha256 the API computed."""

    class Corrupting(Loopback):
        def __call__(self, argv, **kwargs):
            data = bytearray(kwargs["input"])
            data[-1] ^= 0xFF
            return super().__call__(argv, **{**kwargs, "input": bytes(data)})

    resp = _put(Corrupting(), "shared/config/x.csv", data=b"hello")
    assert resp.status_code == 502
    assert resp.json()["error"] == "UploadIncomplete"
    assert resp.json()["transient"] is False
    assert not (served / "shared/config/x.csv").exists()


def test_no_ssh_binary_is_a_failure_with_nothing_sent(served):
    def missing(argv, **kwargs):
        raise FileNotFoundError("ssh")

    resp = _put(missing, "shared/config/x")
    assert resp.status_code == 502
    assert "nothing was sent" in resp.json()["detail"]


# ------------------------------------------------------------ uncertain outcomes


def _uncertain(resp):
    assert resp.status_code == 504, resp.text
    body = resp.json()
    assert body["error"] == "UploadOutcomeUncertain"
    assert body["transient"] is False  # a retry could write what was written
    assert "may or may not have been written" in body["detail"]
    assert "nothing is retried automatically" in body["detail"]
    return body


def test_a_timeout_is_uncertain_and_not_retried(served):
    calls = []

    def slow(argv, **kwargs):
        calls.append(argv)
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    _uncertain(_put(slow, "shared/config/x"))
    assert len(calls) == 1


def test_ssh_failing_is_uncertain_since_it_may_have_dropped_mid_upload(served):
    runner = Recorder(255, stderr=b"Connection reset by peer")
    _uncertain(_put(runner, "shared/config/x"))
    assert len(runner.calls) == 1


def test_a_crashed_helper_is_uncertain_with_what_it_said(served):
    body = _uncertain(_put(Recorder(1, stderr=b"Traceback\nOSError: boom"), "shared/config/x"))
    assert "boom" in body["detail"]


@pytest.mark.parametrize("stdout", [b"", b"not json\n", b'{"protocol": 99, "ok": true}\n'])
def test_a_lost_answer_is_uncertain(served, stdout):
    _uncertain(_put(Recorder(stdout=stdout), "shared/config/x"))


def test_a_confirmation_of_other_bytes_is_uncertain(served):
    answer = _ok_answer("shared/config/x", b"other").encode()
    _uncertain(_put(Recorder(stdout=answer), "shared/config/x", data=b"a,b\n"))


# ------------------------------------------------------------ one limit, three places


def _ts_const(name: str) -> str:
    match = re.search(rf"export const {name} = (.+?);", API_TS.read_text())
    assert match, f"{name} is not in {API_TS}"
    return match.group(1)


def test_the_browser_holds_the_same_upload_limit_and_destination():
    expr = _ts_const("UPLOAD_MAX_BYTES")
    assert re.fullmatch(r"[\d\s*]+", expr), expr
    assert eval(expr) == remote_helper.MAX_UPLOAD  # noqa: S307 - digits and * only
    assert json.loads(_ts_const("UPLOAD_ROOT")) == "/".join(remote_helper.UPLOAD_ROOT)
    assert json.loads(_ts_const("UPLOAD_TEMP_PREFIX")) == remote_helper.TEMP_PREFIX
    assert json.loads(_ts_const("UPLOAD_HISTORY_DIR")) == remote_helper.HISTORY_DIR
    assert int(_ts_const("UPLOAD_MAX_DEPTH")) == remote_helper.MAX_UPLOAD_DEPTH
    assert int(_ts_const("UPLOAD_MAX_PATH")) == remote_helper.MAX_PATH


def test_the_browser_waits_longer_than_the_server_does():
    expr = _ts_const("UPLOAD_TIMEOUT_MS")
    assert re.fullmatch(r"[\d\s*]+", expr), expr
    assert eval(expr) > remote_files.UPLOAD_TIMEOUT * 1000  # noqa: S307


# ------------------------------------------------------------ the audit (#1954)

SECRET = b"PRIVATE-FILE-CONTENTS-7f3a"


def _audited(tmp_path, runner, path="shared/config/x.csv", data=SECRET, **params):
    db = tmp_path / "activity.sqlite3"
    client = _client(runner, activity_db=db)
    resp = _put(runner, path, data=data, client=client, **params)
    rows = client.get("/activity", params={"source": "server"}).json()["events"]
    with sqlite3.connect(db) as conn:
        dump = "\n".join(repr(r) for r in conn.execute("SELECT * FROM activity"))
    return resp, rows, dump


def test_an_upload_is_audited_with_metadata_and_never_its_bytes(served, tmp_path):
    resp, rows, dump = _audited(tmp_path, Loopback())
    assert resp.status_code == 200, resp.text
    (row,) = rows
    assert row["action"] == "files.upload"
    assert row["target"] == "box"
    assert row["outcome"] == "succeeded"
    assert row["detail"] == {
        "path": "shared/config/x.csv",
        "size": len(SECRET),
        "sha256": hashlib.sha256(SECRET).hexdigest(),
        "mode": "create",
        "push_id": resp.json()["push_id"],
        "previous_sha256": None,
    }
    assert SECRET.decode() not in dump
    assert base64.b64encode(SECRET).decode() not in dump


def test_an_uncertain_upload_is_audited_as_unknown(served, tmp_path):
    def slow(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    resp, (row,), dump = _audited(tmp_path, slow)
    assert resp.status_code == 504
    assert row["outcome"] == "unknown"
    assert row["detail"]["error"] == "UploadOutcomeUncertain"
    assert SECRET.decode() not in dump


def test_a_refused_upload_is_audited_as_refused_even_after_it_began(served, tmp_path):
    """The file exists: the helper refused after ssh opened, nothing written."""
    resp, (row,), _ = _audited(tmp_path, Loopback(), path="shared/config/sources.csv")
    assert resp.status_code == 409
    assert row["outcome"] == "refused"
    assert row["detail"]["error"] == "UploadExists"


def test_damaged_bytes_are_audited_as_failed(served, tmp_path):
    class Corrupting(Loopback):
        def __call__(self, argv, **kwargs):
            data = bytearray(kwargs["input"])
            data[-1] ^= 0xFF
            return super().__call__(argv, **{**kwargs, "input": bytes(data)})

    resp, (row,), _ = _audited(tmp_path, Corrupting())
    assert resp.status_code == 502
    assert row["outcome"] == "failed"


def test_an_overwrite_is_audited_as_one(served, tmp_path):
    resp, (row,), _ = _audited(
        tmp_path, Loopback(), path="shared/config/sources.csv", overwrite="true"
    )
    assert resp.status_code == 200
    assert row["detail"]["mode"] == "overwrite"
