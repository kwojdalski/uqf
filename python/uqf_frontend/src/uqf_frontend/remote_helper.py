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
"replaced", "mode", "created_dirs", "swept"}`.

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
    including a hangup or SIGTERM (ssh dropped or timed out) while the bytes
    arrive, which are turned into a refusal so the cleanup runs. From the
    publishing step on they are ignored (the `keep` hook): the file may be
    live by then, so the upload finishes and says so rather than claim
    nothing was published (#2022). Only SIGKILL leaves a temp file; the next
    upload to that directory sweeps temp files older than STALE_TEMP.

PUSH HISTORY (#2002). Every upload belongs to a push: a file selection, a
folder or a drop, opened with `push_open` when the operator confirms it and
closed with `push_close` when the last file is sent. The push keeps what it
changed on the server, and only there, in

    shared/config/.uqf-history/<push-id>/
        manifest.json         MANIFEST_VERSION: who, when, where, and per file
                              its action, sizes, sha256s, modes and state
        before/<path>         a replaced or deleted file, as it was
        after/<path>          the file as the push wrote it

where <path> is the file's path below shared/config/. The history is created
0o700 and walked like everything else - one confined, no-follow step at a
time - and no browse, delete or upload path may name it (split_path refuses
it, in any letter case). An upload copies the file it replaces to `before/`
and the new bytes to `after/`, records the file `pending`, publishes, then
records it `published`: a helper killed in between leaves `pending`, and
`history_show` tells what landed by hashing the file as it is now.
`restore` writes a push's `before/` files back - deleting the files it
created, regular files only, never following a link (#1953's rules) - as a
push of its own, so a restore can itself be restored. A file changed since
the push is a conflict: the restore is refused and nothing written. The
history root's `.lock` (WRITE_LOCK) makes that check hold at the write: an
upload holds it for its one publishing step, and a restore for its check
and every write after it, so a push that lands between a restore's first
look and its writes is seen and refused, never overwritten (#2181).
`history_diff` diffs before and after as text under DIFF_CAP; a binary or
larger file is told by its sizes and sha256s only. Opening a push prunes the
history to its bound - the newest `max_kept` pushes, none older than
`retention_days` (the tighter wins) - and logs each pruned id in PRUNED_LOG, so a
restore of one is refused as pruned, not as never having existed.

FRAMING. `list` is sent as before: the helper's source on stdin to
`python -I -`. An upload cannot be, because `-` reads stdin to its end as
source. So the API runs `python -I -c BOOTSTRAP <request>`, where BOOTSTRAP
(remote_files.BOOTSTRAP, fixed text) reads one decimal line N, then N bytes
of this source, which it runs, then calls `main(argv, stdin)`; the rest of
stdin, exactly `size` bytes, is the file. No path is in either.
"""

import base64
import calendar
import difflib
import errno
import fcntl
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
    "ssh",
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
    # push history (#2002): a push whose history retention removed, and a
    # restore refused because a file changed since the push it would undo
    "pruned",
    "conflict",
)
#: Largest file one upload carries, in bytes. The API refuses a larger body
#: before ssh, and the browser before sending (web/src/api/).
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

#: The push history's directory below UPLOAD_ROOT (PUSH HISTORY).
HISTORY_DIR = ".uqf-history"
HISTORY_PARTS = (*UPLOAD_ROOT, HISTORY_DIR)
#: A push id: when it was opened, UTC, to the millisecond, then 8 random hex
#: digits - so names sort in time order.
PUSH_ID = re.compile(r"[0-9]{8}T[0-9]{9}Z-[0-9a-f]{8}")
MANIFEST = "manifest.json"
#: The lock file, in a push's directory and in the history root (WRITE_LOCK).
LOCK = ".lock"
MANIFEST_VERSION = 1
#: Largest manifest read, in bytes.
MAX_MANIFEST = 4 * 1024 * 1024
#: Most files one push records - the browser's own queue bound (web/src/api/).
MAX_PUSH_FILES = 500
#: The pruned pushes, one JSON object a line, at most PRUNED_LOG_MAX of them.
PRUNED_LOG = "pruned.jsonl"
PRUNED_LOG_MAX = 1000
#: The retention bound when the caller names none, and the most it may name.
DEFAULT_KEEP = 50
DEFAULT_KEEP_DAYS = 90
MAX_KEEP = 10000
MAX_KEEP_DAYS = 3650
#: Largest file `history_diff` diffs as text, and the most text one answer
#: carries; past either a file is told by its sizes and sha256s.
DIFF_CAP = 256 * 1024
DIFF_ANSWER_CAP = 2 * 1024 * 1024
#: Most pushes `history_list` describes.
MAX_HISTORY_LIST = 500
#: Who pushed - the claimed user - and through what: at most this long.
MAX_LABEL = 128


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
    # in any letter case: a case-insensitive filesystem would open it all the same
    if tuple(p.casefold() for p in parts[: len(HISTORY_PARTS)]) == HISTORY_PARTS:
        raise Refusal(
            "refused",
            f"{'/'.join(HISTORY_PARTS)} is the config push history - read it with the "
            "History view or `uqs target config history`, never through the file browser",
        )
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


def _mtime(ns):
    """A file's st_mtime_ns as the API sends every instant: ISO-8601 in UTC,
    to the nanosecond, with its `Z` - not the float of epoch seconds it was,
    the one instant in the API that was not text (#2104)."""
    seconds, fraction = divmod(ns, 10**9)
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(seconds)) + f".{fraction:09d}Z"


def describe(fd, name):
    """One listing entry for `name` in directory `fd`, never following it."""
    shown, bad_name = _readable_name(name)
    try:
        st = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except OSError as exc:
        return {"name": shown, "kind": "unknown", "size": None, "mtime": None,
                "blocked": True, "reason": f"cannot be read: {exc.strerror}"}  # fmt: skip
    mode = st.st_mode
    entry = {"name": shown, "size": None, "mtime": _mtime(st.st_mtime_ns), "blocked": False,
             "reason": None}  # fmt: skip
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
    """(entries, total or None when the scan stopped early, truncated).

    Directories first, then by name - what an operator scans for - and in
    that order BEFORE the cut, so `limit` keeps the first entries of the
    order shown rather than dropping a directory whose name sorts late
    (#2478). Whether an entry is a directory is asked without following it,
    as `describe` asks."""
    names = []
    complete = True
    try:
        with os.scandir(fd) as it:
            for entry in it:
                if len(names) >= SCAN_CAP:
                    complete = False
                    break
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    is_dir = False
                names.append((not is_dir, entry.name))
    except PermissionError:
        raise Refusal("denied", "the directory cannot be listed") from None
    names.sort()
    shown = [describe(fd, n) for _, n in names[:limit]]
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
    if "host" in data and "ssh" not in data:
        # a report written before #2123 named the ssh destination `host`
        data["ssh"] = data.pop("host")
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
    try:
        return make_dirs_at(fd, parts)
    finally:
        os.close(fd)


def make_dirs_at(fd, parts, mode=0o777):
    """make_dirs below an open directory `fd`, which stays open; the paths
    created are relative to it."""
    fd = os.dup(fd)
    created = []
    try:
        for i, part in enumerate(parts):
            try:
                nxt = _open_dir(fd, part)
            except Refusal as exc:
                if exc.error != "not_found":
                    raise
                try:
                    os.mkdir(part, mode, dir_fd=fd)
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


def _publish_step(fd, tmp, name, existing, mode, overwrite, keep):
    """publish's one step: `keep`, then `tmp` becomes `name`."""
    if keep is not None:
        keep(fd, tmp, existing, mode)
    if overwrite:
        if existing is None:
            _existing(fd, name, overwrite)  # what is there now may not be a file
        os.replace(tmp, name, src_dir_fd=fd, dst_dir_fd=fd)
    else:
        _publish_new(fd, tmp, name)


def publish(fd, name, stream, size, digest, overwrite, *, mode=None, keep=None, lock=None):
    """Write `name` in directory `fd` from `stream`: a temp file, then one
    atomic step. Returns (replaced, mode). `mode` sets the permission bits
    (else a replaced file's are kept and a new file gets _new_mode); `keep`,
    called with (fd, temp name, existing stat or None, mode) once the bytes
    are verified and before the one publishing step, records the push's
    history - a refusal from it publishes nothing. `lock`, when given, is
    made and held around `keep` and the publishing step (WRITE_LOCK)."""
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
            if mode is None:
                mode = stat.S_IMODE(existing.st_mode) & 0o777 if existing else _new_mode()
            os.fchmod(tfd, mode)
            os.fsync(tfd)
        finally:
            os.close(tfd)
        if lock is None:
            _publish_step(fd, tmp, name, existing, mode, overwrite, keep)
        else:
            with lock():
                _publish_step(fd, tmp, name, existing, mode, overwrite, keep)
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


def _held_signals(handler):
    """Install `handler` for a hangup and SIGTERM; the handlers it replaced."""
    previous = {}
    for signum in (signal.SIGHUP, signal.SIGTERM):
        try:
            previous[signum] = signal.signal(signum, handler)
        except ValueError:
            pass  # not the main thread: only in-process callers, never ssh
    return previous


def _restore_signals(previous):
    for signum, handler in previous.items():
        signal.signal(signum, handler)


def upload(request, stream):
    """Answer an `upload` request, the file's bytes read from `stream`, as
    one file of the push `push_id` names (PUSH HISTORY)."""
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
    push_id = _push_id(request.get("push_id"))
    path = "/".join(parts)
    pfd = _push_dir(root, push_id)
    previous = {}
    try:
        with _Locked(pfd):
            manifest = _read_manifest(pfd)
            if manifest.get("closed"):
                raise Refusal("refused", f"push {push_id} is closed - open another to upload")
            files = [
                e for e in manifest["files"] if not (e["path"] == path and e["state"] == "failed")
            ]
            if any(e["path"] == path for e in files):
                raise Refusal("exists", f"{path!r} is already part of push {push_id}")
            if len(files) >= MAX_PUSH_FILES:
                raise Refusal("refused", f"a push records at most {MAX_PUSH_FILES} files")
            manifest["files"] = files
            entry = {"path": path, "action": "replace" if overwrite else "create",
                     "size": size, "sha256": digest, "mode": None, "previous_size": None,
                     "previous_sha256": None, "previous_mode": None, "created_dirs": [],
                     "state": "pending", "error": None, "at": _utc_now()}  # fmt: skip
            fd, created = make_dirs(root, parts[:-1])
            entry["created_dirs"] = created
            try:
                swept = sweep(fd)
                previous = _held_signals(_interrupted)
                keep = _keeper(pfd, manifest, entry, parts[len(UPLOAD_ROOT) :])
                try:
                    replaced, mode = publish(fd, parts[-1], stream, size, digest, overwrite,
                                             keep=keep, lock=lambda: _WriteLock(root))  # fmt: skip
                except Refusal as exc:
                    _forget(pfd, manifest, entry, parts[len(UPLOAD_ROOT) :], exc.error)
                    raise
                entry.update(state="published", action="replace" if replaced else "create")
                _write_manifest(pfd, manifest)
            finally:
                _restore_signals(previous)
                os.close(fd)
    finally:
        os.close(pfd)
    return {
        "protocol": PROTOCOL,
        "ok": True,
        "path": path,
        "size": size,
        "sha256": digest,
        "replaced": replaced,
        "mode": mode,
        "created_dirs": created,
        "swept": swept,
        "push_id": push_id,
        "previous_size": entry["previous_size"],
        "previous_sha256": entry["previous_sha256"],
    }


# ------------------------------------------------------------ push history (#2002)


def _utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _push_id(value):
    if not isinstance(value, str) or not PUSH_ID.fullmatch(value):
        raise Refusal("bad_request", "a push id is YYYYMMDDTHHMMSSmmmZ- and 8 hex digits")
    return value


def _push_time(push_id):
    """When a push was opened, in epoch seconds, from its id."""
    return calendar.timegm(time.strptime(push_id[:15], "%Y%m%dT%H%M%S"))


def _label(value, what):
    """A claimed user, a target name or a channel: short text, or None."""
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > MAX_LABEL or not value.isprintable():
        raise Refusal("bad_request", f"{what} must be printable text of at most {MAX_LABEL}")
    return value


def _count(request, key, default=None, most=None):
    value = request.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise Refusal("bad_request", f"{key} must be a non-negative integer")
    if most is not None and value > most:
        raise Refusal("bad_request", f"{key} may be at most {most}")
    return value


class _Locked:
    """An exclusive lock on one push's manifest, for as long as the block."""

    def __init__(self, pfd):
        flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        self.fd = os.open(LOCK, flags, 0o600, dir_fd=pfd)

    def __enter__(self):
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        os.close(self.fd)


class _WriteLock(_Locked):
    """WRITE_LOCK: the history root's lock, which every write to a config file
    a push records takes - an upload around its one publishing step, a
    restore around its check and all its writes - so what a restore checked
    is what it overwrites (#2181). Always taken inside a push's own lock,
    never around one, so the two cannot deadlock."""

    def __init__(self, root):
        hfd = _history_root(root)
        try:
            super().__init__(hfd)
        finally:
            os.close(hfd)


def _history_root(root, create=False):
    """A descriptor for shared/config/.uqf-history, created 0o700 if asked."""
    if not create:
        return walk(root, list(HISTORY_PARTS))
    fd, _ = make_dirs(root, list(UPLOAD_ROOT))
    try:
        hfd, _ = make_dirs_at(fd, [HISTORY_DIR], 0o700)
    finally:
        os.close(fd)
    return hfd


#: PRUNED_LOG's reasons before #2111, each with the word it is read as now.
#: A server's log outlives the release that wrote it, so a line in the old
#: words is read in the new ones - never refused, never served as written.
PRUNED_WHY_RENAMED = {"count": "row_cap", "age": "retention"}


def _pruned_records(hfd):
    """The pruned pushes, oldest first, each `why` in today's words."""
    try:
        data, _ = _read_bytes(hfd, PRUNED_LOG, MAX_MANIFEST)
    except Refusal:
        return []
    out = []
    for line in data.decode("utf-8", "replace").splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and isinstance(record.get("push_id"), str):
            why = record.get("why")
            if isinstance(why, str):
                record["why"] = PRUNED_WHY_RENAMED.get(why, why)
            out.append(record)
    return out


def _push_dir(root, push_id):
    """A descriptor for one push's history, or why there is none: `pruned`
    when retention removed it, `not_found` when it never existed."""
    try:
        hfd = _history_root(root)
    except Refusal as exc:
        if exc.error == "not_found":
            raise Refusal(
                "not_found", f"no push {push_id} - this target has no push history"
            ) from None
        raise
    try:
        return _open_dir(hfd, push_id)
    except Refusal as exc:
        if exc.error != "not_found":
            raise
        gone = [r for r in _pruned_records(hfd) if r["push_id"] == push_id]
        if gone:
            raise Refusal(
                "pruned",
                f"push {push_id}'s history was pruned at {gone[-1].get('pruned_at')} "
                f"({gone[-1].get('why')} bound) - it can no longer be shown, diffed or restored",
            ) from None
        raise Refusal("not_found", f"no push {push_id} in this target's history") from None
    finally:
        os.close(hfd)


def _open_file(fd, name, expect=None):
    """A read descriptor for regular file `name` in `fd`, never following a
    link, checked to be the file lstat (and `expect`, when given) saw."""
    try:
        before = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        raise Refusal("not_found", f"{name!r} does not exist") from None
    if not stat.S_ISREG(before.st_mode):
        raise Refusal("refused", f"{name!r} is not a regular file")
    flags = (os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
             | getattr(os, "O_CLOEXEC", 0))  # fmt: skip
    try:
        rfd = os.open(name, flags, dir_fd=fd)
    except OSError as exc:
        raise Refusal("refused", f"{name!r} cannot be opened: {exc.strerror}") from None
    after = os.fstat(rfd)
    seen = [(before.st_dev, before.st_ino)]
    if expect is not None:
        seen.append((expect.st_dev, expect.st_ino))
    if any(pair != (after.st_dev, after.st_ino) for pair in seen):
        os.close(rfd)
        raise Refusal("refused", f"{name!r} changed while it was opened")
    return rfd


def _read_bytes(fd, name, cap):
    """(bytes, stat) of regular file `name` in `fd`, at most `cap` of them."""
    rfd = _open_file(fd, name)
    with os.fdopen(rfd, "rb") as fh:
        st = os.fstat(fh.fileno())
        data = fh.read(cap + 1)
    if len(data) > cap:
        raise Refusal("too_large", f"{name!r} is larger than {cap} bytes")
    return data, st


def _hash_file(fd, name, cap=MAX_UPLOAD):
    """(sha256, size, mode) of what `name` in `fd` is now, None when nothing
    is there; Refusal for a link, a directory or a special file."""
    try:
        st = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(st.st_mode):
        raise Refusal("refused", f"{name!r} is not a regular file")
    if st.st_size > cap:
        raise Refusal("too_large", f"{name!r} is larger than {cap} bytes")
    rfd = _open_file(fd, name, st)
    hashed = hashlib.sha256()
    try:
        while chunk := os.read(rfd, CHUNK):
            hashed.update(chunk)
    finally:
        os.close(rfd)
    return hashed.hexdigest(), st.st_size, stat.S_IMODE(st.st_mode) & 0o777


def _current(root, parts):
    """_hash_file of root/parts..., None when it or a directory on the way
    is missing."""
    try:
        fd = walk(root, parts[:-1])
    except Refusal as exc:
        if exc.error == "not_found":
            return None
        raise
    try:
        return _hash_file(fd, parts[-1])
    finally:
        os.close(fd)


def _copy_into(rfd, dfd, name, mode):
    """Copy what `rfd` reads to a new file `name` in `dfd`; (sha256, size)."""
    try:
        os.unlink(name, dir_fd=dfd)  # a copy a killed helper left; unlink follows nothing
    except FileNotFoundError:
        pass
    flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
             | getattr(os, "O_CLOEXEC", 0))  # fmt: skip
    wfd = os.open(name, flags, 0o600, dir_fd=dfd)
    hashed = hashlib.sha256()
    size = 0
    try:
        while chunk := os.read(rfd, CHUNK):
            view = memoryview(chunk)
            while view:
                view = view[os.write(wfd, view) :]
            hashed.update(chunk)
            size += len(chunk)
        os.fchmod(wfd, mode)
        os.fsync(wfd)
    finally:
        os.close(wfd)
    return hashed.hexdigest(), size


def _copy_to_history(pfd, side, rel, rfd, mode):
    """Copy `rfd` to <push>/<side>/<rel>; (sha256, size)."""
    dfd, _ = make_dirs_at(pfd, [side, *rel[:-1]], 0o700)
    try:
        return _copy_into(rfd, dfd, rel[-1], mode)
    finally:
        os.close(dfd)


def _remove_copy(pfd, side, rel):
    """Remove <push>/<side>/<rel>, if it is there."""
    try:
        dfd = walk_at(pfd, [side, *rel[:-1]])
    except Refusal:
        return
    try:
        os.unlink(rel[-1], dir_fd=dfd)
    except FileNotFoundError:
        pass
    finally:
        os.close(dfd)


def walk_at(fd, parts):
    """`walk` below an open directory `fd`, which stays open."""
    fd = os.dup(fd)
    try:
        for part in parts:
            nxt = _open_dir(fd, part)
            os.close(fd)
            fd = nxt
    except BaseException:
        os.close(fd)
        raise
    return fd


def _read_manifest(pfd):
    data, _ = _read_bytes(pfd, MANIFEST, MAX_MANIFEST)
    try:
        manifest = json.loads(data.decode("utf-8"))
    except ValueError:
        raise Refusal("refused", "the push's manifest.json is not valid JSON") from None
    if not isinstance(manifest, dict) or manifest.get("version") != MANIFEST_VERSION:
        raise Refusal("refused", f"the push's manifest.json is not version {MANIFEST_VERSION}")
    if not isinstance(manifest.get("files"), list):
        raise Refusal("refused", "the push's manifest.json has no file list")
    manifest["reported"] = read_reported(manifest)
    # A manifest written before #2107 says `destination`: still read, and
    # written back as `config_dir` the next time this push's manifest is.
    if "destination" in manifest:
        manifest.setdefault("config_dir", manifest.pop("destination"))
    return manifest


def _write_manifest(pfd, manifest):
    """Replace the manifest in one step: a temp file, fsync'd, then rename."""
    data = json.dumps(manifest, indent=1, sort_keys=True).encode("utf-8")
    if len(data) > MAX_MANIFEST:
        raise Refusal("too_large", f"the manifest would be larger than {MAX_MANIFEST} bytes")
    tmp = TEMP_PREFIX + os.urandom(8).hex() + ".tmp"
    flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
             | getattr(os, "O_CLOEXEC", 0))  # fmt: skip
    wfd = os.open(tmp, flags, 0o600, dir_fd=pfd)
    try:
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(wfd, view) :]
            os.fsync(wfd)
        finally:
            os.close(wfd)
        os.replace(tmp, MANIFEST, src_dir_fd=pfd, dst_dir_fd=pfd)
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=pfd)
        except FileNotFoundError:
            pass
        raise


def _keeper(pfd, manifest, entry, rel):
    """publish's `keep` for an upload: the file being replaced to before/,
    the verified new bytes to after/, the entry recorded `pending` - then
    hangups and SIGTERM are ignored, so the publishing step and its record
    are made whole."""

    def keep(fd, tmp, existing, mode):
        if existing is not None:
            if existing.st_size > MAX_UPLOAD:
                raise Refusal(
                    "too_large",
                    f"the file being replaced is larger than {MAX_UPLOAD} bytes - its "
                    "content could not be kept in the push history, so it is not replaced",
                )
            rfd = _open_file(fd, rel[-1], existing)
            try:
                old = stat.S_IMODE(existing.st_mode) & 0o777
                digest, size = _copy_to_history(pfd, "before", rel, rfd, old)
            finally:
                os.close(rfd)
            entry.update(previous_sha256=digest, previous_size=size, previous_mode=old)
        tfd = _open_file(fd, tmp)
        try:
            _copy_to_history(pfd, "after", rel, tfd, mode)
        finally:
            os.close(tfd)
        entry.update(mode=mode, action="replace" if existing is not None else "create")
        manifest["files"].append(entry)
        _write_manifest(pfd, manifest)
        _held_signals(signal.SIG_IGN)

    return keep


def _forget(pfd, manifest, entry, rel, error):
    """Record a file that was not published, keeping none of its bytes."""
    _remove_copy(pfd, "before", rel)
    _remove_copy(pfd, "after", rel)
    entry.update(state="failed", error=error)
    if entry not in manifest["files"]:
        manifest["files"].append(entry)
    _write_manifest(pfd, manifest)


def _rmtree(dfd, name):
    """Remove `name` in `dfd` and everything below it, never following a link."""
    st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
    if not stat.S_ISDIR(st.st_mode):
        os.unlink(name, dir_fd=dfd)
        return
    sub = _open_dir(dfd, name)
    try:
        with os.scandir(sub) as it:
            children = [e.name for e in it]
        for child in children:
            _rmtree(sub, child)
    finally:
        os.close(sub)
    os.rmdir(name, dir_fd=dfd)


def _bounds(request):
    max_kept = _count(request, "max_kept", DEFAULT_KEEP, MAX_KEEP)
    days = _count(request, "retention_days", DEFAULT_KEEP_DAYS, MAX_KEEP_DAYS)
    if max_kept < 1 or days < 1:
        raise Refusal("bad_request", "max_kept and retention_days must be at least 1")
    return max_kept, days


def prune(hfd, max_kept, retention_days, protect=(), now=None):
    """Remove the pushes past the bound - beyond the newest `max_kept`, or opened
    more than `retention_days` ago - but never one in `protect`; log each in
    PRUNED_LOG and return them."""
    now = time.time() if now is None else now
    with os.scandir(hfd) as it:
        names = sorted((e.name for e in it if PUSH_ID.fullmatch(e.name)), reverse=True)
    cutoff = now - retention_days * 86400
    gone = []
    for i, name in enumerate(names):
        if name in protect:
            continue
        # The activity history's words for the same two bounds (#2111);
        # _pruned_records reads a line written before in the old ones.
        why = "row_cap" if i >= max_kept else None
        if why is None and _push_time(name) < cutoff:
            why = "retention"
        if why is None:
            continue
        _rmtree(hfd, name)
        gone.append({"push_id": name, "why": why, "pruned_at": _utc_now()})
    if gone:
        records = (_pruned_records(hfd) + gone)[-PRUNED_LOG_MAX:]
        text = "".join(json.dumps(r, sort_keys=True) + "\n" for r in records).encode("utf-8")
        tmp = TEMP_PREFIX + os.urandom(8).hex() + ".tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        wfd = os.open(tmp, flags, 0o600, dir_fd=hfd)
        try:
            os.write(wfd, text)
            os.fsync(wfd)
        finally:
            os.close(wfd)
        os.replace(tmp, PRUNED_LOG, src_dir_fd=hfd, dst_dir_fd=hfd)
    return gone


def _new_push(root, push_id, fields, max_kept, retention_days, protect=()):
    """Create <history>/<push_id> with its manifest, then prune; the pruned."""
    hfd = _history_root(root, create=True)
    try:
        try:
            os.mkdir(push_id, 0o700, dir_fd=hfd)
        except FileExistsError:
            raise Refusal("exists", f"push {push_id} exists already") from None
        pfd = _open_dir(hfd, push_id)
        try:
            manifest = {"version": MANIFEST_VERSION, "push_id": push_id, "created": _utc_now(),
                        "closed": None, "reported": [], "files": [], **fields}  # fmt: skip
            _write_manifest(pfd, manifest)
        finally:
            os.close(pfd)
        return prune(hfd, max_kept, retention_days, protect=(push_id, *protect))
    finally:
        os.close(hfd)


def push_open(request):
    """Open a push: its history directory and manifest, then retention."""
    push_id = _push_id(request.get("push_id"))
    parts = split_path(request.get("config_dir"))
    if tuple(parts[: len(UPLOAD_ROOT)]) != UPLOAD_ROOT:
        raise Refusal("unsupported", f"a push goes to {'/'.join(UPLOAD_ROOT)}/ or below it")
    max_kept, retention_days = _bounds(request)
    fields = {
        "kind": "push",
        "restores": None,
        "config_dir": "/".join(parts),
        "declared": {
            "file_count": _count(request, "file_count", 0, MAX_PUSH_FILES),
            "total_bytes": _count(request, "total_bytes", 0),
        },
        "user": _label(request.get("user"), "user"),
        "target": _label(request.get("target"), "target"),
        "via": _label(request.get("via"), "via"),
    }
    pruned = _new_push(request.get("root"), push_id, fields, max_kept, retention_days)
    return {"protocol": PROTOCOL, "ok": True, "push_id": push_id, "pruned": pruned}


#: The outcomes a browser may report for a file the server never recorded:
#: models.FileOutcome's words, less `succeeded`, which it cannot know (#2108).
REPORTED = ("unknown", "cancelled", "failed", "refused", "invalid")
#: A reported outcome as a manifest written before #2108 stores it. Manifests
#: already on servers keep it; they are read in the new word, never written.
_STORED_OUTCOME = {"uncertain": "unknown"}


def read_reported(manifest):
    """The manifest's reported files, each outcome in the current word."""
    return [{**r, "outcome": _STORED_OUTCOME.get(r.get("outcome"), r.get("outcome"))}
            for r in manifest.get("reported") or []]  # fmt: skip


def push_close(request):
    """Close a push: no more uploads, and what the browser saw of files the
    server never recorded. The manifest, as closed."""
    push_id = _push_id(request.get("push_id"))
    items = request.get("reported", [])
    if not isinstance(items, list) or len(items) > MAX_PUSH_FILES:
        raise Refusal("bad_request", f"reported must be a list of at most {MAX_PUSH_FILES}")
    reported = []
    for item in items:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("path"), str)
            or len(item["path"]) > MAX_PATH
            or item.get("outcome") not in REPORTED
        ):
            raise Refusal("bad_request", f"each reported item is a path and one of {REPORTED}")
        reported.append({"path": item["path"], "outcome": item["outcome"]})
    pfd = _push_dir(request.get("root"), push_id)
    try:
        with _Locked(pfd):
            manifest = _read_manifest(pfd)
            if manifest.get("closed"):
                raise Refusal("exists", f"push {push_id} is closed already")
            recorded = {e["path"] for e in manifest["files"]}
            manifest["reported"] = [r for r in reported if r["path"] not in recorded]
            manifest["closed"] = _utc_now()
            _write_manifest(pfd, manifest)
    finally:
        os.close(pfd)
    return {"protocol": PROTOCOL, "ok": True, "manifest": manifest}


def summarise(manifest):
    files = manifest["files"]
    states = {
        s: sum(1 for e in files if e.get("state") == s) for s in ("published", "pending", "failed")
    }
    return {
        **{k: manifest.get(k) for k in ("push_id", "kind", "restores", "created", "closed",
                                         "user", "target", "via", "config_dir", "declared")},
        "file_count": len(files),
        "total_bytes": sum(e.get("size") or 0 for e in files if e.get("state") != "failed"),
        "states": states,
        "reported_count": len(manifest.get("reported") or []),
    }  # fmt: skip


def history_list(request):
    """The newest pushes, each summarised, and the latest pruned."""
    limit = _count(request, "limit", MAX_HISTORY_LIST, MAX_HISTORY_LIST) or MAX_HISTORY_LIST
    try:
        hfd = _history_root(request.get("root"))
    except Refusal as exc:
        if exc.error != "not_found":
            raise
        return {"protocol": PROTOCOL, "ok": True, "pushes": [], "total": 0,
                "truncated": False, "pruned": []}  # fmt: skip
    try:
        with os.scandir(hfd) as it:
            names = sorted((e.name for e in it if PUSH_ID.fullmatch(e.name)), reverse=True)
        pushes = []
        for name in names[:limit]:
            try:
                pfd = _open_dir(hfd, name)
                try:
                    pushes.append({**summarise(_read_manifest(pfd)), "error": None})
                finally:
                    os.close(pfd)
            except Refusal as exc:
                pushes.append({"push_id": name, "error": exc.detail})
        pruned = _pruned_records(hfd)[-20:][::-1]
    finally:
        os.close(hfd)
    return {"protocol": PROTOCOL, "ok": True, "pushes": pushes, "total": len(names),
            "truncated": len(names) > limit, "pruned": pruned}  # fmt: skip


def _now_state(root, entry):
    """What the file is now against what the push did: `after` (as the push
    left it), `before` (as it was before), `changed`, or `blocked`."""
    after = entry.get("sha256") if entry.get("action") != "delete" else None
    before = entry.get("previous_sha256")
    try:
        found = _current(root, upload_parts(entry["path"]))
    except Refusal as exc:
        return "blocked", None, exc.detail
    now = found[0] if found else None
    if now == after:
        return "after", now, None
    if now == before:
        return "before", now, None
    return "changed", now, None


def history_show(request):
    """One push's manifest, each file with what it is now."""
    root = request.get("root")
    push_id = _push_id(request.get("push_id"))
    pfd = _push_dir(root, push_id)
    try:
        manifest = _read_manifest(pfd)  # replaced in one step: no lock to read it
    finally:
        os.close(pfd)
    for entry in manifest["files"]:
        if entry.get("state") == "failed":
            entry["now"], entry["now_sha256"], entry["now_error"] = None, None, None
        else:
            entry["now"], entry["now_sha256"], entry["now_error"] = _now_state(root, entry)
    return {"protocol": PROTOCOL, "ok": True, "summary": summarise(manifest), "manifest": manifest}


def _is_text(data):
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _side(pfd, side, rel):
    """(bytes or None, why not): one side of a file's history, up to DIFF_CAP."""
    try:
        dfd = walk_at(pfd, [side, *rel[:-1]])
    except Refusal:
        return None, "missing"
    try:
        data, _ = _read_bytes(dfd, rel[-1], DIFF_CAP)
    except Refusal as exc:
        return None, "oversized" if exc.error == "too_large" else "missing"
    finally:
        os.close(dfd)
    return data, None


def _unified(path, old, new):
    out = []
    lines = difflib.unified_diff(
        old.decode("utf-8").splitlines(keepends=True),
        new.decode("utf-8").splitlines(keepends=True),
        fromfile=f"a/{path}", tofile=f"b/{path}",
    )  # fmt: skip
    for line in lines:
        out.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    return "".join(out)


def history_diff(request):
    """Before against after for one push's files - or the one `path` names:
    text under DIFF_CAP, sizes and sha256s only for a binary or larger file."""
    push_id = _push_id(request.get("push_id"))
    only = request.get("path")
    if only is not None:
        upload_parts(only)
    pfd = _push_dir(request.get("root"), push_id)
    files = []
    budget = DIFF_ANSWER_CAP
    try:
        manifest = _read_manifest(pfd)
        entries = [e for e in manifest["files"] if e.get("state") != "failed"]
        if only is not None:
            entries = [e for e in entries if e["path"] == only]
            if not entries:
                raise Refusal("not_found", f"push {push_id} wrote no file {only!r}")
        for entry in entries:
            rel = upload_parts(entry["path"])[len(UPLOAD_ROOT) :]
            out = {k: entry.get(k) for k in ("path", "action", "previous_size", "size",
                                             "previous_sha256", "sha256")}  # fmt: skip
            out["kind"], out["diff"] = "text", None
            sides = []
            for side, there in (("before", entry.get("previous_sha256")),
                                ("after", entry.get("action") != "delete")):  # fmt: skip
                data, why = _side(pfd, side, rel) if there else (b"", None)
                if why is not None:
                    out["kind"] = why
                    break
                sides.append(data)
            if out["kind"] == "text":
                if not all(_is_text(d) for d in sides):
                    out["kind"] = "binary"
                else:
                    text = _unified(entry["path"], *sides)
                    if len(text) > budget:
                        out["kind"] = "omitted"
                    else:
                        budget -= len(text)
                        out["diff"] = text
            files.append(out)
    finally:
        os.close(pfd)
    return {"protocol": PROTOCOL, "ok": True, "push_id": push_id, "diff_cap": DIFF_CAP,
            "files": files}  # fmt: skip


def _plan(root, manifest):
    """Each recorded file of a push, and what restoring it takes: `write` its
    before content back, `delete` the file it created, nothing (`unchanged`,
    already as it was), or `conflict` - changed since the push."""
    plan = []
    for entry in manifest["files"]:
        if entry.get("state") == "failed":
            continue
        now, now_sha, why = _now_state(root, entry)
        if now == "before":
            step = "unchanged"
        elif now == "after":
            step = "write" if entry.get("previous_sha256") else "delete"
        else:
            step = "conflict"
        plan.append({"path": entry["path"], "step": step, "now_sha256": now_sha,
                     "restore_sha256": entry.get("previous_sha256"), "error": why})  # fmt: skip
    return plan


def _restore_one(root, spfd, npfd, manifest, source, step):
    """Carry out one step of a restore as an entry of push `manifest`."""
    parts = upload_parts(source["path"])
    rel = parts[len(UPLOAD_ROOT) :]
    entry = {"path": source["path"], "action": "replace", "size": None, "sha256": None,
             "mode": None, "previous_size": None, "previous_sha256": None,
             "previous_mode": None, "created_dirs": [], "state": "pending", "error": None,
             "at": _utc_now()}  # fmt: skip
    if step == "write":
        fd, created = make_dirs(root, parts[:-1])
        entry["created_dirs"] = created
        try:
            bfd = walk_at(spfd, ["before", *rel[:-1]])
            try:
                rfd = _open_file(bfd, rel[-1])
            finally:
                os.close(bfd)
            with os.fdopen(rfd, "rb") as stream:
                size = source["previous_size"]
                digest = source["previous_sha256"]
                entry.update(size=size, sha256=digest)
                keep = _keeper(npfd, manifest, entry, rel)
                try:
                    replaced, _ = publish(fd, parts[-1], stream, size, digest, True,
                                          mode=source.get("previous_mode"), keep=keep)  # fmt: skip
                except Refusal as exc:
                    _forget(npfd, manifest, entry, rel, exc.error)
                    raise
            entry.update(state="published", action="replace" if replaced else "create")
            _write_manifest(npfd, manifest)
        finally:
            os.close(fd)
        return
    fd = walk(root, parts[:-1])
    try:
        found = _hash_file(fd, parts[-1])
        if found is None or found[0] != source.get("sha256"):
            raise Refusal("conflict", f"{source['path']!r} changed during the restore")
        rfd = _open_file(fd, parts[-1])
        try:
            digest, size = _copy_to_history(npfd, "before", rel, rfd, found[2])
        finally:
            os.close(rfd)
        entry.update(action="delete", previous_sha256=digest, previous_size=size,
                     previous_mode=found[2])  # fmt: skip
        manifest["files"].append(entry)
        _write_manifest(npfd, manifest)
        os.unlink(parts[-1], dir_fd=fd)  # a regular file: _hash_file refused anything else
    finally:
        os.close(fd)
    entry["state"] = "published"
    _write_manifest(npfd, manifest)
    # the directories the push created, deepest first, while empty
    for made in sorted(source.get("created_dirs") or [], key=len, reverse=True):
        try:
            made_parts = upload_parts(made + "/x")[:-1]
            if len(made_parts) <= len(UPLOAD_ROOT):
                continue  # strictly below shared/config, never it or above
            dfd = walk(root, made_parts[:-1])
        except Refusal:
            continue
        try:
            os.rmdir(made_parts[-1], dir_fd=dfd)
        except OSError:
            pass  # not empty, or gone: left as it is
        finally:
            os.close(dfd)


def _restore_steps(plan, source_id):
    """The write and delete steps of a restore `plan`, or Refusal: `conflict`
    when any file changed since push `source_id`, `refused` when none needs
    restoring."""
    conflicts = [p["path"] for p in plan if p["step"] == "conflict"]
    if conflicts:
        shown = ", ".join(json.dumps(p) for p in conflicts[:10])
        raise Refusal(
            "conflict",
            f"{len(conflicts)} file(s) changed since push {source_id}: {shown} - the "
            "restore would overwrite those changes, so nothing was written",
        )
    steps = [p for p in plan if p["step"] in ("write", "delete")]
    if not steps:
        raise Refusal("refused", f"nothing to restore - every file of push {source_id} "
                      "is as it was before it")  # fmt: skip
    return steps


def _drop_push(root, push_id):
    """Remove push `push_id`'s history: a restore refused before it wrote."""
    hfd = _history_root(root)
    try:
        _rmtree(hfd, push_id)
    finally:
        os.close(hfd)


def restore(request):
    """Restore push `restores` to its before state, as push `push_id`:
    with `dry_run`, only the plan; else, confirmed, every step, or nothing
    when any file changed since the push."""
    root = request.get("root")
    source_id = _push_id(request.get("restores"))
    dry_run = request.get("dry_run", False)
    if not isinstance(dry_run, bool):
        raise Refusal("bad_request", "dry_run must be true or false")
    if not dry_run and request.get("confirmed") is not True:
        raise Refusal("refused", "explicit confirmation is required")
    spfd = _push_dir(root, source_id)
    try:
        source = _read_manifest(spfd)
        plan = _plan(root, source)
        answer = {"protocol": PROTOCOL, "ok": True, "restores": source_id,
                  "dry_run": dry_run, "plan": plan, "push_id": None, "results": [],
                  "pruned": []}  # fmt: skip
        if dry_run:
            return answer
        steps = _restore_steps(plan, source_id)
        push_id = _push_id(request.get("push_id"))
        max_kept, retention_days = _bounds(request)
        fields = {"kind": "restore", "restores": source_id, "config_dir": "/".join(UPLOAD_ROOT),
                  "declared": {"file_count": len(steps), "total_bytes": 0},
                  "user": _label(request.get("user"), "user"),
                  "target": _label(request.get("target"), "target"),
                  "via": _label(request.get("via"), "via")}  # fmt: skip
        _new_push(root, push_id, fields, MAX_KEEP, MAX_KEEP_DAYS)
        previous = _held_signals(signal.SIG_IGN)  # local disk work: finish it whole
        try:
            npfd = _push_dir(root, push_id)
            try:
                with _Locked(npfd), _WriteLock(root):
                    # The first plan was unlocked: look again, now that no push
                    # can write until this restore is done, and refuse as it
                    # would have (#2181) - leaving no restore push behind.
                    try:
                        plan = answer["plan"] = _plan(root, source)
                        steps = _restore_steps(plan, source_id)
                    except Refusal:
                        _drop_push(root, push_id)
                        raise
                    manifest = _read_manifest(npfd)
                    by_path = {e["path"]: e for e in source["files"] if e.get("state") != "failed"}
                    for step in steps:
                        result = {"path": step["path"], "step": step["step"],
                                  "outcome": "succeeded", "error": None}  # fmt: skip
                        try:
                            _restore_one(root, spfd, npfd, manifest, by_path[step["path"]],
                                         step["step"])  # fmt: skip
                        except (Refusal, OSError) as exc:
                            result["outcome"] = "failed"
                            result["error"] = (
                                exc.detail if isinstance(exc, Refusal) else exc.strerror
                            )
                        answer["results"].append(result)
                    manifest["closed"] = _utc_now()
                    _write_manifest(npfd, manifest)
            finally:
                os.close(npfd)
        finally:
            _restore_signals(previous)
        hfd = _history_root(root)
        try:
            answer["pruned"] = prune(hfd, max_kept, retention_days, protect=(push_id, source_id))
        finally:
            os.close(hfd)
        answer["push_id"] = push_id
        return answer
    finally:
        os.close(spfd)


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
            results.append({"path": path, "outcome": "succeeded", "error": None})
        except (Refusal, OSError) as exc:
            detail = exc.detail if isinstance(exc, Refusal) else exc.strerror
            results.append({"path": path, "outcome": "failed", "error": detail})
    return {"protocol": PROTOCOL, "ok": True, "results": results}


#: The push history's ops (PUSH HISTORY), each answering a request object.
HISTORY_OPS = {
    "push_open": push_open,
    "push_close": push_close,
    "history_list": history_list,
    "history_show": history_show,
    "history_diff": history_diff,
    "restore": restore,
}


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
        op = HISTORY_OPS.get(request.get("op"))
        if op is not None:
            return op(request)
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
    if tuple(parts) == UPLOAD_ROOT:
        for entry in entries:
            if entry["name"].casefold() == HISTORY_DIR:
                entry["blocked"] = True
                entry["reason"] = "config push history - shown by History, not browsed"
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
