"""A config push through the frontend's API, as its Files view makes one (#2786).

The browser's upload panel (web/src/UploadPanel.tsx, web/src/api/config_push.ts
and services.ts) is the model, step for step, so a push made here is the same
in the frontend's activity history (`config.push.open`, one `files.upload` per
file, `config.push`) and in the server's push history (`.uqf-history/<id>/`)
as one made in a browser - `uqs target config history|show|diff|restore` read
it back like any other:

  queue    every file named, a folder's files at their paths below it (the
           folder's own name first, as a folder upload keeps it), held to the
           browser's checks: 16 MiB a file, a path the server would take, no
           path twice - a file that fails one is `invalid` and never sent -
           and 500 files and 256 MiB in all, which refuse the whole queue, as
           the browser refuses a selection that would pass them.
  open     POST /services/{target}/config/pushes, `confirmed: true`, with the
           queued files' count and bytes: the push id every upload names.
  upload   PUT /services/{target}/files?path=&overwrite=&push_id= per file,
           one at a time, the raw bytes, one attempt each. A 2xx with a body
           is `succeeded`; a refusal or a failure the server names as certain
           wrote nothing; UploadOutcomeUncertain, a 504, an answer that is not
           JSON, a lost connection or a Ctrl-C in flight is `unknown` - the
           file may have been written - and is never retried.
  close    POST .../config/pushes/{id}/close, always - after a failure or a
           Ctrl-C too - with what the server never recorded: the files that
           failed, were cancelled or are unknown, as the browser reports
           them, and the invalid ones, which the browser drops from its queue
           before the push and so never reports.

Every request carries the write token (`Authorization: Bearer`), its own
X-UQF-Request-Id and the run's one X-UQF-Session-Id, so `uqs activity list
--session ID` shows the run's rows together, and `x-uqf-user`: the frontend
records it as a CLAIM, as it records every x-uqf-user (uqf_frontend/identity.py).
The server's word is final on every check made here first.

The path rules and the per-file limit are the server's own
(uqf_frontend.remote_helper, imported when a push is built, as
cli/target/target_config.py imports it); the queue's two bounds are the browser's
alone, and test_target_config_push.py holds them equal to services.ts.
"""

from __future__ import annotations

import json
import signal
import threading
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from uqs.paths import UqsError

#: The browser's queue bounds (web/src/api/services.ts UPLOAD_MAX_FILES,
#: UPLOAD_MAX_TOTAL_BYTES): on top of the per-file limit every side holds.
MAX_FILES = 500
MAX_TOTAL_BYTES = 256 * 1024 * 1024
#: Seconds an upload may take: the browser's UPLOAD_TIMEOUT_MS, longer than
#: the server's own 120 s, so its "uncertain" arrives before this gives up.
UPLOAD_TIMEOUT = 150.0
#: Seconds any other request may take: the browser's DEFAULT_REQUEST_TIMEOUT_MS.
REQUEST_TIMEOUT = 45.0
#: The frontend's error type for an upload that may have been written.
UNCERTAIN = "UploadOutcomeUncertain"
#: The env variable holding the write token: the server's own setting.
TOKEN_ENV = "UQF_FRONTEND_WRITE_TOKEN"


@dataclass
class Reply:
    """An HTTP answer: its status and body; `payload` is the body as JSON,
    None when it is not."""

    status: int
    body: bytes

    @property
    def payload(self) -> Any:
        try:
            return json.loads(self.body)
        except ValueError:
            return None


class Lost(Exception):
    """The request got no answer: refused, dropped or timed out."""


#: (method, url, headers, body, timeout) -> Reply, or Lost. urllib by default;
#: a test passes the frontend app itself.
Send = Callable[[str, str, dict[str, str], bytes | None, float], Reply]


def urllib_send(
    method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float
) -> Reply:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:  # noqa: S310 - the operator's --api
            return Reply(answer.status, answer.read())
    except urllib.error.HTTPError as exc:
        return Reply(exc.code, exc.read())
    except (urllib.error.URLError, OSError) as exc:  # TimeoutError and ConnectionError too
        raise Lost(str(getattr(exc, "reason", exc))) from None


@dataclass
class Item:
    """One file of the queue, and what came of it."""

    local: Path
    relative: str  # below the destination folder
    size: int
    path: str = ""  # below the deployment root: config_dir/relative
    #: queued -> succeeded | failed | refused | unknown | cancelled; or invalid
    outcome: str = "queued"
    message: str = ""
    http_status: int | None = None
    error: str | None = None
    answer: dict[str, Any] = field(default_factory=dict)

    def doc(self) -> dict[str, Any]:
        return {
            "path": self.path, "local": str(self.local), "size": self.size,
            "outcome": self.outcome, "message": self.message or None,
            "http_status": self.http_status, "error": self.error,
            "sha256": self.answer.get("sha256"), "replaced": self.answer.get("replaced"),
            "previous_sha256": self.answer.get("previous_sha256"),
            "created_dirs": self.answer.get("created_dirs"),
        }  # fmt: skip


