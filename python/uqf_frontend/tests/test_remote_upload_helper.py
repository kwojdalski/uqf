"""The helper's upload op, run against a local tree with no ssh (#1951).

Every rule remote_helper.py states for `upload` is exercised on a real
directory: the destination confined to shared/config, unusual names,
traversal, links on the way and at the name, overwrite refused and opted
into, permissions kept, publishing in one step (no partial file is ever
visible), and the temp file removed on every failure.
"""

from __future__ import annotations

import errno
import hashlib
import io
import json
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from uqf_frontend import remote_files, remote_helper
from uqf_frontend.remote_helper import TEMP_PREFIX, Refusal, handle, upload_parts

UNUSUAL = [
    "with space.csv",
    "it's quoted",
    'double "quoted"',
    "new\nline",
    "-leading-dash",
    "--help",
    "zażółć gęślą jaźń.toml",
    "日本語.txt",
    "$(touch pwned)",
    "`id`",
    "semi;colon",
    "back\\slash",
    "*",
    "tab\there",
    ".hidden",
]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "srv" / "uqf"
    (root / "shared" / "config").mkdir(parents=True)
    (root / "releases" / "r1").mkdir(parents=True)
    (tmp_path / "outside").mkdir()
    return root


def _open_push(root: Path) -> str:
    """A push for one upload (#2002): every upload belongs to one."""
    push_id = remote_files.new_push_id()
    handle({"protocol": 1, "op": "push_open", "root": str(root), "push_id": push_id,
            "destination": "shared/config"})  # fmt: skip
    return push_id  # a refused open leaves no push, and the upload says why


def _listing(directory: Path) -> list[str]:
    """What a directory holds, but the push history an upload's push keeps."""
    return sorted(n for n in os.listdir(directory) if n != remote_helper.HISTORY_DIR)


def _request(root: Path, path: str, data: bytes, overwrite: bool = False, **over) -> dict:
    return {
        "protocol": 1,
        "op": "upload",
        "root": str(root),
        "path": path,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "overwrite": overwrite,
        "push_id": over.pop("push_id", None) or _open_push(root),
        **over,
    }


def _put(root: Path, path: str, data: bytes, overwrite: bool = False, stream=None, **over):
    stream = io.BytesIO(data) if stream is None else stream
    return handle(_request(root, path, data, overwrite, **over), stream)


def _temps(directory: Path) -> list[str]:
    return [p.name for p in directory.iterdir() if p.name.startswith(TEMP_PREFIX)]


# ------------------------------------------------------------ where it may write


def test_a_file_lands_below_shared_config_with_nested_dirs_created(root):
    answer = _put(root, "shared/config/scripts/torqconfig/sources.csv", b"a,b\n1,2\n")
    assert answer["ok"] is True, answer
    written = root / "shared/config/scripts/torqconfig/sources.csv"
    assert written.read_bytes() == b"a,b\n1,2\n"
    assert answer["path"] == "shared/config/scripts/torqconfig/sources.csv"
    assert answer["size"] == 8
    assert answer["sha256"] == hashlib.sha256(b"a,b\n1,2\n").hexdigest()
    assert answer["replaced"] is False
    assert answer["created"] == ["shared/config/scripts", "shared/config/scripts/torqconfig"]
    assert _temps(written.parent) == []


def test_shared_config_itself_is_created_when_missing(tmp_path):
    root = tmp_path / "bare"
    root.mkdir()
    answer = _put(root, "shared/config/x.csv", b"x")
    assert answer["ok"], answer
    # the push the upload belongs to made them, to keep its history there
    assert answer["created"] == []
    assert (root / "shared/config/x.csv").read_bytes() == b"x"


@pytest.mark.parametrize(
    "path",
    [
        "shared/config",  # the directory itself is not a file
        "shared/config/",
        "shared/data/x",
        "releases/r1/config/settings.toml",
        "current/x",
        "x.csv",
        "",
        "shared",
        "sharedconfig/x",
    ],
)
def test_a_destination_outside_shared_config_is_unsupported(root, path):
    answer = _put(root, path, b"x")
    assert answer["error"] == "unsupported", answer
    assert "shared/config/" in answer["detail"]
    assert not (root / "releases/r1/config").exists()


