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

`list` only reads: one directory and one deploy report. Confirmed `delete`
removes named regular files and empty directories, never following links or recursing.

UPLOAD (#1951). `upload` is the one op that writes, and only below
UPLOAD_ROOT (shared/config/, the operator-supplied files every release
links in). Its request carries the destination path, the file's `size` and
`sha256`, and `overwrite`; the bytes follow the helper on stdin (FRAMING).
The answer names what was written: `{"ok": true, "path", "size", "sha256",
"replaced", "mode", "created", "swept"}`.

  - The destination path is confined as a browse path is, and must name a
    file strictly below UPLOAD_ROOT, at most MAX_UPLOAD_DEPTH deep, each
    name at most MAX_NAME_BYTES bytes and none starting with TEMP_PREFIX.
  - Missing directories below the root are created one at a time, each
    re-opened with O_NOFOLLOW and its identity checked; a link anywhere on
    the way is refused, never followed or replaced.
  - The bytes go to a fresh temp file (O_CREAT|O_EXCL|O_NOFOLLOW) in the
    destination directory, are counted and hashed as they arrive, and are
    fsync'd. Fewer or more bytes than `size`, or another sha256, publishes
    nothing.
  - Publishing is one atomic step in that directory: os.link(temp, name)
    when overwriting is not allowed - it fails if the name exists, with no
    window between check and publish - else os.replace(temp, name). Either
    way a reader sees the old file or the whole new one, never part of it.
  - An existing file is replaced only with `overwrite`; a link, directory
    or special file at the name is never replaced. A replaced file's
    permission bits (not setuid, setgid or sticky) are kept; a new file gets
    0o666 less the umask, as any newly created file would.
  - The temp file is removed on every path that does not publish it,
    including a hangup or SIGTERM (ssh dropped or timed out), which are
    turned into a refusal so the cleanup runs. Only SIGKILL leaves one; the
    next upload to that directory sweeps temp files older than STALE_TEMP.

FRAMING. `list` is sent as before: the helper's source on stdin to
`python -I -`. An upload cannot be, because `-` reads stdin to its end as
source. So the API runs `python -I -c BOOTSTRAP <request>`, where BOOTSTRAP
(remote_files.BOOTSTRAP, fixed text) reads one decimal line N, then N bytes
of this source, which it runs, then calls `main(argv, stdin)`; the rest of
stdin, exactly `size` bytes, is the file. No path is in either.
"""

import base64
import errno
import hashlib
import json
import os
import re
import signal
import stat
import sys
import time

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
ERRORS = (
    "bad_request",
    "refused",
    "not_found",
    "not_a_directory",
    "denied",
    # upload only: a destination outside UPLOAD_ROOT, a name that exists
    # without `overwrite`, a file over MAX_UPLOAD, and bytes that did not
    # arrive whole - in every one, nothing was written
    "unsupported",
    "exists",
    "too_large",
    "incomplete",
)
#: Largest file one upload carries, in bytes. The API refuses a larger body
#: before ssh, and the browser before sending (web/src/api.ts).
MAX_UPLOAD = 16 * 1024 * 1024
#: The one directory uploads may go below, relative to the deployment root:
#: the operator-supplied files each release links in (uqs.deploy.config).
UPLOAD_ROOT = ("shared", "config")
#: Most directories below UPLOAD_ROOT an upload's path may hold.
MAX_UPLOAD_DEPTH = 16
#: Longest single name, in bytes - what most filesystems allow.
MAX_NAME_BYTES = 255
#: Names the upload's temp files take, and so no upload may.
TEMP_PREFIX = ".uqf-upload-"
#: Seconds after which a temp file left in a destination directory - by a
#: helper killed outright, which no cleanup survives - is swept by the next
#: upload there. Far past any upload's own time limit.
STALE_TEMP = 3600
#: Bytes read from stdin at a time.
CHUNK = 64 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}")


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


# ------------------------------------------------------------ upload (#1951)


def upload_parts(path):
    """The components of an upload's destination `path`, or Refusal: a
    confined path (split_path) naming a file strictly below UPLOAD_ROOT. The
    API runs this before it opens ssh, and the server runs it again."""
    parts = split_path(path)
    where = "/".join(UPLOAD_ROOT)
    if tuple(parts[: len(UPLOAD_ROOT)]) != UPLOAD_ROOT or len(parts) <= len(UPLOAD_ROOT):
        raise Refusal("unsupported", f"uploads go to a file below {where}/ and nowhere else")
    if len(parts) - len(UPLOAD_ROOT) - 1 > MAX_UPLOAD_DEPTH:
        raise Refusal("refused", f"more than {MAX_UPLOAD_DEPTH} directories below {where}/")
    for part in parts:
        if len(part.encode("utf-8")) > MAX_NAME_BYTES:
            raise Refusal("refused", f"a name is longer than {MAX_NAME_BYTES} bytes")
        if part.startswith(TEMP_PREFIX):
            raise Refusal("refused", f"names starting {TEMP_PREFIX!r} are the upload's own")
    return parts


def make_dirs(root, parts):
    """(descriptor for root/parts..., the paths created): `walk`, creating
    each missing directory and opening it again, confined, before the next."""
    fd = _open_root(root)
    created = []
    try:
        for i, part in enumerate(parts):
            try:
                nxt = _open_dir(fd, part)
            except Refusal as exc:
                if exc.error != "not_found":
                    raise
                try:
                    os.mkdir(part, 0o777, dir_fd=fd)
                    created.append("/".join(parts[: i + 1]))
                except FileExistsError:
                    pass  # made meanwhile: opened, and checked, below
                except PermissionError:
                    raise Refusal("denied", f"{part!r} cannot be created") from None
                nxt = _open_dir(fd, part)
            os.close(fd)
            fd = nxt
    except BaseException:
        os.close(fd)
        raise
    return fd, created


def _existing(fd, name, overwrite):
    """The stat of what `name` is now, or None; Refusal when it may not be
    replaced - never a link, a directory or a special file, and nothing at
    all without `overwrite`."""
    try:
        st = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(st.st_mode):
        raise Refusal("refused", f"{name!r} is a symbolic link - links are not replaced")
    if stat.S_ISDIR(st.st_mode):
        raise Refusal("refused", f"{name!r} is a directory")
    if not stat.S_ISREG(st.st_mode):
        raise Refusal("refused", f"{name!r} is a special file")
    if not overwrite:
        raise Refusal("exists", f"{name!r} exists - replacing it must be asked for")
    return st


def _new_mode():
    """What a newly created file gets: 0o666 less this process's umask."""
    mask = os.umask(0o022)
    os.umask(mask)
    return 0o666 & ~mask


def _receive(tfd, stream, size, digest):
    """Copy exactly `size` bytes from `stream` to `tfd`, or Refusal."""
    hashed = hashlib.sha256()
    left = size
    while left:
        chunk = stream.read(min(CHUNK, left))
        if not chunk:
            raise Refusal("incomplete", f"the upload ended after {size - left} of {size} bytes")
        view = memoryview(chunk)
        while view:
            view = view[os.write(tfd, view) :]
        hashed.update(chunk)
        left -= len(chunk)
    if stream.read(1):
        raise Refusal("incomplete", f"more than the {size} bytes declared arrived")
    if hashed.hexdigest() != digest:
        raise Refusal("incomplete", "the bytes that arrived do not match the sha256 sent")


def _publish_new(fd, tmp, name):
    """Publish `tmp` as `name` only if no `name` exists - atomically, with
    link(2), which fails when it does. A filesystem without hard links
    falls back to a check and a rename, which a writer racing it could beat."""
    try:
        os.link(tmp, name, src_dir_fd=fd, dst_dir_fd=fd)
    except FileExistsError:
        raise Refusal("exists", f"{name!r} exists - replacing it must be asked for") from None
    except OSError as exc:
        if exc.errno not in (errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EXDEV):
            raise
        _existing(fd, name, overwrite=False)
        os.rename(tmp, name, src_dir_fd=fd, dst_dir_fd=fd)
        return
    os.unlink(tmp, dir_fd=fd)


def _interrupted(signum, _frame):
    raise Refusal("incomplete", f"interrupted by signal {signum} - nothing was published")


def sweep(fd, now=None):
    """Remove temp files older than STALE_TEMP from directory `fd`; how many."""
    cutoff = (time.time() if now is None else now) - STALE_TEMP
    swept = 0
    with os.scandir(fd) as it:
        names = [e.name for e in it if e.name.startswith(TEMP_PREFIX) and e.name.endswith(".tmp")]
    for name in names:
        try:
            st = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISREG(st.st_mode) and st.st_mtime < cutoff:
                os.unlink(name, dir_fd=fd)
                swept += 1
        except OSError:
            pass
    return swept


def publish(fd, name, stream, size, digest, overwrite):
    """Write `name` in directory `fd` from `stream`: a temp file, then one
    atomic step. Returns (replaced, mode)."""
    existing = _existing(fd, name, overwrite)
    tmp = TEMP_PREFIX + os.urandom(8).hex() + ".tmp"
    flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
             | getattr(os, "O_CLOEXEC", 0))  # fmt: skip
    try:
        tfd = os.open(tmp, flags, 0o600, dir_fd=fd)
    except PermissionError:
        raise Refusal("denied", "the destination directory cannot be written") from None
    published = False
    try:
        try:
            _receive(tfd, stream, size, digest)
            mode = stat.S_IMODE(existing.st_mode) & 0o777 if existing else _new_mode()
            os.fchmod(tfd, mode)
            os.fsync(tfd)
        finally:
            os.close(tfd)
        if overwrite:
            if existing is None:
                _existing(fd, name, overwrite)  # what is there now may not be a file
            os.replace(tmp, name, src_dir_fd=fd, dst_dir_fd=fd)
        else:
            _publish_new(fd, tmp, name)
        published = True
    finally:
        if not published:
            try:
                os.unlink(tmp, dir_fd=fd)
            except FileNotFoundError:
                pass
    try:
        os.fsync(fd)  # the directory entry, where the platform allows it
    except OSError:
        pass
    return existing is not None, mode