def config_dir(into: str | None) -> str:
    """The folder pushed to, relative to the deployment root: shared/config,
    or `into` below it."""
    from uqf_frontend import remote_helper

    root = "/".join(remote_helper.UPLOAD_ROOT)
    below = (into or "").strip("/")
    return f"{root}/{below}" if below else root


def expand(paths: Iterable[Path]) -> list[tuple[Path, str]]:
    """(file, path below the destination) for every file named and every file
    under every folder named, a folder's files under its own name - as the
    browser's folder upload and drop keep them. Sorted within a folder."""
    picked: list[tuple[Path, str]] = []
    for given in paths:
        if given.is_dir():
            files = sorted(p for p in given.rglob("*") if p.is_file())
            picked += [(p, f"{given.resolve().name}/{p.relative_to(given).as_posix()}")
                       for p in files]  # fmt: skip
        elif given.is_file():
            picked.append((given, given.name))
        else:
            raise UqsError(f"{given}: no such file or folder - nothing was sent")
    return picked


def queue(picked: list[tuple[Path, str]], base: str) -> list[Item]:
    """The queue, each file checked as the browser checks it on adding it
    (UploadPanel's `add`): the whole selection refused past the queue's
    bounds, a file over the limit, already queued or at a path the server
    refuses `invalid`."""
    from uqf_frontend import remote_helper

    items = [Item(local, relative, local.stat().st_size) for local, relative in picked]
    if not items:
        raise UqsError("no files to push - nothing was sent")
    if len(items) > MAX_FILES:
        raise UqsError(f"a push holds at most {MAX_FILES} files; this would make "
                       f"{len(items)}. Nothing was sent.")  # fmt: skip
    total = sum(item.size for item in items)
    if total > MAX_TOTAL_BYTES:
        raise UqsError(f"a push holds at most {MAX_TOTAL_BYTES} bytes; this would make "
                       f"{total}. Nothing was sent.")  # fmt: skip
    seen: set[str] = set()
    for item in items:
        item.path = f"{base}/{item.relative}"
        problem = None
        if item.size > remote_helper.MAX_UPLOAD:
            problem = f"larger than the {remote_helper.MAX_UPLOAD}-byte limit"
        elif item.relative in seen:
            problem = "already queued"
        else:
            try:
                remote_helper.upload_parts(item.path)
            except remote_helper.Refusal as exc:
                problem = exc.detail
        seen.add(item.relative)
        if problem:
            item.outcome, item.message = "invalid", problem
    return items


def _detail(reply: Reply) -> tuple[str, str]:
    """(error type, readable reason) of an error answer, as the browser's
    errorBody reads it: never "", a status at worst."""
    payload = reply.payload if isinstance(reply.payload, dict) else {}
    error, detail = payload.get("error"), payload.get("detail")
    return (
        error if isinstance(error, str) else "",
        detail if isinstance(detail, str) and detail else f"Request failed ({reply.status}).",
    )


@dataclass
class Portal:
    """The frontend's API at `base`, called as the browser calls it."""

    base: str
    token: str
    user: str | None
    send: Send = urllib_send
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def _headers(self, content_type: str | None = None) -> dict[str, str]:
        headers = {"X-UQF-Request-Id": uuid.uuid4().hex, "X-UQF-Session-Id": self.session_id,
                   "Accept": "application/json"}  # fmt: skip
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.user:
            headers["x-uqf-user"] = self.user
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _url(self, path: str, query: dict[str, str] | None = None) -> str:
        return self.base.rstrip("/") + path + (f"?{urlencode(query)}" if query else "")

    def _json(self, method: str, path: str, body: Any = None) -> dict[str, Any]:
        """A request answered with JSON, or UqsError naming why not."""
        data = None if body is None else json.dumps(body).encode()
        try:
            reply = self.send(method, self._url(path),
                              self._headers("application/json" if data else None), data,
                              REQUEST_TIMEOUT)  # fmt: skip
        except Lost as exc:
            raise UqsError(f"the frontend API at {self.base} did not answer: {exc}") from None
        if not 200 <= reply.status < 300 or not isinstance(reply.payload, dict):
            error, detail = _detail(reply)
            raise UqsError(f"{detail} ({reply.status}{' ' + error if error else ''})")
        return reply.payload

    def target(self, name: str) -> dict[str, Any]:
        """The target as the Files view knows it (GET /services): its dest."""
        listed = self._json("GET", "/services")
        for entry in listed.get("targets", []):
            if entry.get("target") == name:
                return entry
        known = ", ".join(sorted(t.get("target", "?") for t in listed.get("targets", [])))
        raise UqsError(f"the frontend at {self.base} declares no target {name!r} "
                       f"(it declares: {known or 'none'}). Nothing was sent.")  # fmt: skip

    def open(self, name: str, base: str, items: list[Item]) -> dict[str, Any]:
        queued = [i for i in items if i.outcome == "queued"]
        return self._json("POST", f"/services/{quote(name, safe='')}/config/pushes", {
            "confirmed": True, "config_dir": base, "file_count": len(queued),
            "total_bytes": sum(i.size for i in queued),
        })  # fmt: skip

    def upload(self, name: str, item: Item, overwrite: bool, push_id: str) -> None:
        """PUT one file, once; its outcome as the browser classifies it."""
        query = {"path": item.path, "overwrite": str(overwrite).lower(), "push_id": push_id}
        url = self._url(f"/services/{quote(name, safe='')}/files", query)
        try:
            data = item.local.read_bytes()
        except OSError as exc:  # gone or unreadable since it was queued: never sent
            item.outcome, item.message = "failed", f"could not be read: {exc}"
            return
        try:
            reply = self.send("PUT", url, self._headers("application/octet-stream"), data,
                              UPLOAD_TIMEOUT)  # fmt: skip
        except Lost as exc:
            item.outcome = "unknown"
            item.message = (f"the connection to the API failed ({exc}) - the file may or may "
                            "not have been written. List the directory before uploading it "
                            "again.")  # fmt: skip
            return
        payload = reply.payload
        item.http_status = reply.status
        if 200 <= reply.status < 300 and isinstance(payload, dict):
            item.outcome, item.answer = "succeeded", payload
            item.message = "replaced an existing file" if payload.get("replaced") else ""
            return
        item.error, item.message = _detail(reply)
        # only the server's word, or an answer it never gave, is uncertain
        unsure = item.error == UNCERTAIN or payload is None or reply.status == 504
        item.outcome = ("unknown" if unsure else "refused" if reply.status < 500
                        else "failed")  # fmt: skip

    def close(self, name: str, push_id: str, items: list[Item]) -> dict[str, Any]:
        return self._json(
            "POST",
            f"/services/{quote(name, safe='')}/config/pushes/{quote(push_id)}/close",
            {"reported": reported(items)},
        )