@pytest.mark.parametrize(
    ("path", "error"),
    [
        ("/etc/passwd", "refused"),
        ("/srv/uqf/shared/config/x", "refused"),
        ("shared/config/../../../etc/x", "refused"),
        ("shared/config/a/../../x", "refused"),
        ("../shared/config/x", "refused"),
        ("shared/config/a\x00b", "refused"),
        (f"shared/config/{TEMP_PREFIX}abc.tmp", "refused"),
        ("shared/config/" + "n" * 256, "refused"),
        ("shared/config/" + "d/" * 17 + "x", "refused"),
    ],
)
def test_traversal_and_malformed_paths_write_nothing(root, tmp_path, path, error):
    request = _request(root, path, b"x")
    before = sorted(p for p in tmp_path.rglob("*"))
    answer = handle(request, io.BytesIO(b"x"))
    assert answer["ok"] is False and answer["error"] == error, answer
    assert sorted(p for p in tmp_path.rglob("*")) == before


def test_upload_parts_is_what_the_api_checks_before_ssh():
    assert upload_parts("shared/config/./a//b.csv") == ["shared", "config", "a", "b.csv"]
    assert upload_parts("shared/config/" + "d/" * 16 + "x")[-1] == "x"
    assert upload_parts("shared/config/" + "é" * 127)  # 254 bytes
    with pytest.raises(Refusal) as caught:
        upload_parts("shared/config/" + "é" * 128)  # 256 bytes
    assert caught.value.error == "refused"


@pytest.mark.parametrize("name", UNUSUAL)
def test_an_unusual_name_is_written_as_itself(root, name):
    answer = _put(root, f"shared/config/{name}/{name}", name.encode())
    assert answer["ok"], answer
    assert (root / "shared/config" / name / name).read_bytes() == name.encode()
    assert not list(root.rglob("pwned"))


# ------------------------------------------------------------ links


def test_a_link_on_the_way_is_refused_not_followed(root, tmp_path):
    (root / "shared/config/out").symlink_to(tmp_path / "outside")
    answer = _put(root, "shared/config/out/x.csv", b"x")
    assert answer["error"] == "refused" and "symbolic link" in answer["detail"]
    assert list((tmp_path / "outside").iterdir()) == []


def test_shared_config_as_a_link_is_refused(tmp_path):
    root = tmp_path / "srv"
    (root / "shared").mkdir(parents=True)
    (root / "shared" / "config").symlink_to(tmp_path)
    answer = _put(root, "shared/config/x.csv", b"x")
    assert answer["error"] == "refused"
    assert not (tmp_path / "x.csv").exists()


def test_a_link_at_the_name_is_never_replaced_even_with_overwrite(root, tmp_path):
    target = tmp_path / "outside" / "victim"
    target.write_text("keep")
    (root / "shared/config/x.csv").symlink_to(target)
    for overwrite in (False, True):
        answer = _put(root, "shared/config/x.csv", b"evil", overwrite=overwrite)
        assert answer["error"] == "refused", answer
        assert "symbolic link" in answer["detail"]
    assert target.read_text() == "keep"
    assert (root / "shared/config/x.csv").is_symlink()
    assert _temps(root / "shared/config") == []


def test_a_dangling_link_at_the_name_is_not_replaced(root, tmp_path):
    (root / "shared/config/x.csv").symlink_to(tmp_path / "outside" / "nothing")
    answer = _put(root, "shared/config/x.csv", b"x", overwrite=True)
    assert answer["error"] == "refused"
    assert not (tmp_path / "outside" / "nothing").exists()


def test_a_directory_swapped_for_a_link_while_creating_is_refused(root, tmp_path, monkeypatch):
    """mkdir succeeds, then a link replaces the new directory before it is
    opened: the confined open refuses it and nothing lands outside."""
    real_mkdir = os.mkdir

    def racing_mkdir(name, mode=0o777, *, dir_fd=None):
        real_mkdir(name, mode, dir_fd=dir_fd)
        os.rmdir(name, dir_fd=dir_fd)
        os.symlink(str(tmp_path / "outside"), name, dir_fd=dir_fd)

    push_id = _open_push(root)
    monkeypatch.setattr(remote_helper.os, "mkdir", racing_mkdir)
    answer = _put(root, "shared/config/new/x.csv", b"x", push_id=push_id)
    assert answer["ok"] is False and answer["error"] == "refused", answer
    assert list((tmp_path / "outside").iterdir()) == []