def upload(request, stream):
    """Answer an `upload` request, the file's bytes read from `stream`."""
    if stream is None:
        raise Refusal("bad_request", "an upload's bytes must follow the helper on stdin")
    root = request.get("root")
    parts = upload_parts(request.get("path"))
    size = request.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise Refusal("bad_request", "size must be a non-negative integer")
    if size > MAX_UPLOAD:
        raise Refusal("too_large", f"a file may be at most {MAX_UPLOAD} bytes")
    digest = request.get("sha256")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise Refusal("bad_request", "sha256 must be 64 lower-case hex digits")
    overwrite = request.get("overwrite", False)
    if not isinstance(overwrite, bool):
        raise Refusal("bad_request", "overwrite must be true or false")
    fd, created = make_dirs(root, parts[:-1])
    previous = {}
    try:
        swept = sweep(fd)
        for signum in (signal.SIGHUP, signal.SIGTERM):
            try:
                previous[signum] = signal.signal(signum, _interrupted)
            except ValueError:
                pass  # not the main thread: only in-process callers, never ssh
        replaced, mode = publish(fd, parts[-1], stream, size, digest, overwrite)
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        os.close(fd)
    return {
        "protocol": PROTOCOL,
        "ok": True,
        "path": "/".join(parts),
        "size": size,
        "sha256": digest,
        "replaced": replaced,
        "mode": mode,
        "created": created,
        "swept": swept,
    }


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


