"""The program the file browser runs ON THE SERVER (#1950). Standard library only.

remote_files.py sends this file's source over ssh to the deployed release's
own interpreter, `<dest>/current/.venv/bin/python -I -`, with one argument:
the request, as base64 of a JSON object. Nothing is installed on the server
and no other Python is tried; the source travels with every call, so the
server never runs a stale copy. It is also an ordinary module here, so the
API checks a path with the same `split_path` before anything is sent, and
the tests run `handle` against a local directory tree with no ssh at all.

PROTOCOL (version 1). Request:

    {"protocol": 1, "op": "list", "root": "/srv/uqf", "path": "releases", "limit": 500}

Answer, one JSON line on stdout, exit status 0 whether or not it refused:

    {"protocol": 1, "ok": true, "path": "releases", "parent": "", "entries": [...],
     "total": 3, "truncated": false, "release": {...}}
    {"protocol": 1, "ok": false, "error": "refused", "detail": "..."}

`error` is one of ERRORS. A non-zero exit means the helper itself failed.

CONFINEMENT. `root` is the target's deployment root, from the operator's
registry, and is trusted. Everything under it is not:

  - `path` is relative: absolute paths, NUL bytes and `..` components are
    refused before any filesystem call. Empty and `.` components are ignored.
  - The walk never follows a symbolic link. Each component is opened with
    O_NOFOLLOW relative to its parent's descriptor, and its identity is
    checked against the lstat taken first, so a link swapped in between the
    two cannot redirect the walk outside the root.
  - A listed symlink, FIFO, socket or device is shown as BLOCKED, with why,
    and cannot be navigated into. That includes `current` itself: the release
    it names is reported under `release`, and browsed as `releases/<id>`.
  - A name that is not UTF-8 is blocked too: it could not be named back.
  - A listing scans at most SCAN_CAP names and returns at most `limit`
    (never above MAX_LIMIT); `truncated` says when there were more, and
    `total` is None when the scan stopped early.

The list operation only reads. The explicitly confirmed delete operation
removes regular files or empty directories relative to confined parent descriptors.
It never recurses or follows links.
"""

import base64
import errno
import json
import os
import stat
import sys

PROTOCOL = 1
#: Longest relative path accepted, in characters.
MAX_PATH = 4096
#: Most entries one answer carries, whatever the request asks for.
MAX_LIMIT = 5000
#: Most names one listing reads before it stops counting.
SCAN_CAP = 50000
#: Largest deploy-report.json read, in bytes.
MAX_REPORT = 1024 * 1024
#: The deploy report's fields worth showing an operator (deploy/report.py).
REPORT_FIELDS = (
    "release",
    "revision",
    "status",
    "stage",
    "error",
    "profile",
    "host",
    "dest",
    "previous_release",
    "dirty",
    "live",
    "rollback",
    "qversion",
    "jobs",
    "artifact",
)
ERRORS = ("bad_request", "refused", "not_found", "not_a_directory", "denied")


class Refusal(Exception):
    """A request this helper declines, with the ERRORS word for why."""

    def __init__(self, error, detail):
        super().__init__(detail)
        self.error = error
        self.detail = detail


def split_path(path):
    """The components of a root-relative `path`, or Refusal.

    "", "." and "/"-separated runs of them are the root itself. The API runs
    this before it opens ssh, and the server runs it again."""
    if not isinstance(path, str):
        raise Refusal("bad_request", "path must be a string")
    if len(path) > MAX_PATH:
        raise Refusal("refused", f"path is longer than {MAX_PATH} characters")
    if "\x00" in path:
        raise Refusal("refused", "path contains a NUL byte")
    try:
        path.encode("utf-8")
    except UnicodeEncodeError:
        raise Refusal("refused", "path is not valid UTF-8") from None
    if path.startswith("/"):
        raise Refusal("refused", "absolute paths are refused - give a path relative to the root")
    parts = [p for p in path.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise Refusal("refused", "'..' is refused - paths stay inside the deployment root")
    return parts


def _open_dir(fd, name):
    """Open directory `name` under descriptor `fd` without following a link."""
    try:
        before = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        raise Refusal("not_found", f"{name!r} does not exist") from None
    except PermissionError:
        raise Refusal("denied", f"{name!r} cannot be read") from None
    if stat.S_ISLNK(before.st_mode):
        raise Refusal("refused", f"{name!r} is a symbolic link - links are not followed")
    if not stat.S_ISDIR(before.st_mode):
        raise Refusal("not_a_directory", f"{name!r} is not a directory")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        opened = os.open(name, flags, dir_fd=fd)
    except PermissionError:
        raise Refusal("denied", f"{name!r} cannot be read") from None
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.ENOENT):
            raise Refusal("refused", f"{name!r} changed while it was opened") from None
        raise
    after = os.fstat(opened)
    if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
        os.close(opened)
        raise Refusal("refused", f"{name!r} changed while it was opened")
    return opened


