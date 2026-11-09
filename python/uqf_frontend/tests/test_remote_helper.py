"""The browser's server-side helper, run against a local tree with no ssh (#1950).

Every confinement rule remote_helper.py states is exercised on a real
directory: absolute paths, `..`, links out of (and inside) the root, special
files, unusual names, bounds, and the release metadata read through `current`
without following it.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from uqf_frontend import remote_helper
from uqf_frontend.remote_helper import Refusal, handle, split_path

#: Names a shell or a naive path join would mishandle.
UNUSUAL = [
    "with space",
    "it's quoted",
    'double "quoted"',
    "new\nline",
    "-leading-dash",
    "--help",
    "zażółć gęślą jaźń",
    "日本語.txt",
    "$(touch pwned)",
    "`id`",
    "semi;colon",
    "back\\slash",
    "*",
    "tab\there",
]


@pytest.fixture
def deployment(tmp_path: Path) -> Path:
    """A deployment root as `uqs deploy push` lays it out, plus an outside
    directory a link could escape to."""
    root = tmp_path / "srv" / "uqf"
    rel = root / "releases" / "20261101T090000Z-abc1234"
    (rel / "config").mkdir(parents=True)
    (rel / "config" / "settings.toml").write_text("x = 1\n")
    (rel / "deploy-report.json").write_text(
        json.dumps(
            {
                "release": "20261101T090000Z-abc1234",
                "revision": "abc1234",
                "status": "deployed",
                "profile": "essential",
                "previous_release": None,
                "processes": [{"procname": "rdb1"}],
                "secret_looking": "not a reported field",
            }
        )
    )
    (root / "current").symlink_to("releases/20261101T090000Z-abc1234")
    (root / "shared" / "data").mkdir(parents=True)
    (root / "shared" / "data" / "big.bin").write_bytes(b"\0" * 12345)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("do not list me\n")
    return root


def _list(root: Path, path: str = "", limit: int = 500) -> dict:
    return handle({"protocol": 1, "op": "list", "root": str(root), "path": path, "limit": limit})


def _entries(answer: dict) -> dict[str, dict]:
    assert answer["ok"], answer
    return {e["name"]: e for e in answer["entries"]}


# ------------------------------------------------------------ path checks


@pytest.mark.parametrize(
    ("path", "parts"),
    [
        ("", []),
        (".", []),
        ("./", []),
        ("releases", ["releases"]),
        ("releases/", ["releases"]),
        ("releases//r1/./config", ["releases", "r1", "config"]),
        ("-leading-dash", ["-leading-dash"]),
        ("a b/it's", ["a b", "it's"]),
        ("..foo/bar..", ["..foo", "bar.."]),
    ],
)
def test_a_relative_path_splits_into_its_components(path, parts):
    assert split_path(path) == parts


@pytest.mark.parametrize(
    ("path", "said"),
    [
        ("/etc", "absolute"),
        ("/", "absolute"),
        ("//srv/uqf", "absolute"),
        ("..", "'..'"),
        ("../outside", "'..'"),
        ("releases/../../outside", "'..'"),
        ("shared/data/..", "'..'"),
        ("a\x00b", "NUL"),
        ("\udcff", "UTF-8"),
        ("x" * (remote_helper.MAX_PATH + 1), "longer"),
    ],
)
def test_an_escaping_or_malformed_path_is_refused(path, said):
    with pytest.raises(Refusal) as caught:
        split_path(path)
    assert caught.value.error == "refused"
    assert said in caught.value.detail


@pytest.mark.parametrize("path", ["/etc", "../outside", "releases/../../outside", "a\x00b"])
def test_the_helper_refuses_escapes_itself_not_only_the_api(deployment, path):
    answer = _list(deployment, path)
    assert answer == {
        "protocol": 1,
        "ok": False,
        "error": "refused",
        "detail": answer["detail"],
    }


def test_a_non_string_path_is_a_bad_request(deployment):
    answer = handle({"protocol": 1, "op": "list", "root": str(deployment), "path": ["etc"]})
    assert answer["error"] == "bad_request"


# ------------------------------------------------------------ listing


def test_the_root_lists_types_sizes_and_blocks_current(deployment):
    answer = _list(deployment)
    assert answer["path"] == ""
    assert answer["parent"] is None
    assert answer["total"] == 3
    assert answer["truncated"] is False
    found = _entries(answer)
    assert found["releases"]["kind"] == "dir"
    assert found["releases"]["blocked"] is False
    assert found["releases"]["size"] is None
    assert found["shared"]["kind"] == "dir"
    # `current` is a link: listed, blocked, never followed
    assert found["current"]["kind"] == "symlink"
    assert found["current"]["blocked"] is True
    assert "not followed" in found["current"]["reason"]
    # directories first
    assert [e["kind"] for e in answer["entries"]] == ["dir", "dir", "symlink"]


def test_a_file_reports_its_size_and_a_subdirectory_its_parent(deployment):
    answer = _list(deployment, "shared/data")
    assert answer["path"] == "shared/data"
    assert answer["parent"] == "shared"
    big = _entries(answer)["big.bin"]
    assert (big["kind"], big["size"], big["blocked"]) == ("file", 12345, False)
    assert _list(deployment, "shared")["parent"] == ""


def test_release_metadata_comes_from_current_without_following_it(deployment):
    release = _list(deployment, "shared")["release"]
    assert release["current"] == "releases/20261101T090000Z-abc1234"
    assert release["release"] == "20261101T090000Z-abc1234"
    assert release["report_error"] is None
    assert release["report"] == {
        "release": "20261101T090000Z-abc1234",
        "revision": "abc1234",
        "status": "deployed",
        "profile": "essential",
        "previous_release": None,
    }


def test_an_absolute_current_inside_the_root_still_names_its_release(deployment):
    (deployment / "current").unlink()
    (deployment / "current").symlink_to(deployment / "releases" / "20261101T090000Z-abc1234")
    assert _list(deployment)["release"]["release"] == "20261101T090000Z-abc1234"


@pytest.mark.parametrize(
    ("link", "said"),
    [
        ("../../outside", "does not name a release"),
        ("releases/../../outside", "does not name a release"),
        ("/etc", "does not name a release"),
    ],
)
def test_a_current_pointing_outside_is_not_read(deployment, link, said):
    (deployment / "current").unlink()
    (deployment / "current").symlink_to(link)
    release = _list(deployment)["release"]
    assert release["release"] is None
    assert release["report"] is None
    assert said in release["report_error"]


@pytest.mark.parametrize("key", ["ssh", "host"])
def test_a_reports_ssh_destination_is_ssh_whichever_release_wrote_it(deployment, key):
    """#2123: a report names the ssh destination `ssh`; one from a release
    before then, still on the server, names it `host` - read as `ssh` too."""
    report = deployment / "releases" / "20261101T090000Z-abc1234" / "deploy-report.json"
    report.write_text(json.dumps({**json.loads(report.read_text()), key: "svc@box"}))
    shown = _list(deployment)["release"]["report"]
    assert shown["ssh"] == "svc@box" and "host" not in shown


def test_a_report_that_is_a_link_is_not_read(deployment, tmp_path):
    report = deployment / "releases" / "20261101T090000Z-abc1234" / "deploy-report.json"
    report.unlink()
    report.symlink_to(tmp_path / "outside" / "secret")
    release = _list(deployment)["release"]
    assert release["report"] is None
    assert "cannot be read" in release["report_error"]


def test_no_current_and_no_report_are_said_not_raised(deployment):
    (deployment / "current").unlink()
    assert _list(deployment)["release"]["report_error"] == "no current release"
    (deployment / "current").symlink_to("releases/20261101T090000Z-abc1234")
    (deployment / "releases" / "20261101T090000Z-abc1234" / "deploy-report.json").unlink()
    assert "no deploy-report.json" in _list(deployment)["release"]["report_error"]


# ------------------------------------------------------------ links


def test_a_link_escaping_the_root_is_blocked_and_cannot_be_entered(deployment, tmp_path):
    (deployment / "shared" / "escape").symlink_to(tmp_path / "outside")
    found = _entries(_list(deployment, "shared"))
    assert found["escape"]["blocked"] is True
    for path in ("shared/escape", "shared/escape/", "shared/escape/secret"):
        answer = _list(deployment, path)
        assert answer["ok"] is False
        assert answer["error"] == "refused"
        assert "symbolic link" in answer["detail"]
        assert "do not list me" not in json.dumps(answer)


def test_a_link_inside_the_root_is_not_followed_either(deployment):
    """The rule is no links at all - `current` included - so whether a link
    escapes never has to be decided."""
    answer = _list(deployment, "current")
    assert answer["error"] == "refused"
    answer = _list(deployment, "current/config")
    assert answer["error"] == "refused"


def test_a_root_level_link_to_slash_is_refused(deployment):
    (deployment / "rootfs").symlink_to("/")
    assert _list(deployment, "rootfs")["error"] == "refused"
    assert _list(deployment, "rootfs/etc")["error"] == "refused"


def test_a_dangling_link_is_listed_as_blocked(deployment):
    (deployment / "shared" / "dangling").symlink_to("nowhere")
    entry = _entries(_list(deployment, "shared"))["dangling"]
    assert (entry["kind"], entry["blocked"]) == ("symlink", True)


def test_a_directory_swapped_for_a_link_between_lstat_and_open_is_refused(
    deployment, tmp_path, monkeypatch
):
    """The race the descriptor walk closes: the lstat sees a directory, the
    open finds a link. O_NOFOLLOW refuses it."""
    target = deployment / "shared" / "data"
    real_stat = os.stat
    swapped = []

    def stat_then_swap(name, *args, **kwargs):
        result = real_stat(name, *args, **kwargs)
        if name == "data" and not swapped:
            swapped.append(True)
            target.rename(deployment / "shared" / "data-moved")
            target.symlink_to(tmp_path / "outside")
        return result

    monkeypatch.setattr(remote_helper.os, "stat", stat_then_swap)
    answer = _list(deployment, "shared/data")
    assert swapped
    assert answer["ok"] is False
    assert answer["error"] == "refused"
    assert "changed while it was opened" in answer["detail"]


def test_a_directory_replaced_by_another_between_lstat_and_open_is_refused(deployment, monkeypatch):
    """Not a link but a different directory: the identity check catches it."""
    target = deployment / "shared" / "data"
    real_stat = os.stat
    swapped = []

    def stat_then_swap(name, *args, **kwargs):
        result = real_stat(name, *args, **kwargs)
        if name == "data" and not swapped:
            swapped.append(True)
            target.rename(deployment / "shared" / "data-moved")
            target.mkdir()
        return result

    monkeypatch.setattr(remote_helper.os, "stat", stat_then_swap)
    answer = _list(deployment, "shared/data")
    assert answer["error"] == "refused"


# ------------------------------------------------------------ special files


def test_a_fifo_is_blocked_and_cannot_be_entered(deployment):
    os.mkfifo(deployment / "shared" / "pipe")
    entry = _entries(_list(deployment, "shared"))["pipe"]
    assert (entry["kind"], entry["blocked"]) == ("special", True)
    assert "FIFO" in entry["reason"]
    assert _list(deployment, "shared/pipe")["error"] == "not_a_directory"


def test_a_socket_is_blocked():
    # AF_UNIX caps a socket's path near 104 bytes on macOS: pytest's tmp_path
    # is longer, so this one lives in a short directory of its own.
    short = Path(tempfile.mkdtemp(prefix="uqfb", dir="/tmp"))
    sock = socket.socket(socket.AF_UNIX)
    try:
        sock.bind(str(short / "s"))
        entry = _entries(_list_dir_of(short))["s"]
    finally:
        sock.close()
        shutil.rmtree(short)
    assert (entry["kind"], entry["blocked"]) == ("special", True)
    assert "socket" in entry["reason"]


def _list_dir_of(directory: Path) -> dict:
    return handle({"protocol": 1, "op": "list", "root": str(directory), "path": ""})


def test_a_device_is_blocked():
    entry = _entries(_list_dir_of(Path("/dev")))["null"]
    assert (entry["kind"], entry["blocked"]) == ("special", True)
    assert "device" in entry["reason"]


# ------------------------------------------------------------ unusual names


@pytest.mark.parametrize("name", UNUSUAL)
def test_an_unusual_name_is_listed_and_entered_as_itself(deployment, name):
    d = deployment / "shared" / name
    d.mkdir()
    (d / name).write_text("12345")
    found = _entries(_list(deployment, "shared"))
    assert found[name]["kind"] == "dir"
    inner = _list(deployment, f"shared/{name}")
    assert inner["path"] == f"shared/{name}"
    assert inner["parent"] == "shared"
    assert _entries(inner)[name]["size"] == 5


def test_a_name_that_is_not_utf8_is_blocked(deployment):
    raw = os.path.join(os.fsencode(deployment / "shared"), b"bad\xff")
    try:
        os.mkdir(raw)
    except OSError:
        pytest.skip("this filesystem refuses names that are not UTF-8")
    found = {e["name"]: e for e in _list(deployment, "shared")["entries"]}
    entry = found["bad�"]
    assert entry["blocked"] is True
    assert "UTF-8" in entry["reason"]
    json.dumps(found)  # the answer itself still encodes


# ------------------------------------------------------------ bounds


def test_a_listing_is_capped_at_the_limit_and_says_so(deployment):
    d = deployment / "shared" / "many"
    d.mkdir()
    for i in range(30):
        (d / f"f{i:02d}").write_text("")
    answer = _list(deployment, "shared/many", limit=10)
    assert len(answer["entries"]) == 10
    assert answer["total"] == 30
    assert answer["truncated"] is True
    assert [e["name"] for e in answer["entries"]] == [f"f{i:02d}" for i in range(10)]


def test_a_cut_listing_keeps_directories_first(deployment):
    """#2478: `limit` keeps the first entries of the order shown - directories
    first - not the first names alphabetically, sorted afterwards."""
    d = deployment / "shared" / "mixed"
    d.mkdir()
    (d / "a").write_text("")
    (d / "b").write_text("")
    (d / "z").mkdir()
    answer = _list(deployment, "shared/mixed", limit=1)
    assert [(e["name"], e["kind"]) for e in answer["entries"]] == [("z", "dir")]
    assert answer["truncated"] is True


def test_the_limit_is_held_to_its_maximum(deployment):
    answer = _list(deployment, "", limit=remote_helper.MAX_LIMIT * 10)
    assert answer["ok"] is True


def test_the_scan_stops_at_its_cap(deployment, monkeypatch):
    monkeypatch.setattr(remote_helper, "SCAN_CAP", 5)
    d = deployment / "shared" / "many"
    d.mkdir()
    for i in range(8):
        (d / f"f{i}").write_text("")
    answer = _list(deployment, "shared/many", limit=100)
    assert answer["total"] is None
    assert answer["truncated"] is True
    assert len(answer["entries"]) == 5


@pytest.mark.parametrize("limit", [0, -1, "10", True, 1.5])
def test_a_bad_limit_is_a_bad_request(deployment, limit):
    answer = handle({"protocol": 1, "op": "list", "root": str(deployment), "limit": limit})
    assert answer["error"] == "bad_request"


@pytest.mark.parametrize(
    "request_",
    [
        None,
        [],
        {"protocol": 2, "op": "list", "root": "/"},
        {"protocol": 1, "op": "unsupported", "root": "/"},
        {"protocol": 1, "op": "list", "root": "relative"},
    ],
)
def test_an_unknown_request_is_refused(request_):
    assert handle(request_)["error"] == "bad_request"


def test_a_missing_path_and_a_file_path_are_told_apart(deployment):
    assert _list(deployment, "nope")["error"] == "not_found"
    assert _list(deployment, "shared/data/big.bin")["error"] == "not_a_directory"


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads everything")
def test_an_unreadable_directory_is_denied(deployment):
    locked = deployment / "shared" / "locked"
    locked.mkdir()
    locked.chmod(0o000)
    try:
        assert _list(deployment, "shared/locked")["error"] == "denied"
    finally:
        locked.chmod(0o755)


# ------------------------------------------------------------ as it is run


def test_the_helper_runs_as_a_program_from_stdin(deployment):
    """Exactly how ssh runs it: the source on stdin to `python -I -`, the
    request as its one argument. A name with a newline and quotes survives."""
    name = 'it\'s a\nnew "line"'
    (deployment / "shared" / name).mkdir()
    request = {"protocol": 1, "op": "list", "root": str(deployment), "path": f"shared/{name}"}
    done = subprocess.run(
        [sys.executable, "-I", "-", remote_helper.encode(request)],
        input=Path(remote_helper.__file__).read_text(),
        capture_output=True,
        text=True,
        check=True,
    )
    answer = json.loads(done.stdout)
    assert answer["ok"] is True
    assert answer["path"] == f"shared/{name}"


def test_a_garbled_argument_is_a_refusal_not_a_crash():
    src = Path(remote_helper.__file__).read_text()
    for argv in ([], ["not base64!"], ["e30=", "extra"]):
        done = subprocess.run(
            [sys.executable, "-I", "-", *argv], input=src, capture_output=True, text=True
        )
        assert done.returncode == 0
        assert json.loads(done.stdout)["error"] == "bad_request"


def test_the_helper_imports_only_the_standard_library():
    """It runs with the release's interpreter, which carries uqf's
    dependencies, but must not need them - nor anything of this package."""
    tree = ast.parse(Path(remote_helper.__file__).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "a relative import needs the package"
            imported.add((node.module or "").split(".")[0])
    assert imported <= set(sys.stdlib_module_names), imported - set(sys.stdlib_module_names)
    assert not imported & {"subprocess", "shutil", "pty", "multiprocessing"}


#: Only upload, confirmed deletion and the push history may write (#1951,
#: #1953, #2002).
WRITERS = {
    "delete_paths",
    "make_dirs",
    "_new_mode",
    "_receive",
    "_publish_new",
    "sweep",
    "publish",
    "_publish_step",
    "upload",
    "_interrupted",
    # the push history (#2002): its directories, copies, manifest, retention
    # and restore - every one confined to shared/config/.uqf-history but the
    # restore's own publish and delete
    "make_dirs_at",
    "_copy_into",
    "_remove_copy",
    "_write_manifest",
    "_keeper",
    "_forget",
    "_rmtree",
    "prune",
    "_new_push",
    "_restore_one",
}


def test_only_the_upload_and_delete_functions_write():
    """No os call that writes, removes or renames outside the upload op's
    own functions - so `list` reads - and none anywhere that runs a program.
    The helper imports no subprocess (the stdlib test above names them)."""
    src = Path(remote_helper.__file__).read_text()
    tree = ast.parse(src)
    forbidden = {"remove", "unlink", "rmdir", "rename", "replace", "mkdir", "makedirs",
                 "chmod", "fchmod", "chown", "symlink", "link", "truncate", "write", "umask",
                 "fsync"}  # fmt: skip
    running = {"system", "popen", "execv", "execvp", "spawnv", "fork"}
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    assert WRITERS <= {f.name for f in functions}
    for fn in functions:
        called = {
            node.func.attr
            for node in ast.walk(fn)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "os"
        }
        assert called & running == set(), fn.name
        if fn.name not in WRITERS:
            assert called & forbidden == set(), fn.name
            text = ast.get_source_segment(src, fn) or ""
            assert "O_WRONLY" not in text and "O_RDWR" not in text and "O_CREAT" not in text