def handle(request, stream=None):
    """Answer one request object; Refusal becomes an ok:false answer.
    `stream` carries an upload's bytes, and nothing else reads it."""
    try:
        if not isinstance(request, dict):
            raise Refusal("bad_request", "the request is not a JSON object")
        if request.get("protocol") != PROTOCOL:
            raise Refusal("bad_request", f"protocol {request.get('protocol')!r} is not {PROTOCOL}")
        if request.get("op") == "delete":
            return delete_paths(request.get("root"), request.get("paths"), request.get("confirmed"))
        if request.get("op") == "upload":
            return upload(request, stream)
        return _list(request)
    except Refusal as exc:
        return {"protocol": PROTOCOL, "ok": False, "error": exc.error, "detail": exc.detail}


def _list(request):
    """Answer a `list` request."""
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


def main(argv, stream=None):
    """Run one request: `argv[1]`, and for an upload the bytes on `stream`
    (stdin, past this source - see FRAMING)."""
    if len(argv) != 2:
        answer = {"protocol": PROTOCOL, "ok": False, "error": "bad_request",
                  "detail": "expected one argument, the encoded request"}  # fmt: skip
    else:
        try:
            request = json.loads(base64.b64decode(argv[1], validate=True).decode("utf-8"))
        except ValueError:
            request = None
        answer = handle(request, stream)
    sys.stdout.write(json.dumps(answer) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