def _open_root(root):
    if not isinstance(root, str) or not root.startswith("/"):
        raise Refusal("bad_request", "root must be an absolute path")
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    try:
        return os.open(root, flags)
    except FileNotFoundError:
        raise Refusal("not_found", f"the deployment root {root} does not exist") from None
    except PermissionError:
        raise Refusal("denied", f"the deployment root {root} cannot be read") from None
    except NotADirectoryError:
        raise Refusal("not_a_directory", f"the deployment root {root} is not a directory") from None


def walk(root, parts):
    """A descriptor for root/parts..., opened one confined step at a time."""
    fd = _open_root(root)
    try:
        for part in parts:
            nxt = _open_dir(fd, part)
            os.close(fd)
            fd = nxt
    except BaseException:
        os.close(fd)
        raise
    return fd


def _readable_name(name):
    """(name to show, why it is blocked or None): a name that is not UTF-8
    could not be sent back to name it, so it is shown escaped and blocked."""
    try:
        name.encode("utf-8")
        return name, None
    except UnicodeEncodeError:
        raw = os.fsencode(name)
        return raw.decode("utf-8", "replace"), "name is not valid UTF-8"


def describe(fd, name):
    """One listing entry for `name` in directory `fd`, never following it."""
    shown, bad_name = _readable_name(name)
    try:
        st = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except OSError as exc:
        return {"name": shown, "kind": "unknown", "size": None, "mtime": None,
                "blocked": True, "reason": f"cannot be read: {exc.strerror}"}  # fmt: skip
    mode = st.st_mode
    entry = {"name": shown, "size": None, "mtime": st.st_mtime, "blocked": False, "reason": None}
    if stat.S_ISDIR(mode):
        entry["kind"] = "dir"
    elif stat.S_ISREG(mode):
        entry["kind"] = "file"
        entry["size"] = st.st_size
    elif stat.S_ISLNK(mode):
        entry["kind"] = "symlink"
        entry["blocked"], entry["reason"] = True, "symbolic link - not followed"
    else:
        what = (
            "FIFO" if stat.S_ISFIFO(mode)
            else "socket" if stat.S_ISSOCK(mode)
            else "device" if stat.S_ISCHR(mode) or stat.S_ISBLK(mode)
            else "special file"
        )  # fmt: skip
        entry["kind"] = "special"
        entry["blocked"], entry["reason"] = True, f"{what} - not supported"
    if bad_name is not None:
        entry["blocked"], entry["reason"] = True, bad_name
    return entry


def list_dir(fd, limit):
    """(entries, total or None when the scan stopped early, truncated)."""
    names = []
    complete = True
    try:
        with os.scandir(fd) as it:
            for entry in it:
                if len(names) >= SCAN_CAP:
                    complete = False
                    break
                names.append(entry.name)
    except PermissionError:
        raise Refusal("denied", "the directory cannot be listed") from None
    names.sort()
    shown = [describe(fd, n) for n in names[:limit]]
    # directories first, then by name: what an operator scans for
    shown.sort(key=lambda e: (e["kind"] != "dir", e["name"]))
    truncated = len(names) > limit or not complete
    return shown, (len(names) if complete else None), truncated


def _read_report(root, release):
    """The current release's deploy-report.json fields, or why it was not read."""
    try:
        fd = walk(root, ["releases", release])
    except Refusal as exc:
        return None, exc.detail
    try:
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        try:
            rfd = os.open("deploy-report.json", flags, dir_fd=fd)
        except FileNotFoundError:
            return None, "the release has no deploy-report.json"
        except OSError as exc:
            return None, f"deploy-report.json cannot be read: {exc.strerror}"
        with os.fdopen(rfd, "rb") as fh:
            st = os.fstat(fh.fileno())
            if not stat.S_ISREG(st.st_mode):
                return None, "deploy-report.json is not a regular file"
            if st.st_size > MAX_REPORT:
                return None, f"deploy-report.json is larger than {MAX_REPORT} bytes"
            try:
                data = json.loads(fh.read(MAX_REPORT + 1).decode("utf-8"))
            except ValueError:
                return None, "deploy-report.json is not valid JSON"
    finally:
        os.close(fd)
    if not isinstance(data, dict):
        return None, "deploy-report.json is not a JSON object"
    return {k: data[k] for k in REPORT_FIELDS if k in data}, None