#: What the close reports of a file, by its outcome: the browser's REPORTED
#: (a refusal is its "failed" too), and `invalid`, which it never queues.
_REPORTED = {"failed": "failed", "refused": "failed", "unknown": "unknown",
             "cancelled": "cancelled", "invalid": "invalid"}  # fmt: skip


def reported(items: list[Item]) -> list[dict[str, str]]:
    """The close's `reported`: each file that did not succeed. The server
    keeps only those it never recorded itself, and refuses the whole close
    over a path it could not store - so an invalid file whose path is too
    long or not UTF-8 is left out, as the browser leaves out every one."""
    return [{"path": i.path, "outcome": _REPORTED[i.outcome]} for i in items
            if i.outcome in _REPORTED and _storable(i.path)]  # fmt: skip


def _storable(path: str) -> bool:
    from uqf_frontend import remote_helper

    try:
        path.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return len(path) <= remote_helper.MAX_PATH and "\x00" not in path


@contextmanager
def _held_interrupt(pressed: list[bool]) -> Iterator[None]:
    """A Ctrl-C during the block is noted in `pressed`, not raised: the open
    and the close run to their answer, as the browser lets them, so a push
    that was opened is closed. Off the main thread, Ctrl-C is not ours."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.signal(signal.SIGINT, lambda *_: pressed.append(True))
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


@dataclass
class Result:
    push_id: str
    dest: str
    items: list[Item]
    interrupted: bool = False
    closed: dict[str, Any] | None = None
    close_error: str | None = None


def run(
    portal: Portal, name: str, base: str, items: list[Item], overwrite: bool,
    done: Callable[[Item], None] | None = None,
) -> Result:  # fmt: skip
    """Open the push, send each queued file in turn, close it - always.

    A Ctrl-C while a file is in flight leaves that file `unknown` and every
    later one `cancelled`; the push is closed all the same. Refused (UqsError)
    when the push cannot be opened: then nothing was sent. A Ctrl-C during
    the open or the close is held until it answers (_held_interrupt), and one
    during the open cancels every file."""
    pressed: list[bool] = []
    with _held_interrupt(pressed):
        opened = portal.open(name, base, items)
    result = Result(push_id=opened["push_id"], dest=opened.get("dest", ""), items=items,
                    interrupted=bool(pressed))  # fmt: skip
    for item in items:
        if item.outcome != "queued":
            continue
        if result.interrupted:
            item.outcome = "cancelled"
            continue
        try:
            portal.upload(name, item, overwrite, result.push_id)
        except KeyboardInterrupt:
            result.interrupted = True
            item.outcome = "unknown"
            item.message = (
                "the upload was cancelled in flight - the file may or may not have been "
                "written. List the directory before uploading it again."
            )
        if done:
            done(item)
    try:
        with _held_interrupt(pressed):
            result.closed = portal.close(name, result.push_id, items)
    except UqsError as exc:
        result.close_error = (f"push {result.push_id} was not closed: {exc}. Its files are in "
                              "its history; the summary of the push is not.")  # fmt: skip
    return result
