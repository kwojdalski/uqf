"""Config pushes through the API: one push id, server-side history, diff and
restore (#2002).

ssh is the byte-clean loopback test_remote_upload.py uses: the remote
command runs with a local shell, so the real quoting, bootstrap and helper
act on a temporary deployment. The audit is read back from the activity
database and from every export format at every level, and searched for the
files' bytes.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import sqlite3
import subprocess
import sys
import typing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from uqf_frontend import remote_helper
from uqf_frontend.app import create_app
from uqf_frontend.config import Settings
from uqf_frontend.gateway import FakeGateway
from uqs import activity_log

TOKEN = secrets.token_hex(32)
RELEASE = "20261101T090000000Z-abc1234"
#: What the deployed file held, what the push writes: neither may reach the
#: activity store or an export.
OLD_SECRET = b"password=OLD-SECRET-41c2\n"
NEW_SECRET = b"password=NEW-SECRET-9e7d\n"


class Loopback:
    """sshd, locally, carrying stdin the way ssh does; `after` may change
    what the caller is told once the command has run."""

    def __init__(self, after=None):
        self.calls: list[list[str]] = []
        self.after = after

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        assert argv[0] == "ssh"
        done = subprocess.run(
            ["/bin/sh", "-c", argv[-1]], input=kwargs["input"], capture_output=True,
            text=kwargs.get("text", False), timeout=kwargs["timeout"], check=False,
        )  # fmt: skip
        return self.after(argv, done) if self.after else done


@pytest.fixture
def served(tmp_path, monkeypatch) -> Path:
    dest = tmp_path / "srv" / "uqf"
    rel = dest / "releases" / RELEASE
    (rel / ".venv" / "bin").mkdir(parents=True)
    (rel / ".venv" / "bin" / "python").symlink_to(sys.executable)
    (dest / "current").symlink_to(f"releases/{RELEASE}")
    (dest / "shared" / "config").mkdir(parents=True)
    (dest / "shared" / "config" / "app.env").write_bytes(OLD_SECRET)
    registry = tmp_path / "deploy_targets.toml"
    registry.write_text(f'[targets.box]\nssh = "ops@box"\ndest = "{dest}"\n')
    monkeypatch.setenv("UQS_DEPLOY_TARGETS", str(registry))
    monkeypatch.setenv("UQS_DEPLOY_HISTORY", str(tmp_path / "deploy_history.json"))
    return dest


def _client(runner, tmp_path: Path, writes: bool = True, **settings) -> TestClient:
    config = (
        Settings(enable_writes=True, write_token=TOKEN,
                 activity_db=tmp_path / "activity.sqlite3", **settings)
        if writes else Settings(activity_db=tmp_path / "activity.sqlite3")
    )  # fmt: skip
    return TestClient(
        create_app(gateway=FakeGateway(), settings=config, remote_runner=runner),
        base_url="http://localhost",
        headers={"Authorization": f"Bearer {TOKEN}", "x-uqf-user": "kw"},
    )


def _use(c: TestClient, runner) -> None:
    """Swap the ssh the app runs, mid-test."""
    typing.cast(typing.Any, c.app).state.remote_runner = runner


def _open(c: TestClient, files: int = 2, total: int = 50) -> str:
    body = {"destination": "shared/config", "file_count": files, "total_bytes": total}
    resp = c.post("/services/box/config/pushes", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["push_id"]


def _upload(c: TestClient, push: str, path: str, data: bytes, overwrite: bool = False):
    params = {"path": path, "push_id": push, "overwrite": str(overwrite).lower()}
    return c.put("/services/box/files/content", params=params, content=data)


def _rows(c: TestClient, **filters) -> list[dict]:
    return c.get("/activity", params={"source": "server", **filters}).json()["events"]


def _by_action(c: TestClient) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for row in _rows(c):
        out.setdefault(row["action"], []).append(row)
    return out


# ------------------------------------------------------------ one push, one id


def test_a_push_has_one_id_on_every_row_and_a_summary(served, tmp_path):
    c = _client(Loopback(), tmp_path)
    push = _open(c)
    replaced = _upload(c, push, "shared/config/app.env", NEW_SECRET, overwrite=True)
    created = _upload(c, push, "shared/config/sub/new.csv", b"a,b\n")
    assert replaced.status_code == created.status_code == 200
    assert replaced.json()["previous_sha256"] == hashlib.sha256(OLD_SECRET).hexdigest()
    assert created.json()["previous_sha256"] is None
    closed = c.post(f"/services/box/config/pushes/{push}/close", json={"reported": []})
    assert closed.status_code == 200, closed.text
    assert closed.json()["summary"]["states"] == {"published": 2, "pending": 0, "failed": 0}

    rows = _by_action(c)
    (opened,) = rows["config.push.open"]
    assert opened["detail"]["push_id"] == push and opened["outcome"] == "succeeded"
    uploads = {r["detail"]["file_name"]: r for r in rows["file.upload"]}
    assert {r["detail"]["push_id"] for r in uploads.values()} == {push}
    assert uploads["shared/config/app.env"]["detail"]["previous_sha256"] == (
        hashlib.sha256(OLD_SECRET).hexdigest()
    )
    assert uploads["shared/config/sub/new.csv"]["detail"]["previous_sha256"] is None
    (summary,) = rows["config.push"]
    assert summary["target"] == "box" and summary["outcome"] == "succeeded"
    assert summary["detail"] == {
        "push_id": push, "destination": "shared/config", "item_count": 2,
        "size_bytes": len(NEW_SECRET) + 4,
        # the x-uqf-user header is a claim, and the row says so (#2045)
        "identity": "claimed",
    }  # fmt: skip
    assert summary["items"] == [
        {"target": "shared/config/app.env", "outcome": "succeeded"},
        {"target": "shared/config/sub/new.csv", "outcome": "succeeded"},
    ]


def test_an_upload_must_name_its_push(served, tmp_path):
    runner = Loopback()
    c = _client(runner, tmp_path)
    missing = c.put("/services/box/files/content", params={"path": "shared/config/x"},
                    content=b"x")  # fmt: skip
    assert missing.status_code == 422
    malformed = _upload(c, "not-a-push", "shared/config/x", b"x")
    assert malformed.status_code == 422
    unknown = _upload(c, "20261104T000000000Z-00000000", "shared/config/x", b"x")
    assert unknown.status_code == 400 and "no push" in unknown.json()["detail"]
    assert not (served / "shared/config/x").exists()
    assert len(runner.calls) == 1  # the two malformed requests never reached ssh


def test_history_list_show_and_diff(served, tmp_path):
    c = _client(Loopback(), tmp_path)
    push = _open(c)
    _upload(c, push, "shared/config/app.env", NEW_SECRET, overwrite=True)
    c.post(f"/services/box/config/pushes/{push}/close", json={"reported": []})
    listed = c.get("/services/box/config/pushes").json()
    assert [p["push_id"] for p in listed["pushes"]] == [push]
    assert listed["pushes"][0]["user"] == "kw" and listed["pushes"][0]["via"] == "frontend"
    shown = c.get(f"/services/box/config/pushes/{push}").json()
    (entry,) = shown["push"]["files"]
    assert entry["action"] == "replace" and entry["now"] == "after"
    assert entry["previous_sha256"] == hashlib.sha256(OLD_SECRET).hexdigest()
    diff = c.get(f"/services/box/config/pushes/{push}/diff",
                 params={"path": "shared/config/app.env"}).json()  # fmt: skip
    (one,) = diff["files"]
    assert one["kind"] == "text"
    assert f"-{OLD_SECRET.decode()}+{NEW_SECRET.decode()}" in one["diff"]
    assert diff["diff_cap"] == remote_helper.DIFF_CAP


# ------------------------------------------------------------ failures


def test_a_partial_failure_is_consistent_in_history_and_audit(served, tmp_path):
    """The second file exists and replacing it was not asked: refused, the
    others land; the summary says each, and failed overall."""
    (served / "shared/config/b").write_bytes(b"there")
    c = _client(Loopback(), tmp_path)
    push = _open(c, files=3)
    assert _upload(c, push, "shared/config/a", b"1").status_code == 200
    assert _upload(c, push, "shared/config/b", b"2").status_code == 409
    assert _upload(c, push, "shared/config/c", b"3").status_code == 200
    closed = c.post(f"/services/box/config/pushes/{push}/close", json={"reported": []}).json()
    assert [(f["path"], f["state"]) for f in closed["push"]["files"]] == [
        ("shared/config/a", "published"), ("shared/config/b", "failed"),
        ("shared/config/c", "published"),
    ]  # fmt: skip
    (summary,) = _by_action(c)["config.push"]
    assert summary["outcome"] == "failed"
    assert [i["outcome"] for i in summary["items"]] == ["succeeded", "refused", "succeeded"]


def test_an_interrupted_push_is_never_retried_and_history_shows_what_landed(served, tmp_path):
    """The first upload lands, then the connection drops before its answer
    (uncertain); the second times out before it is sent (uncertain). Neither
    is retried; history shows the first landed and the second did not."""

    def dropped(argv, done):
        return subprocess.CompletedProcess(argv, 255, b"", b"Connection reset")

    sent: list[list[str]] = []

    def never_sent(argv, **kwargs):
        sent.append(argv)
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    c = _client(Loopback(), tmp_path)
    push = _open(c)
    dropping = Loopback(after=dropped)
    _use(c, dropping)
    first = _upload(c, push, "shared/config/landed", b"1")
    assert first.status_code == 504 and first.json()["error"] == "UploadOutcomeUncertain"
    assert len(dropping.calls) == 1
    _use(c, never_sent)
    second = _upload(c, push, "shared/config/lost", b"2")
    assert second.status_code == 504 and len(sent) == 1
    _use(c, Loopback())
    reported = [{"path": "shared/config/landed", "outcome": "unknown"},
                {"path": "shared/config/lost", "outcome": "unknown"}]  # fmt: skip
    closed = c.post(f"/services/box/config/pushes/{push}/close", json={"reported": reported})
    assert closed.status_code == 200, closed.text
    shown = c.get(f"/services/box/config/pushes/{push}").json()["push"]
    assert [(f["path"], f["state"], f["now"]) for f in shown["files"]] == [
        ("shared/config/landed", "published", "after"),
    ]
    assert shown["reported"] == [{"path": "shared/config/lost", "outcome": "unknown"}]
    assert not (served / "shared/config/lost").exists()
    rows = _by_action(c)
    assert {r["outcome"] for r in rows["file.upload"]} == {"unknown"}
    (summary,) = rows["config.push"]
    assert summary["outcome"] == "unknown"
    assert summary["items"] == [
        {"target": "shared/config/landed", "outcome": "succeeded"},
        {"target": "shared/config/lost", "outcome": "unknown"},
    ]


# ------------------------------------------------------------ restore


def test_restore_round_trip_is_a_confirmed_audited_push(served, tmp_path):
    c = _client(Loopback(), tmp_path)
    push = _open(c)
    _upload(c, push, "shared/config/app.env", NEW_SECRET, overwrite=True)
    _upload(c, push, "shared/config/new/x.csv", b"x")
    c.post(f"/services/box/config/pushes/{push}/close", json={"reported": []})
    plan = c.get(f"/services/box/config/pushes/{push}/restore").json()
    assert plan["dry_run"] and [p["step"] for p in plan["plan"]] == ["write", "delete"]
    assert (served / "shared/config/app.env").read_bytes() == NEW_SECRET  # nothing written yet
    unconfirmed = c.post(f"/services/box/config/pushes/{push}/restore", json={"confirmed": False})
    assert unconfirmed.status_code == 422
    resp = c.post(f"/services/box/config/pushes/{push}/restore", json={"confirmed": True})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["push_id"] and body["push_id"] != push
    assert (served / "shared/config/app.env").read_bytes() == OLD_SECRET
    assert not (served / "shared/config/new").exists()
    rows = _by_action(c)
    restored = [r for r in rows["config.restore"] if r["outcome"] == "succeeded"]
    (row,) = restored
    assert row["detail"]["restores"] == push and row["detail"]["push_id"] == body["push_id"]
    assert row["items"] == [
        {"target": "shared/config/app.env", "outcome": "succeeded"},
        {"target": "shared/config/new/x.csv", "outcome": "succeeded"},
    ]
    listed = c.get("/services/box/config/pushes").json()["pushes"]
    assert listed[0]["kind"] == "restore" and listed[0]["restores"] == push


def test_a_conflicting_restore_writes_nothing_and_is_refused(served, tmp_path):
    c = _client(Loopback(), tmp_path)
    push = _open(c)
    _upload(c, push, "shared/config/app.env", NEW_SECRET, overwrite=True)
    (served / "shared/config/app.env").write_bytes(b"edited by hand")
    resp = c.post(f"/services/box/config/pushes/{push}/restore", json={"confirmed": True})
    assert resp.status_code == 409 and resp.json()["error"] == "RestoreConflict"
    assert (served / "shared/config/app.env").read_bytes() == b"edited by hand"
    (row,) = _by_action(c)["config.restore"]
    assert row["outcome"] == "refused"


def test_a_lost_restore_answer_is_uncertain_and_not_retried(served, tmp_path):
    c = _client(Loopback(), tmp_path)
    push = _open(c)
    _upload(c, push, "shared/config/x", b"x")

    def lost(argv, done):
        return subprocess.CompletedProcess(argv, 0, "", "")

    runner = Loopback(after=lost)
    _use(c, runner)
    resp = c.post(f"/services/box/config/pushes/{push}/restore", json={"confirmed": True})
    assert resp.status_code == 504 and resp.json()["error"] == "RestoreOutcomeUncertain"
    assert "nothing is retried" in resp.json()["detail"]
    assert len(runner.calls) == 1
    assert not (served / "shared/config/x").exists()  # it did run: history will say so
    (row,) = _by_action(c)["config.restore"]
    assert row["outcome"] == "unknown"


def test_a_restore_after_pruning_is_refused_clearly(served, tmp_path):
    c = _client(Loopback(), tmp_path, config_history_keep=1)
    gone = _open(c)
    _upload(c, gone, "shared/config/x", b"x")
    newer = _open(c)
    resp = c.post(f"/services/box/config/pushes/{gone}/restore", json={"confirmed": True})
    assert resp.status_code == 410 and resp.json()["error"] == "HistoryPruned"
    assert "pruned" in resp.json()["detail"] and "can no longer be" in resp.json()["detail"]
    assert c.get(f"/services/box/config/pushes/{gone}").status_code == 410
    # pruning is audited: which push, by the open that pruned it
    (pruned,) = _by_action(c)["config.history.prune"]
    assert pruned["items"] == [{"target": gone, "outcome": "succeeded"}]
    assert pruned["detail"] == {"pruned_count": 1}
    opens = _by_action(c)["config.push.open"]
    assert [o["detail"]["pruned_count"] for o in opens if o["detail"]["push_id"] == newer] == [1]


# ------------------------------------------------------------ confinement and gates


@pytest.mark.parametrize("path", ["shared/config/.uqf-history", "shared/config/.UQF-HISTORY/x"])
def test_the_history_is_not_reachable_through_the_file_routes(served, tmp_path, path):
    runner = Loopback()
    c = _client(runner, tmp_path)
    push = _open(c)
    calls = len(runner.calls)
    assert c.get("/services/box/files", params={"path": path}).status_code == 400
    deleted = c.post("/services/box/files/delete", json={"paths": [path], "confirmed": True})
    assert deleted.status_code == 400
    assert _upload(c, push, f"{path}/y", b"evil").status_code == 400
    assert len(runner.calls) == calls  # each refused before ssh
    listing = c.get("/services/box/files", params={"path": "shared/config"}).json()
    (history,) = [e for e in listing["entries"] if e["name"] == ".uqf-history"]
    assert history["blocked"] is True


def test_the_history_needs_remote_operations(served, tmp_path):
    c = _client(Loopback(), tmp_path, writes=False)
    pushes = "/services/box/config/pushes"
    assert c.get(pushes).status_code == 403
    assert c.get(f"{pushes}/20261104T000000000Z-00000000").status_code == 403
    body = {"destination": "shared/config", "file_count": 1, "total_bytes": 1}
    assert c.post(pushes, json=body).status_code == 403
    assert c.get("/services/box/config/pushes/bad-id").status_code in (403, 422)


# ------------------------------------------------------------ no bytes in the audit


def test_no_file_bytes_reach_the_activity_store_or_any_export(served, tmp_path):
    """Every push action - open, overwrite, create, close, list, show, diff,
    plan, restore, prune - then the database file and every export format at
    every level, searched for the files' bytes, raw and base64."""
    c = _client(Loopback(), tmp_path, config_history_keep=2)
    push = _open(c)
    _upload(c, push, "shared/config/app.env", NEW_SECRET, overwrite=True)
    _upload(c, push, "shared/config/more.env", NEW_SECRET)
    c.post(f"/services/box/config/pushes/{push}/close", json={"reported": []})
    c.get("/services/box/config/pushes")
    c.get(f"/services/box/config/pushes/{push}")
    c.get(f"/services/box/config/pushes/{push}/diff")
    c.get(f"/services/box/config/pushes/{push}/restore")
    assert c.post(f"/services/box/config/pushes/{push}/restore",
                  json={"confirmed": True}).status_code == 200  # fmt: skip
    _open(c)  # prunes the oldest
    secrets_ = [OLD_SECRET.strip(), NEW_SECRET.strip()]
    needles = [s.decode() for s in secrets_] + [base64.b64encode(s).decode() for s in secrets_]
    needles += [s.decode().split("=")[1] for s in secrets_]
    db = tmp_path / "activity.sqlite3"
    raw = b"".join(p.read_bytes() for p in tmp_path.glob("activity.sqlite3*"))
    with sqlite3.connect(db) as conn:
        dump = "\n".join(conn.iterdump())
    exports = [
        c.get("/activity/export", params={"format": fmt, **({"level": lvl} if lvl else {})}).text
        for fmt in activity_log.EXPORT_FORMATS
        for lvl in (None, *activity_log.LEVELS)
    ]
    assert any("config.push" in e for e in exports)
    for needle in needles:
        assert needle.encode() not in raw, needle
        assert needle not in dump, needle
        for text in exports:
            assert needle not in text, needle