def test_a_directory_or_fifo_at_the_name_is_not_replaced(root):
    (root / "shared/config/d").mkdir()
    os.mkfifo(root / "shared/config/p")
    assert _put(root, "shared/config/d", b"x", overwrite=True)["error"] == "refused"
    assert _put(root, "shared/config/p", b"x", overwrite=True)["error"] == "refused"
    assert (root / "shared/config/d").is_dir()
    assert stat.S_ISFIFO(os.lstat(root / "shared/config/p").st_mode)


# ------------------------------------------------------------ overwrite


def test_an_existing_file_is_refused_without_overwrite(root):
    existing = root / "shared/config/x.csv"
    existing.write_text("old")
    answer = _put(root, "shared/config/x.csv", b"new")
    assert answer["error"] == "exists"
    assert "must be asked for" in answer["detail"]
    assert existing.read_text() == "old"
    assert _temps(existing.parent) == []


def test_overwrite_replaces_and_keeps_ordinary_permissions(root):
    existing = root / "shared/config/secrets.env"
    existing.write_text("A=1\n")
    os.chmod(existing, 0o600)
    answer = _put(root, "shared/config/secrets.env", b"A=2\n", overwrite=True)
    assert answer["ok"], answer
    assert answer["replaced"] is True
    assert existing.read_text() == "A=2\n"
    assert stat.S_IMODE(os.stat(existing).st_mode) == 0o600
    assert answer["mode"] == 0o600


def test_setuid_and_sticky_bits_are_not_carried_over(root):
    existing = root / "shared/config/run.sh"
    existing.write_text("x")
    os.chmod(existing, 0o4755)
    answer = _put(root, "shared/config/run.sh", b"y", overwrite=True)
    assert answer["ok"], answer
    assert stat.S_IMODE(os.stat(existing).st_mode) == 0o755


def test_a_new_file_gets_the_umask_default(root):
    old = os.umask(0o027)
    try:
        answer = _put(root, "shared/config/x.csv", b"x")
    finally:
        os.umask(old)
    assert stat.S_IMODE(os.stat(root / "shared/config/x.csv").st_mode) == 0o640
    assert answer["mode"] == 0o640


def test_overwrite_with_nothing_there_simply_writes(root):
    answer = _put(root, "shared/config/x.csv", b"x", overwrite=True)
    assert answer["ok"] and answer["replaced"] is False


def test_a_file_appearing_between_check_and_publish_is_not_overwritten(root, monkeypatch):
    """Without overwrite, publishing is link(2): it fails if the name exists
    by then - there is no check-then-write window."""
    dest = root / "shared/config/x.csv"

    class Racing(io.BytesIO):
        def read(self, n=-1):
            out = super().read(n)
            if not out and not dest.exists():
                dest.write_text("theirs")
            return out

    answer = _put(root, "shared/config/x.csv", b"mine", stream=Racing(b"mine"))
    assert answer["error"] == "exists"
    assert dest.read_text() == "theirs"
    assert _temps(dest.parent) == []


def test_a_filesystem_without_hard_links_falls_back_to_rename(root, monkeypatch):
    def no_links(*_a, **_k):
        raise OSError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(remote_helper.os, "link", no_links)
    answer = _put(root, "shared/config/x.csv", b"x")
    assert answer["ok"], answer
    assert (root / "shared/config/x.csv").read_bytes() == b"x"
    (root / "shared/config/y.csv").write_text("old")
    assert _put(root, "shared/config/y.csv", b"new")["error"] == "exists"
    assert (root / "shared/config/y.csv").read_text() == "old"
    assert _temps(root / "shared/config") == []


# ------------------------------------------------------------ atomicity and cleanup


class Watching(io.BytesIO):
    """A stream that, each time it is read, records what a reader of the
    destination directory would see."""

    def __init__(self, data: bytes, directory: Path, name: str) -> None:
        super().__init__(data)
        self.directory, self.name, self.seen = directory, name, []

    def read(self, n=-1):
        target = self.directory / self.name
        self.seen.append(target.read_bytes() if target.exists() else None)
        return super().read(min(n, 7) if n and n > 0 else n)


def test_no_partial_file_is_ever_visible_at_the_name(root, monkeypatch):
    monkeypatch.setattr(remote_helper, "CHUNK", 7)
    data = b"0123456789" * 10
    stream = Watching(data, root / "shared/config", "x.csv")
    answer = _put(root, "shared/config/x.csv", data, stream=stream)
    assert answer["ok"], answer
    assert len(stream.seen) > 10
    assert set(stream.seen) == {None}  # absent throughout, then whole
    assert (root / "shared/config/x.csv").read_bytes() == data