def release_info(root, root_fd):
    """What `current` names - read as link text, never followed - and that
    release's deploy report."""
    out = {"current": None, "release": None, "report": None, "report_error": None}
    try:
        st = os.stat("current", dir_fd=root_fd, follow_symlinks=False)
    except FileNotFoundError:
        out["report_error"] = "no current release"
        return out
    except OSError as exc:
        out["report_error"] = f"current cannot be read: {exc.strerror}"
        return out
    if not stat.S_ISLNK(st.st_mode):
        out["report_error"] = "current is not a link to a release"
        return out
    link = os.readlink("current", dir_fd=root_fd)
    out["current"], _ = _readable_name(link)
    rel = link
    prefix = root.rstrip("/") + "/"
    if rel.startswith(prefix):
        rel = rel[len(prefix) :]
    parts = rel.split("/")
    if len(parts) != 2 or parts[0] != "releases" or parts[1] in ("", ".", ".."):
        out["report_error"] = "current does not name a release under releases/"
        return out
    out["release"] = parts[1]
    out["report"], out["report_error"] = _read_report(root, parts[1])
    return out


MAX_DELETE = 100


def delete_paths(root, paths, confirmed):
    """Remove named regular files or empty directories, never following links."""
    if confirmed is not True:
        raise Refusal("refused", "explicit confirmation is required")
    if not isinstance(paths, list) or not 1 <= len(paths) <= MAX_DELETE:
        raise Refusal("bad_request", f"paths must contain 1 to {MAX_DELETE} entries")
    if not all(isinstance(path, str) for path in paths):
        raise Refusal("bad_request", "every path must be a string")
    results = []
    for path in paths:
        try:
            parts = split_path(path)
            if not parts:
                raise Refusal("refused", "the deployment root cannot be deleted")
            fd = walk(root, parts[:-1])
            try:
                before = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
                if stat.S_ISREG(before.st_mode):
                    os.unlink(parts[-1], dir_fd=fd)
                elif stat.S_ISDIR(before.st_mode):
                    os.rmdir(parts[-1], dir_fd=fd)
                else:
                    raise Refusal(
                        "refused", "only regular files and empty directories may be deleted"
                    )
            finally:
                os.close(fd)
            results.append({"path": path, "deleted": True, "error": None})
        except (Refusal, OSError) as exc:
            detail = exc.detail if isinstance(exc, Refusal) else exc.strerror
            results.append({"path": path, "deleted": False, "error": detail})
    return {"protocol": PROTOCOL, "ok": True, "results": results}


def handle(request):
    """Answer one request object; Refusal becomes an ok:false answer."""
    try:
        if not isinstance(request, dict):
            raise Refusal("bad_request", "the request is not a JSON object")
        if request.get("protocol") != PROTOCOL:
            raise Refusal("bad_request", f"protocol {request.get('protocol')!r} is not {PROTOCOL}")
        if request.get("op") == "delete":
            return delete_paths(request.get("root"), request.get("paths"), request.get("confirmed"))
        if request.get("op") != "list":
            raise Refusal("bad_request", f"op {request.get('op')!r} is not supported")
        limit = request.get("limit", MAX_LIMIT)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise Refusal("bad_request", "limit must be a positive integer")
        limit = min(limit, MAX_LIMIT)
        root = request.get("root")
        parts = split_path(request.get("path", ""))
        root_fd = _open_root(root)
        try:
            release = release_info(root, root_fd)
        finally:
            os.close(root_fd)
        fd = walk(root, parts)
        try:
            entries, total, truncated = list_dir(fd, limit)
        finally:
            os.close(fd)
    except Refusal as exc:
        return {"protocol": PROTOCOL, "ok": False, "error": exc.error, "detail": exc.detail}
    return {
        "protocol": PROTOCOL,
        "ok": True,
        "path": "/".join(parts),
        "parent": "/".join(parts[:-1]) if parts else None,
        "entries": entries,
        "total": total,
        "truncated": truncated,
        "release": release,
    }


def encode(request):
    """The one argument `main` reads: base64 of the request's JSON."""
    return base64.b64encode(json.dumps(request).encode("utf-8")).decode("ascii")


def main(argv):
    if len(argv) != 2:
        answer = {"protocol": PROTOCOL, "ok": False, "error": "bad_request",
                  "detail": "expected one argument, the encoded request"}  # fmt: skip
    else:
        try:
            request = json.loads(base64.b64decode(argv[1], validate=True).decode("utf-8"))
        except ValueError:
            request = None
        answer = handle(request)
    sys.stdout.write(json.dumps(answer) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
