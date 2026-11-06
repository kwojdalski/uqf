"""The config push history, run against a local tree with no ssh (#2002).

Every rule remote_helper.py's PUSH HISTORY states is exercised on a real
directory: a push's before and after copies and its manifest, a create
recorded with no previous hash, a partial failure and an interrupted upload
recorded as what they were, the diff (text, binary, oversized), restore and
its round trip, a restore refused on a conflict or after pruning, retention
by count and by age, unusual names, and the history's confinement - no link
followed, and no browse, delete or upload path reaching it.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
from pathlib import Path

import pytest

from uqf_frontend import remote_files, remote_helper
from uqf_frontend.models import PrunedPushOut
from uqf_frontend.remote_helper import HISTORY_DIR, MANIFEST, handle

UNUSUAL = [
    "with space.csv",
    "it's quoted",
    'double "quoted"',
    "new\nline",
    "-leading-dash",
    "zażółć gęślą jaźń.toml",
    "$(touch pwned)",
    "semi;colon",
    "back\\slash",
    "tab\there",
    ".hidden",
]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "srv" / "uqf"
    (root / "shared" / "config").mkdir(parents=True)
    (tmp_path / "outside").mkdir()
    return root


def _ask(root: Path, op: str, **fields) -> dict:
    return handle({"protocol": 1, "op": op, "root": str(root), **fields})


def _open(root: Path, push_id: str | None = None, **fields) -> str:
    push_id = push_id or remote_files.new_push_id()
    answer = _ask(root, "push_open", push_id=push_id, config_dir="shared/config", **fields)
    assert answer["ok"], answer
    return push_id


def _put(root: Path, push_id: str, path: str, data: bytes, overwrite: bool = False, **over):
    request = {"protocol": 1, "op": "upload", "root": str(root), "path": path,
               "size": len(data), "sha256": _sha(data), "overwrite": overwrite,
               "push_id": push_id, **over}  # fmt: skip
    return handle(request, io.BytesIO(data))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _history(root: Path) -> Path:
    return root / "shared" / "config" / HISTORY_DIR


def _manifest(root: Path, push_id: str) -> dict:
    return json.loads((_history(root) / push_id / MANIFEST).read_text())


def _restore(root: Path, source: str, **over) -> dict:
    fields = {"restores": source, "push_id": remote_files.new_push_id(), "confirmed": True}
    return _ask(root, "restore", **{**fields, **over})


# ------------------------------------------------------------ what a push keeps


def test_a_push_opens_its_history_closed_to_other_users(root):
    push_id = _open(root, user="kw", target="box", via="frontend", file_count=2, total_bytes=9)
    assert stat.S_IMODE(_history(root).stat().st_mode) == 0o700
    assert stat.S_IMODE((_history(root) / push_id).stat().st_mode) == 0o700
    manifest = _manifest(root, push_id)
    assert manifest == {
        "version": 1, "push_id": push_id, "kind": "push", "restores": None,
        "created": manifest["created"], "closed": None, "config_dir": "shared/config",
        "declared": {"file_count": 2, "total_bytes": 9}, "user": "kw", "target": "box",
        "via": "frontend", "reported": [], "files": [],
    }  # fmt: skip


def test_a_manifest_an_older_release_wrote_is_read_and_written_back_as_config_dir(root):
    """A manifest stays on the server across releases: one written before
    #2107 says `destination`. It is still read, as `config_dir`, and the next
    write of it says only `config_dir`."""
    push_id = _open(root)
    path = _history(root) / push_id / MANIFEST
    old = json.loads(path.read_text())
    old["destination"] = old.pop("config_dir")
    path.write_text(json.dumps(old))
    (summary,) = _ask(root, "history_list")["pushes"]
    assert summary["config_dir"] == "shared/config" and "destination" not in summary
    assert _ask(root, "history_show", push_id=push_id)["manifest"]["config_dir"] == "shared/config"
    assert _ask(root, "push_close", push_id=push_id)["ok"]
    written = _manifest(root, push_id)
    assert written["config_dir"] == "shared/config" and "destination" not in written


def test_an_overwrite_keeps_the_previous_content_and_a_create_has_no_previous(root):
    old = root / "shared/config/sources.csv"
    old.write_bytes(b"old,secret\n")
    old.chmod(0o640)
    push_id = _open(root)
    replaced = _put(root, push_id, "shared/config/sources.csv", b"new\n", overwrite=True)
    created = _put(root, push_id, "shared/config/sub/new.csv", b"fresh\n")
    assert replaced["ok"] and created["ok"]
    assert replaced["previous_sha256"] == _sha(b"old,secret\n")
    assert replaced["previous_size"] == len(b"old,secret\n")
    assert created["previous_sha256"] is None and created["previous_size"] is None
    kept = _history(root) / push_id
    assert (kept / "before/sources.csv").read_bytes() == b"old,secret\n"
    assert stat.S_IMODE((kept / "before/sources.csv").stat().st_mode) == 0o640
    assert (kept / "after/sources.csv").read_bytes() == b"new\n"
    assert (kept / "after/sub/new.csv").read_bytes() == b"fresh\n"
    assert not (kept / "before/sub").exists()
    first, second = _manifest(root, push_id)["files"]
    assert first == {
        "path": "shared/config/sources.csv", "action": "replace", "size": 4,
        "sha256": _sha(b"new\n"), "mode": 0o640, "previous_size": 11,
        "previous_sha256": _sha(b"old,secret\n"), "previous_mode": 0o640,
        "created_dirs": [], "state": "published", "error": None, "at": first["at"],
    }  # fmt: skip
    assert second["action"] == "create" and second["previous_sha256"] is None
    assert second["created_dirs"] == ["shared/config/sub"]


def test_a_file_is_written_once_per_push(root):
    push_id = _open(root)
    assert _put(root, push_id, "shared/config/a", b"1")["ok"]
    again = _put(root, push_id, "shared/config/a", b"2", overwrite=True)
    assert again["error"] == "exists" and push_id in again["detail"]
    assert (root / "shared/config/a").read_bytes() == b"1"


def test_an_upload_names_an_open_push(root):
    answer = _put(root, remote_files.new_push_id(), "shared/config/a", b"1")
    assert answer["error"] == "not_found"
    assert not (root / "shared/config/a").exists()
    no_id = _put(root, "nope", "shared/config/a", b"1")
    assert no_id["error"] == "bad_request"
    push_id = _open(root)
    assert _ask(root, "push_close", push_id=push_id)["ok"]
    closed = _put(root, push_id, "shared/config/a", b"1")
    assert closed["error"] == "refused" and "closed" in closed["detail"]


def test_a_partial_failure_records_each_file_as_what_happened(root):
    """The second of three files is refused (it exists): the first and third
    are published, the second recorded failed with no bytes kept."""
    (root / "shared/config/b").write_bytes(b"there")
    push_id = _open(root)
    assert _put(root, push_id, "shared/config/a", b"1")["ok"]
    assert _put(root, push_id, "shared/config/b", b"2")["error"] == "exists"
    damaged = _put(root, push_id, "shared/config/c", b"3", sha256=_sha(b"other"))
    assert damaged["error"] == "incomplete"
    assert _put(root, push_id, "shared/config/d", b"4")["ok"]
    files = {e["path"].rsplit("/", 1)[1]: e for e in _manifest(root, push_id)["files"]}
    assert {k: (e["state"], e["error"]) for k, e in files.items()} == {
        "a": ("published", None),
        "b": ("failed", "exists"),
        "c": ("failed", "incomplete"),
        "d": ("published", None),
    }
    kept = _history(root) / push_id
    assert sorted(p.name for p in (kept / "after").iterdir()) == ["a", "d"]
    assert not (kept / "before").exists()
    assert (root / "shared/config/b").read_bytes() == b"there"
    shown = _ask(root, "history_show", push_id=push_id)
    assert shown["summary"]["states"] == {"published": 2, "pending": 0, "failed": 2}
    assert [e["now"] for e in shown["manifest"]["files"]] == ["after", None, None, "after"]


def test_a_failed_file_may_be_sent_again_in_the_same_push(root):
    push_id = _open(root)
    assert _put(root, push_id, "shared/config/a", b"1", sha256=_sha(b"x"))["error"] == "incomplete"
    assert _put(root, push_id, "shared/config/a", b"1")["ok"]
    (entry,) = _manifest(root, push_id)["files"]
    assert entry["state"] == "published"


def test_an_interrupted_upload_is_pending_and_show_tells_what_landed(root, monkeypatch):
    """The helper dies between publishing and recording it (landed), and
    between recording the copies and publishing (did not land): both stay
    `pending`, and show hashes the files to say which is which."""
    (root / "shared/config/kept").write_bytes(b"before")
    push_id = _open(root)
    real_write = remote_helper._write_manifest
    calls = []

    def dies_on(n):
        def write(pfd, manifest):
            calls.append(1)
            if len(calls) == n:
                raise OSError(5, "killed")
            real_write(pfd, manifest)

        return write

    # 1st write records pending; the 2nd, after publishing, never happens
    monkeypatch.setattr(remote_helper, "_write_manifest", dies_on(2))
    with pytest.raises(OSError, match="killed"):
        _put(root, push_id, "shared/config/landed", b"new")
    calls.clear()
    monkeypatch.setattr(remote_helper, "_write_manifest", real_write)

    real_replace = os.replace

    def no_publish(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
        if dst == "kept":
            raise OSError(5, "killed")
        real_replace(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

    monkeypatch.setattr(remote_helper.os, "replace", no_publish)
    with pytest.raises(OSError, match="killed"):
        _put(root, push_id, "shared/config/kept", b"after", overwrite=True)
    monkeypatch.setattr(remote_helper.os, "replace", real_replace)

    shown = _ask(root, "history_show", push_id=push_id)
    now = {e["path"]: (e["state"], e["now"]) for e in shown["manifest"]["files"]}
    assert now == {
        "shared/config/landed": ("pending", "after"),
        "shared/config/kept": ("pending", "before"),
    }
    assert (root / "shared/config/kept").read_bytes() == b"before"
    # the restore undoes what landed, and leaves what did not alone
    plan = _ask(root, "restore", restores=push_id, dry_run=True)["plan"]
    assert {p["path"]: p["step"] for p in plan} == {
        "shared/config/landed": "delete",
        "shared/config/kept": "unchanged",
    }


def test_the_file_being_replaced_must_fit_the_history(root, monkeypatch):
    monkeypatch.setattr(remote_helper, "MAX_UPLOAD", 4)
    (root / "shared/config/big").write_bytes(b"12345")
    push_id = _open(root)
    answer = _put(root, push_id, "shared/config/big", b"1", overwrite=True)
    assert answer["error"] == "too_large" and "not replaced" in answer["detail"]
    assert (root / "shared/config/big").read_bytes() == b"12345"


def test_close_records_what_the_browser_saw_of_files_never_recorded(root):
    push_id = _open(root)
    assert _put(root, push_id, "shared/config/a", b"1")["ok"]
    reported = [{"path": "shared/config/a", "outcome": "unknown"},
                {"path": "shared/config/b", "outcome": "cancelled"}]  # fmt: skip
    manifest = _ask(root, "push_close", push_id=push_id, reported=reported)["manifest"]
    # the server's own record wins for a file it has
    assert manifest["reported"] == [{"path": "shared/config/b", "outcome": "cancelled"}]
    assert manifest["closed"]
    assert _ask(root, "push_close", push_id=push_id)["error"] == "exists"
    bad = _ask(root, "push_close", push_id=_open(root), reported=[{"path": "x", "outcome": "ok"}])
    assert bad["error"] == "bad_request"


# ------------------------------------------------------------ diff


def test_the_diff_is_text_under_the_cap_and_hashes_otherwise(root, monkeypatch):
    monkeypatch.setattr(remote_helper, "DIFF_CAP", 64)
    config = root / "shared/config"
    (config / "text.csv").write_text("a,b\n1,2\n")
    (config / "blob.bin").write_bytes(b"\x00\x01")
    (config / "big.txt").write_text("x" * 10)
    push_id = _open(root)
    _put(root, push_id, "shared/config/text.csv", b"a,b\n1,3\n", overwrite=True)
    _put(root, push_id, "shared/config/blob.bin", b"\x00\x02", overwrite=True)
    _put(root, push_id, "shared/config/big.txt", b"y" * 100, overwrite=True)
    _put(root, push_id, "shared/config/new.txt", b"no newline")
    files = {
        f["path"].rsplit("/", 1)[1]: f for f in _ask(root, "history_diff", push_id=push_id)["files"]
    }
    assert files["text.csv"]["kind"] == "text"
    assert files["text.csv"]["diff"] == (
        "--- a/shared/config/text.csv\n+++ b/shared/config/text.csv\n"
        "@@ -1,2 +1,2 @@\n a,b\n-1,2\n+1,3\n"
    )
    assert files["new.txt"]["diff"].endswith("+no newline\n\\ No newline at end of file\n")
    for name, kind in (("blob.bin", "binary"), ("big.txt", "oversized")):
        assert files[name]["kind"] == kind and files[name]["diff"] is None
        assert files[name]["previous_sha256"] and files[name]["sha256"]
    assert files["big.txt"]["size"] == 100


def test_a_diff_of_one_path_and_of_a_path_the_push_never_wrote(root):
    push_id = _open(root)
    _put(root, push_id, "shared/config/a", b"1\n")
    _put(root, push_id, "shared/config/b", b"2\n")
    one = _ask(root, "history_diff", push_id=push_id, path="shared/config/b")
    assert [f["path"] for f in one["files"]] == ["shared/config/b"]
    missing = _ask(root, "history_diff", push_id=push_id, path="shared/config/zz")
    assert missing["error"] == "not_found"


def test_a_diff_past_the_answer_cap_is_omitted_not_cut(root, monkeypatch):
    """One diff is 121 characters: the first fits a 150 cap, the second is
    omitted whole rather than cut short."""
    monkeypatch.setattr(remote_helper, "DIFF_ANSWER_CAP", 150)
    push_id = _open(root)
    _put(root, push_id, "shared/config/a", b"1\n" * 20)
    _put(root, push_id, "shared/config/b", b"2\n" * 20)
    files = _ask(root, "history_diff", push_id=push_id)["files"]
    assert len(files[0]["diff"]) == 121
    assert [f["kind"] for f in files] == ["text", "omitted"]
    assert files[1]["diff"] is None and files[1]["sha256"] == _sha(b"2\n" * 20)


# ------------------------------------------------------------ restore


def test_restore_round_trip_puts_every_file_back_and_deletes_what_it_created(root):
    config = root / "shared/config"
    (config / "a.csv").write_bytes(b"A0")
    (config / "a.csv").chmod(0o600)
    push_id = _open(root)
    _put(root, push_id, "shared/config/a.csv", b"A1", overwrite=True)
    _put(root, push_id, "shared/config/deep/er/b.csv", b"B1")
    _ask(root, "push_close", push_id=push_id)
    assert (config / "a.csv").read_bytes() == b"A1"

    undone = _restore(root, push_id)
    assert undone["ok"], undone
    assert [(r["step"], r["outcome"]) for r in undone["results"]] == [
        ("write", "succeeded"), ("delete", "succeeded"),
    ]  # fmt: skip
    assert (config / "a.csv").read_bytes() == b"A0"
    assert stat.S_IMODE((config / "a.csv").stat().st_mode) == 0o600
    assert not (config / "deep").exists()  # the directories it created, empty, removed
    manifest = _manifest(root, undone["push_id"])
    assert manifest["kind"] == "restore" and manifest["restores"] == push_id
    assert manifest["closed"]
    assert [(e["action"], e["state"]) for e in manifest["files"]] == [
        ("replace", "published"), ("delete", "published"),
    ]  # fmt: skip

    # a restore is a push: restoring it puts the pushed files back
    redone = _restore(root, undone["push_id"])
    assert redone["ok"], redone
    assert (config / "a.csv").read_bytes() == b"A1"
    assert (config / "deep/er/b.csv").read_bytes() == b"B1"


def test_a_restore_needs_confirming_and_a_dry_run_writes_nothing(root):
    push_id = _open(root)
    _put(root, push_id, "shared/config/a", b"1")
    unconfirmed = _restore(root, push_id, confirmed=False)
    assert unconfirmed["error"] == "refused" and "confirmation" in unconfirmed["detail"]
    before = sorted(p for p in root.rglob("*"))
    plan = _ask(root, "restore", restores=push_id, dry_run=True)
    assert plan["dry_run"] and plan["push_id"] is None and plan["results"] == []
    assert [p["step"] for p in plan["plan"]] == ["delete"]
    assert sorted(p for p in root.rglob("*")) == before


def test_a_file_changed_since_the_push_refuses_the_whole_restore(root):
    config = root / "shared/config"
    (config / "a").write_bytes(b"0")
    push_id = _open(root)
    _put(root, push_id, "shared/config/a", b"1", overwrite=True)
    _put(root, push_id, "shared/config/b", b"1")
    (config / "b").write_bytes(b"edited later")
    pushes_before = sorted(p.name for p in _history(root).iterdir())
    refused = _restore(root, push_id)
    assert refused["error"] == "conflict"
    assert '"shared/config/b"' in refused["detail"] and "nothing was written" in refused["detail"]
    assert (config / "a").read_bytes() == b"1" and (config / "b").read_bytes() == b"edited later"
    assert sorted(p.name for p in _history(root).iterdir()) == pushes_before
    plan = _ask(root, "restore", restores=push_id, dry_run=True)["plan"]
    assert [p["step"] for p in plan] == ["write", "conflict"]


def test_a_push_landing_between_a_restores_plan_and_its_write_refuses_it(root, monkeypatch):
    # #2181: restore's conflict check ran once, unlocked, then it wrote with
    # overwrite - so a push landing in between was silently overwritten.
    config = root / "shared/config"
    (config / "app.toml").write_bytes(b"v1\n")
    pushed = _open(root)
    assert _put(root, pushed, "shared/config/app.toml", b"v2\n", overwrite=True)["ok"]
    pushes_before = sorted(p.name for p in _history(root).iterdir())
    concurrent = remote_files.new_push_id()
    planned = remote_helper._plan
    calls = []

    def plan_then_a_concurrent_push(root_, manifest):
        plan = planned(root_, manifest)
        calls.append([p["step"] for p in plan])
        if len(calls) == 1:  # after restore's first look, before it writes
            _open(root, concurrent)
            assert _put(root, concurrent, "shared/config/app.toml", b"v3\n", overwrite=True)["ok"]
        return plan

    monkeypatch.setattr(remote_helper, "_plan", plan_then_a_concurrent_push)
    refused = _restore(root, pushed)
    assert refused["error"] == "conflict", refused
    assert '"shared/config/app.toml"' in refused["detail"]
    assert "nothing was written" in refused["detail"]
    assert calls == [["write"], ["conflict"]]  # looked again before writing
    assert (config / "app.toml").read_bytes() == b"v3\n"
    # the refused restore leaves no push of its own, as a refusal at the plan does
    assert sorted(p.name for p in _history(root).iterdir()) == sorted([*pushes_before, concurrent])


def test_an_uploads_publishing_step_waits_for_the_write_lock(root):
    # A restore holds WRITE_LOCK across its check and its writes; an upload
    # must not publish while it is held, or the check proves nothing (#2181).
    import fcntl
    import threading

    (root / "shared/config/a").write_bytes(b"0")
    push_id = _open(root)
    lock = os.open(_history(root) / ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    answers = []
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        uploading = threading.Thread(
            target=lambda: answers.append(
                _put(root, push_id, "shared/config/a", b"1", overwrite=True)
            )
        )
        uploading.start()
        uploading.join(timeout=0.5)
        assert uploading.is_alive() and not answers
        assert (root / "shared/config/a").read_bytes() == b"0"
    finally:
        os.close(lock)  # releases the lock
    uploading.join(timeout=10)
    assert answers and answers[0]["ok"], answers
    assert (root / "shared/config/a").read_bytes() == b"1"


def test_restoring_what_is_already_restored_is_refused(root):
    push_id = _open(root)
    _put(root, push_id, "shared/config/a", b"1")
    assert _restore(root, push_id)["ok"]
    again = _restore(root, push_id)
    assert again["error"] == "refused" and "nothing to restore" in again["detail"]


def test_a_tampered_before_copy_is_not_restored(root):
    (root / "shared/config/a").write_bytes(b"0")
    push_id = _open(root)
    _put(root, push_id, "shared/config/a", b"1", overwrite=True)
    (_history(root) / push_id / "before/a").write_bytes(b"evil")
    answer = _restore(root, push_id)
    assert answer["ok"] and answer["results"][0]["outcome"] == "failed"
    assert (root / "shared/config/a").read_bytes() == b"1"


# ------------------------------------------------------------ retention


def test_retention_keeps_the_newest_pushes_and_logs_the_pruned(root):
    ids = [f"20261104T10000{i}000Z-0000000{i}" for i in range(4)]
    for push_id in ids[:3]:
        _open(root, push_id)
    answer = _ask(root, "push_open", push_id=ids[3], config_dir="shared/config", max_kept=2)
    assert [p["push_id"] for p in answer["pruned"]] == [ids[1], ids[0]]
    assert {p["why"] for p in answer["pruned"]} == {"row_cap"}
    assert sorted(p.name for p in _history(root).iterdir() if p.is_dir()) == ids[2:]
    logged = [json.loads(line)["push_id"] for line in (_history(root) / "pruned.jsonl").open()]
    assert logged == [ids[1], ids[0]]
    listed = _ask(root, "history_list")
    assert [p["push_id"] for p in listed["pushes"]] == [ids[3], ids[2]]
    assert [p["push_id"] for p in listed["pruned"]] == [ids[0], ids[1]]


def test_retention_by_age(root):
    old = "20200101T000000000Z-00000001"
    _open(root, old)
    answer = _ask(root, "push_open", push_id=remote_files.new_push_id(),
                  config_dir="shared/config", retention_days=30)  # fmt: skip
    assert answer["pruned"] == [
        {"push_id": old, "why": "retention", "pruned_at": answer["pruned"][0]["pruned_at"]}
    ]


def test_a_restore_after_pruning_is_refused_clearly(root):
    gone = _open(root, "20261104T100000000Z-00000001")
    _put(root, gone, "shared/config/a", b"1")
    _ask(root, "push_open", push_id="20261104T100001000Z-00000002", config_dir="shared/config",
         max_kept=1)  # fmt: skip
    for op, extra in (("restore", {"dry_run": True}), ("history_show", {}), ("history_diff", {})):
        key = "restores" if op == "restore" else "push_id"
        answer = _ask(root, op, **{key: gone}, **extra)
        assert answer["error"] == "pruned", answer
        assert "pruned" in answer["detail"] and "row_cap bound" in answer["detail"]
    never = _ask(root, "history_show", push_id="20261104T100009000Z-00000009")
    assert never["error"] == "not_found"


def test_a_bound_outside_its_limits_is_refused(root):
    for over in ({"max_kept": 0}, {"retention_days": 0}, {"max_kept": remote_helper.MAX_KEEP + 1}):
        answer = _ask(root, "push_open", push_id=remote_files.new_push_id(),
                      config_dir="shared/config", **over)  # fmt: skip
        assert answer["error"] == "bad_request", over


# ------------------------------------------------------------ unusual names


@pytest.mark.parametrize("name", UNUSUAL)
def test_unusual_names_are_kept_diffed_and_restored_as_themselves(root, name):
    target = root / "shared/config" / name
    target.mkdir()
    (target / name).write_bytes(b"old\n")
    push_id = _open(root)
    path = f"shared/config/{name}/{name}"
    assert _put(root, push_id, path, b"new\n", overwrite=True)["ok"]
    assert (_history(root) / push_id / "before" / name / name).read_bytes() == b"old\n"
    (diff,) = _ask(root, "history_diff", push_id=push_id)["files"]
    assert diff["path"] == path and "-old\n+new\n" in diff["diff"]
    assert _restore(root, push_id)["ok"]
    assert (target / name).read_bytes() == b"old\n"
    assert not list(root.parent.rglob("pwned"))


# ------------------------------------------------------------ confinement


@pytest.mark.parametrize(
    "path",
    [
        "shared/config/.uqf-history",
        "shared/config/.uqf-history/x",
        "shared/config/./.uqf-history",
        "shared//config/.uqf-history/x",
        "shared/config/.UQF-History/x",
        "SHARED/Config/.uqf-history",
    ],
)
def test_no_browse_delete_or_upload_reaches_the_history(root, path):
    push_id = _open(root)
    _put(root, push_id, "shared/config/a", b"1")
    listed = _ask(root, "list", path=path)
    assert listed["error"] == "refused" and "config push history" in listed["detail"]
    deleted = _ask(root, "delete", paths=[path, f"{path}/{push_id}/{MANIFEST}"], confirmed=True)
    assert all(r["outcome"] == "failed" for r in deleted["results"])
    uploaded = _put(root, push_id, f"{path}/y", b"evil")
    assert uploaded["ok"] is False
    assert (_history(root) / push_id / MANIFEST).exists()


def test_the_history_is_listed_blocked(root):
    _open(root)
    entries = {e["name"]: e for e in _ask(root, "list", path="shared/config")["entries"]}
    assert entries[HISTORY_DIR]["blocked"] is True
    assert "History" in entries[HISTORY_DIR]["reason"]


def test_a_history_dir_that_is_a_link_is_refused_not_followed(root, tmp_path):
    outside = tmp_path / "outside"
    _history(root).symlink_to(outside)
    answer = _ask(root, "push_open", push_id=remote_files.new_push_id(),
                  config_dir="shared/config")  # fmt: skip
    assert answer["error"] == "refused" and "symbolic link" in answer["detail"]
    assert list(outside.iterdir()) == []
    assert _ask(root, "history_list")["error"] == "refused"


def test_a_link_inside_a_push_is_never_followed(root, tmp_path):
    outside = tmp_path / "outside"
    (root / "shared/config/a").write_bytes(b"secret")
    push_id = _open(root)
    (_history(root) / push_id / "before").symlink_to(outside)
    answer = _put(root, push_id, "shared/config/a", b"1", overwrite=True)
    assert answer["ok"] is False and "symbolic link" in answer["detail"]
    assert list(outside.iterdir()) == []
    assert (root / "shared/config/a").read_bytes() == b"secret"


def test_a_before_copy_swapped_for_a_link_is_not_restored(root, tmp_path):
    victim = tmp_path / "outside" / "victim"
    victim.write_bytes(b"outside")
    (root / "shared/config/a").write_bytes(b"0")
    push_id = _open(root)
    _put(root, push_id, "shared/config/a", b"1", overwrite=True)
    copy = _history(root) / push_id / "before/a"
    copy.unlink()
    copy.symlink_to(victim)
    answer = _restore(root, push_id)
    assert answer["results"][0]["outcome"] == "failed"
    assert (root / "shared/config/a").read_bytes() == b"1"


def test_a_restore_deletes_only_a_regular_file(root):
    push_id = _open(root)
    _put(root, push_id, "shared/config/a", b"1")
    (root / "shared/config/a").unlink()
    (root / "shared/config/a").mkdir()
    plan = _ask(root, "restore", restores=push_id, dry_run=True)["plan"]
    assert plan[0]["step"] == "conflict" and "not a regular file" in plan[0]["error"]
    assert _restore(root, push_id)["error"] == "conflict"
    assert (root / "shared/config/a").is_dir()


def test_a_push_goes_to_shared_config_and_nowhere_else(root):
    for destination in ("shared/data", "releases", ""):
        answer = _ask(root, "push_open", push_id=remote_files.new_push_id(),
                      config_dir=destination)  # fmt: skip
        assert answer["error"] == "unsupported", destination
    assert not _history(root).exists()


def test_a_restore_removes_no_directory_outside_shared_config(root):
    """A manifest's created_dirs is read as paths strictly below shared/config:
    an edited one naming an empty directory elsewhere removes nothing."""
    (root / "releases" / "x").mkdir(parents=True)
    push_id = _open(root)
    _put(root, push_id, "shared/config/only", b"1")
    manifest_file = _history(root) / push_id / MANIFEST
    manifest = json.loads(manifest_file.read_text())
    manifest["files"][0]["created_dirs"] = ["releases/x", "shared/config/../../releases/x"]
    manifest_file.write_text(json.dumps(manifest))
    answer = _restore(root, push_id)
    assert answer["ok"] and answer["results"][0]["outcome"] == "succeeded"
    assert not (root / "shared/config/only").exists()
    assert (root / "releases" / "x").is_dir()


def test_a_pruned_log_written_before_2111_is_read_in_todays_words(root):
    """A server's PRUNED_LOG outlives the release that wrote it: a line in the
    pre-#2111 reasons (`count`, `age`) is listed in today's (`row_cap`,
    `retention`), so the API's strict response model still reads it."""
    gone = _open(root, "20261104T100000000Z-00000001")
    _ask(root, "push_open", push_id="20261104T100001000Z-00000002", config_dir="shared/config",
         max_kept=1)  # fmt: skip
    log = _history(root) / "pruned.jsonl"
    old = [{"push_id": gone, "why": "count", "pruned_at": "2026-11-04T10:00:00Z"},
           {"push_id": "20200101T000000000Z-00000009", "why": "age",
            "pruned_at": "2026-11-04T10:00:00Z"}]  # fmt: skip
    log.write_text("".join(json.dumps(r) + "\n" for r in old), encoding="utf-8")
    listed = _ask(root, "history_list")
    assert sorted(p["why"] for p in listed["pruned"]) == ["retention", "row_cap"]
    for record in listed["pruned"]:
        PrunedPushOut.model_validate(record)
