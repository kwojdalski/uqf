"""`uqs target config push` against this API: a config push made from the
command line, as the Files view makes one (#2786).

The API is the frontend's own app (uqf_frontend.app.create_app), served in
process: the command's requests go to it through a TestClient in place of
urllib, and its ssh is the byte-clean loopback test_config_history.py uses,
so the real helper writes a temporary deployment. The command's own unit
tests are python/uqs/tests/test_target_config_push.py; this package's
environment is the one with fastapi. What the push leaves - the
files, the server's push history and the activity rows - is read back as the
browser's push would be.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from uqf_frontend import remote_helper
from uqf_frontend.app import create_app
from uqf_frontend.config import Settings
from uqf_frontend.gateway import FakeGateway
from uqs.cli import app
from uqs.cli.target import target_config_push
from uqs.deploy import portal_push

TOKEN = secrets.token_hex(32)
RELEASE = "20261101T090000000Z-abc1234"
API = "http://localhost"


class Loopback:
    """sshd, locally, carrying stdin the way ssh does. `fail` maps a call's
    number (1-based) to the exit code ssh answers it with, unrun."""

    def __init__(self, fail: dict[int, int] | None = None):
        self.calls = 0
        self.fail = fail or {}

    def __call__(self, argv, **kwargs):
        self.calls += 1
        if self.calls in self.fail:
            return subprocess.CompletedProcess(argv, self.fail[self.calls], b"", b"")
        return subprocess.run(
            ["/bin/sh", "-c", argv[-1]], input=kwargs["input"], capture_output=True,
            text=kwargs.get("text", False), timeout=kwargs["timeout"], check=False,
        )  # fmt: skip


class Api:
    """The frontend app, and every request the command sent it."""

    def __init__(self, tmp_path: Path, runner: Loopback):
        settings = Settings(enable_writes=True, write_token=TOKEN,
                            activity_db=tmp_path / "activity.sqlite3")  # fmt: skip
        self.client = TestClient(
            create_app(gateway=FakeGateway(), settings=settings, remote_runner=runner),
            base_url=API,
        )
        self.sent: list[tuple[str, str, dict[str, str]]] = []
        #: called with each request before it is sent: may raise
        self.before = lambda method, url: None

    def send(self, method, url, headers, body, timeout):
        self.sent.append((method, url, dict(headers)))
        self.before(method, url)
        answer = self.client.request(method, url, headers=headers, content=body)
        return portal_push.Reply(answer.status_code, answer.content)

    def writes(self) -> list[tuple[str, str]]:
        return [(m, u) for m, u, _ in self.sent if m != "GET"]

    def rows(self) -> dict[str, list[dict]]:
        events = self.client.get("/activity", params={"source": "server"}).json()["events"]
        out: dict[str, list[dict]] = {}
        for row in events:
            out.setdefault(row["action"], []).append(row)
        return out

    def manifest(self, push_id: str) -> dict:
        answer = self.client.get(f"/services/box/config/pushes/{push_id}",
                                 headers={"Authorization": f"Bearer {TOKEN}"})  # fmt: skip
        assert answer.status_code == 200, answer.text
        return answer.json()["push"]


@pytest.fixture
def served(tmp_path, monkeypatch) -> Path:
    dest = tmp_path / "srv" / "uqf"
    rel = dest / "releases" / RELEASE
    (rel / ".venv" / "bin").mkdir(parents=True)
    (rel / ".venv" / "bin" / "python").symlink_to(sys.executable)
    (dest / "current").symlink_to(f"releases/{RELEASE}")
    (dest / "shared" / "config").mkdir(parents=True)
    (dest / "shared" / "config" / "app.env").write_bytes(b"a=1\n")
    registry = tmp_path / "deploy_targets.toml"
    registry.write_text(f'[targets.box]\nssh = "ops@box"\ndest = "{dest}"\n')
    monkeypatch.setenv("UQS_DEPLOY_TARGETS", str(registry))
    monkeypatch.setenv("UQS_DEPLOY_HISTORY", str(tmp_path / "deploy_history.json"))
    monkeypatch.setenv(portal_push.TOKEN_ENV, TOKEN)
    return dest


@pytest.fixture
def logged():
    from loguru import logger

    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="WARNING", format="{message}")
    yield lines
    logger.remove(sink)


def _api(tmp_path, monkeypatch, runner: Loopback | None = None) -> Api:
    api = Api(tmp_path, runner or Loopback())
    monkeypatch.setattr(target_config_push, "SEND", api.send)
    return api


def _push(*args: str):
    argv = ["target", "config", "push", "box", *args, "--api", API, "--claimed-user", "kw"]
    return CliRunner().invoke(app, argv, catch_exceptions=False)


def _local(tmp_path: Path, files: dict[str, bytes]) -> Path:
    root = tmp_path / "local"
    for name, data in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(data)
    return root


def _json(result) -> dict:
    return json.loads(result.stdout)


# ------------------------------------------------------------ a push, as the browser makes it


def test_a_folder_push_is_recorded_as_a_browser_push(served, tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    local = _local(tmp_path, {"helpers/a.q": b"a:1\n", "helpers/sub/b.csv": b"x,y\n",
                              "extra.env": b"k=v\n"})  # fmt: skip
    result = _push(str(local / "helpers"), str(local / "extra.env"), "--into", "jobs",
                   "--yes", "--json")  # fmt: skip
    assert result.exit_code == 0, result.output
    doc = _json(result)
    paths = ["shared/config/jobs/helpers/a.q", "shared/config/jobs/helpers/sub/b.csv",
             "shared/config/jobs/extra.env"]  # fmt: skip
    assert [f["path"] for f in doc["files"]] == paths
    assert {f["outcome"] for f in doc["files"]} == {"succeeded"}
    assert doc["files"][0]["sha256"] == hashlib.sha256(b"a:1\n").hexdigest()
    assert doc["dest"] == str(served) and doc["closed"] and doc["exit_code"] == 0
    assert (served / "shared/config/jobs/helpers/sub/b.csv").read_bytes() == b"x,y\n"

    # the browser's sequence: open, one PUT per file, close
    push_id = doc["push_id"]
    assert [m for m, _ in api.writes()] == ["POST", "PUT", "PUT", "PUT", "POST"]
    assert api.writes()[-1][1].endswith(f"/config/pushes/{push_id}/close")
    ids = [h["X-UQF-Request-Id"] for _, _, h in api.sent]
    assert len(set(ids)) == len(ids)
    assert {h["X-UQF-Session-Id"] for _, _, h in api.sent} == {doc["session_id"]}

    rows = api.rows()
    (opened,) = rows["config.push.open"]
    assert opened["detail"]["config_dir"] == "shared/config/jobs"
    assert opened["detail"]["file_count"] == 3 and opened["detail"]["total_bytes"] == 12
    uploads = {r["detail"]["path"]: r["detail"] for r in rows["files.upload"]}
    assert sorted(uploads) == sorted(paths)
    assert uploads[paths[1]]["size"] == 4 and uploads[paths[1]]["push_id"] == push_id
    assert uploads[paths[1]]["sha256"] == hashlib.sha256(b"x,y\n").hexdigest()
    (closed,) = rows["config.push"]
    assert closed["outcome"] == "succeeded"
    assert closed["items"] == [{"subject": p, "outcome": "succeeded"} for p in paths]
    every = [r for kind in rows.values() for r in kind]
    assert {r["claimed_user"] for r in every} == {"kw"}
    assert {r["session_id"] for r in every} == {doc["session_id"]}

    manifest = api.manifest(push_id)
    assert manifest["via"] == "frontend" and manifest["user"] == "kw"
    assert manifest["config_dir"] == "shared/config/jobs" and manifest["reported"] == []


def test_an_existing_file_is_refused_without_overwrite_and_the_rest_still_go(
    served, tmp_path, monkeypatch
):
    api = _api(tmp_path, monkeypatch)
    local = _local(tmp_path, {"app.env": b"a=2\n", "new.csv": b"n\n"})
    result = _push(str(local / "app.env"), str(local / "new.csv"), "--yes", "--json")
    assert result.exit_code == target_config_push.EXIT_PARTIAL, result.output
    doc = _json(result)
    by_path = {f["path"]: f for f in doc["files"]}
    assert by_path["shared/config/app.env"]["outcome"] == "refused"
    assert by_path["shared/config/app.env"]["http_status"] == 409
    assert by_path["shared/config/new.csv"]["outcome"] == "succeeded"
    assert (served / "shared/config/app.env").read_bytes() == b"a=1\n"
    (closed,) = api.rows()["config.push"]
    assert {i["subject"]: i["outcome"] for i in closed["items"]} == {
        "shared/config/app.env": "refused", "shared/config/new.csv": "succeeded"}  # fmt: skip

    again = _push(str(local / "app.env"), "--overwrite", "--yes", "--json")
    assert again.exit_code == 0, again.output
    (replaced,) = _json(again)["files"]
    assert replaced["replaced"] is True
    assert replaced["previous_sha256"] == hashlib.sha256(b"a=1\n").hexdigest()
    assert (served / "shared/config/app.env").read_bytes() == b"a=2\n"


def test_an_uncertain_upload_is_unknown_and_never_retried(served, tmp_path, monkeypatch):
    # ssh call 1 opens the push, 2 is the first upload: ssh fails (255), so
    # the API answers 504 UploadOutcomeUncertain
    api = _api(tmp_path, monkeypatch, Loopback(fail={2: 255}))
    local = _local(tmp_path, {"one.csv": b"1\n", "two.csv": b"2\n"})
    result = _push(str(local / "one.csv"), str(local / "two.csv"), "--yes", "--json")
    assert result.exit_code == target_config_push.EXIT_UNKNOWN, result.output
    doc = _json(result)
    one, two = doc["files"]
    assert one["outcome"] == "unknown" and one["http_status"] == 504
    assert one["error"] == "UploadOutcomeUncertain"
    assert two["outcome"] == "succeeded"
    puts = [u for m, u in api.writes() if m == "PUT"]
    assert len([u for u in puts if "one.csv" in u]) == 1
    manifest = api.manifest(doc["push_id"])
    assert manifest["reported"] == [{"path": "shared/config/one.csv", "outcome": "unknown"}]
    (closed,) = api.rows()["config.push"]
    assert closed["outcome"] == "unknown"


def test_invalid_files_are_never_sent_and_are_reported_at_close(served, tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    local = _local(tmp_path, {"ok.csv": b"ok\n", ".uqf-upload-x": b"temp\n"})
    big = local / "big.bin"
    with big.open("wb") as handle:
        handle.truncate(remote_helper.MAX_UPLOAD + 1)
    result = _push(str(local / "ok.csv"), str(local / ".uqf-upload-x"), str(big), "--yes",
                   "--json")  # fmt: skip
    assert result.exit_code == 3, result.output
    doc = _json(result)
    assert [f["outcome"] for f in doc["files"]] == ["succeeded", "invalid", "invalid"]
    assert "the upload's own" in doc["files"][1]["message"]
    assert "limit" in doc["files"][2]["message"]
    assert [u for m, u in api.writes() if m == "PUT" and "ok.csv" not in u] == []
    (opened,) = api.rows()["config.push.open"]
    assert opened["detail"]["file_count"] == 1  # the browser counts only what it queues
    assert api.manifest(doc["push_id"])["reported"] == [
        {"path": "shared/config/.uqf-upload-x", "outcome": "invalid"},
        {"path": "shared/config/big.bin", "outcome": "invalid"},
    ]


def test_a_dry_run_shows_the_push_and_sends_nothing(served, tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    local = _local(tmp_path, {"d/a.csv": b"a\n"})
    result = _push(str(local / "d"), "--into", "sub", "--dry-run")
    assert result.exit_code == 0, result.output
    assert f"to box at {served}/shared/config/sub/" in result.output
    assert "'shared/config/sub/d/a.csv' 2 bytes" in result.output
    assert "an upload whose path exists is refused" in result.output
    assert "activates nothing" in result.output
    assert api.writes() == []  # the target listing is read; nothing else
    assert api.rows() == {}
    assert not (served / "shared/config/sub").exists()


def test_ctrl_c_in_flight_leaves_that_file_unknown_and_cancels_the_rest(
    served, tmp_path, monkeypatch
):
    api = _api(tmp_path, monkeypatch)
    local = _local(tmp_path, {"a.csv": b"a\n", "b.csv": b"b\n", "c.csv": b"c\n"})

    def interrupt(method, url):
        if method == "PUT" and "b.csv" in url:
            raise KeyboardInterrupt

    api.before = interrupt
    result = _push(*(str(local / n) for n in ("a.csv", "b.csv", "c.csv")), "--yes", "--json")
    assert result.exit_code == target_config_push.EXIT_UNKNOWN, result.output
    doc = _json(result)
    assert [f["outcome"] for f in doc["files"]] == ["succeeded", "unknown", "cancelled"]
    assert doc["interrupted"] and doc["closed"]
    assert api.manifest(doc["push_id"])["reported"] == [
        {"path": "shared/config/b.csv", "outcome": "unknown"},
        {"path": "shared/config/c.csv", "outcome": "cancelled"},
    ]


def test_a_lost_connection_mid_upload_is_unknown(served, tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    local = _local(tmp_path, {"a.csv": b"a\n", "b.csv": b"b\n"})

    def drop(method, url):
        if method == "PUT" and "a.csv" in url:
            raise portal_push.Lost("connection reset")

    api.before = drop
    result = _push(str(local / "a.csv"), str(local / "b.csv"), "--yes")
    assert result.exit_code == target_config_push.EXIT_UNKNOWN, result.output
    assert "OUTCOME UNKNOWN" in result.output and "connection reset" in result.output
    assert "uqs target config show box" in result.output
    assert "list the directory before pushing" in result.output


def test_a_push_the_api_refuses_to_open_sends_no_file(served, tmp_path, monkeypatch, logged):
    api = _api(tmp_path, monkeypatch)
    monkeypatch.setenv(portal_push.TOKEN_ENV, "not-the-token")
    local = _local(tmp_path, {"a.csv": b"a\n"})
    result = _push(str(local / "a.csv"), "--yes")
    assert result.exit_code == 1
    assert any("could not be opened, so nothing was sent" in line for line in logged)
    assert [m for m, _ in api.writes()] == ["POST"]
    assert not (served / "shared/config/a.csv").exists()


def test_an_unknown_target_is_refused_naming_those_declared(served, tmp_path, monkeypatch, logged):
    _api(tmp_path, monkeypatch)
    local = _local(tmp_path, {"a.csv": b"a\n"})
    argv = ["target", "config", "push", "nope", str(local / "a.csv"), "--api", API, "--yes"]
    result = CliRunner().invoke(app, argv)
    assert result.exit_code == 1
    assert any("declares no target 'nope' (it declares: box)" in line for line in logged)