def test_a_replaced_file_reads_old_until_the_new_one_is_whole(root, monkeypatch):
    monkeypatch.setattr(remote_helper, "CHUNK", 7)
    (root / "shared/config/x.csv").write_bytes(b"old contents")
    data = b"new" * 40
    stream = Watching(data, root / "shared/config", "x.csv")
    answer = _put(root, "shared/config/x.csv", data, overwrite=True, stream=stream)
    assert answer["ok"], answer
    assert set(stream.seen) == {b"old contents"}
    assert (root / "shared/config/x.csv").read_bytes() == data


@pytest.mark.parametrize(
    ("stream", "said"),
    [
        (io.BytesIO(b"short"), "ended after 5 of 10 bytes"),
        (io.BytesIO(b"0123456789+extra"), "more than the 10 bytes"),
        (io.BytesIO(b"9876543210"), "do not match the sha256"),
    ],
)
def test_bytes_that_do_not_arrive_whole_publish_nothing_and_leave_no_temp(root, stream, said):
    (root / "shared/config/kept.csv").write_text("old")
    for path, overwrite in (("shared/config/new.csv", False), ("shared/config/kept.csv", True)):
        stream.seek(0)
        answer = handle(_request(root, path, b"0123456789", overwrite), stream)
        assert answer["error"] == "incomplete", answer
        assert said in answer["detail"]
    assert not (root / "shared/config/new.csv").exists()
    assert (root / "shared/config/kept.csv").read_text() == "old"
    assert _temps(root / "shared/config") == []


def test_a_failure_while_writing_removes_the_temp_file(root, monkeypatch):
    def disk_full(fd, data):
        raise OSError(errno.ENOSPC, "No space left on device")

    push_id = _open_push(root)
    monkeypatch.setattr(remote_helper.os, "write", disk_full)
    with pytest.raises(OSError, match="No space"):
        _put(root, "shared/config/x.csv", b"x" * 100, push_id=push_id)
    assert _listing(root / "shared/config") == []


def test_a_hangup_mid_upload_is_a_refusal_and_cleans_up(root):
    """ssh dropping or timing out sends the remote helper SIGHUP or SIGTERM:
    it becomes a refusal, so the temp file is removed, not orphaned."""

    class HangsUp(io.BytesIO):
        def read(self, n=-1):
            os.kill(os.getpid(), signal.SIGHUP)
            return super().read(n)

    before = signal.getsignal(signal.SIGHUP)
    answer = _put(root, "shared/config/x.csv", b"data", stream=HangsUp(b"data"))
    assert answer["error"] == "incomplete" and "signal" in answer["detail"]
    assert _listing(root / "shared/config") == []
    assert signal.getsignal(signal.SIGHUP) is before


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP])
@pytest.mark.parametrize("overwrite", [True, False])
def test_a_signal_just_after_publishing_never_says_nothing_was_published(
    root, monkeypatch, signum, overwrite
):
    """Once os.replace (overwrite) or os.link (create) has run, the file is
    live: a hangup or SIGTERM arriving then must not turn into "nothing was
    published", which the API reports as a certain failure (#2022). The
    signals are ignored across the publishing step and its fsync, as #2004's
    push path does."""
    target = root / "shared/config/a.toml"
    if overwrite:
        target.write_bytes(b"old contents\n")
    step = "replace" if overwrite else "link"
    real = getattr(os, step)

    def publish_then_signal(src, dst, *a, **kw):
        real(src, dst, *a, **kw)
        if dst == "a.toml":  # the publishing step, not a history manifest's write
            os.kill(os.getpid(), signum)

    monkeypatch.setattr(remote_helper.os, step, publish_then_signal)
    before = signal.getsignal(signum)
    answer = _put(root, "shared/config/a.toml", b"new contents\n", overwrite=overwrite)
    assert answer["ok"] is True, answer
    assert answer["replaced"] is overwrite
    assert target.read_bytes() == b"new contents\n"
    assert _temps(root / "shared/config") == []
    assert signal.getsignal(signum) is before


def test_stale_temp_files_are_swept_fresh_ones_kept(root):
    config = root / "shared/config"
    stale, fresh = config / f"{TEMP_PREFIX}old.tmp", config / f"{TEMP_PREFIX}new.tmp"
    stale.write_text("x")
    fresh.write_text("x")
    old = time.time() - remote_helper.STALE_TEMP - 10
    os.utime(stale, (old, old))
    (config / f"{TEMP_PREFIX}link.tmp").symlink_to("/etc/passwd")
    os.utime(config / f"{TEMP_PREFIX}link.tmp", (old, old), follow_symlinks=False)
    answer = _put(root, "shared/config/x.csv", b"x")
    assert answer["swept"] == 1
    assert not stale.exists() and fresh.exists()
    assert (config / f"{TEMP_PREFIX}link.tmp").is_symlink()


# ------------------------------------------------------------ the request


@pytest.mark.parametrize(
    "over",
    [
        {"size": -1},
        {"size": "10"},
        {"size": True},
        {"sha256": "ABC"},
        {"sha256": "0" * 63},
        {"overwrite": "yes"},
    ],
)
def test_a_malformed_upload_request_is_a_bad_request(root, over):
    answer = handle(_request(root, "shared/config/x", b"x", **over), io.BytesIO(b"x"))
    assert answer["error"] == "bad_request", answer
    assert _listing(root / "shared/config") == []


def test_a_file_over_the_limit_is_refused_before_reading(root):
    class Unread(io.BytesIO):
        def read(self, n=-1):
            raise AssertionError("read")

    req = _request(root, "shared/config/x", b"", size=remote_helper.MAX_UPLOAD + 1)
    assert handle(req, Unread())["error"] == "too_large"


def test_an_upload_without_a_stream_is_a_bad_request(root):
    answer = handle(_request(root, "shared/config/x", b"x"))
    assert answer["error"] == "bad_request"


def test_an_empty_file_is_an_ordinary_upload(root):
    assert _put(root, "shared/config/empty", b"")["ok"]
    assert (root / "shared/config/empty").read_bytes() == b""


# ------------------------------------------------------------ as ssh runs it


def test_the_bootstrap_framing_runs_the_helper_and_reads_binary_bytes(root):
    """Exactly what the remote command runs: `python -I -c BOOTSTRAP <req>`,
    stdin the source's length, the source, then the file - every byte value."""
    data = bytes(range(256)) * 300 + b"\r\n\x00tail"
    source = remote_files.helper_source().encode()
    done = subprocess.run(
        [sys.executable, "-I", "-c", remote_files.BOOTSTRAP,
         remote_helper.encode(_request(root, "shared/config/bin/blob", data))],
        input=b"%d\n" % len(source) + source + data,
        capture_output=True, check=True,
    )  # fmt: skip
    answer = json.loads(done.stdout)
    assert answer["ok"], answer
    assert (root / "shared/config/bin/blob").read_bytes() == data


def test_the_list_op_still_runs_from_the_bootstrap(root):
    source = remote_files.helper_source().encode()
    request = {"protocol": 1, "op": "list", "root": str(root), "path": "shared"}
    done = subprocess.run(
        [sys.executable, "-I", "-c", remote_files.BOOTSTRAP, remote_helper.encode(request)],
        input=b"%d\n" % len(source) + source, capture_output=True, check=True,
    )  # fmt: skip
    assert [e["name"] for e in json.loads(done.stdout)["entries"]] == ["config"]


def test_a_killed_helper_leaves_nothing_at_the_name(root):
    """The process dies mid-stream (ssh's TERM after a timeout): the name was
    never published, and the temp file is gone."""
    source = remote_files.helper_source().encode()
    data = b"x" * 1000
    proc = subprocess.Popen(
        [sys.executable, "-I", "-c", remote_files.BOOTSTRAP,
         remote_helper.encode(_request(root, "shared/config/x.csv", data))],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
    )  # fmt: skip
    assert proc.stdin is not None
    proc.stdin.write(b"%d\n" % len(source) + source + data[:10])
    proc.stdin.flush()
    deadline = time.time() + 10
    while not _temps(root / "shared/config") and time.time() < deadline:
        time.sleep(0.02)
    assert _temps(root / "shared/config"), "the helper never started writing"
    proc.terminate()
    out, _ = proc.communicate(timeout=10)
    assert json.loads(out)["error"] == "incomplete"
    assert _listing(root / "shared/config") == []
