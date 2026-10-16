"""Talking to the processes of a stack that is not ours.

Everything an attachment reads or changes on a running TorQ process goes
through ONE operation: send a q lambda (python/uqs/src/uqs/attach/q/<op>.q) and
ONE JSON string argument, get one JSON string back. Reasons:

  * No q code is left behind on a shared process. A lambda sent over IPC runs
    and is gone; the only thing an attachment ever leaves in a process is its
    ownership record, `.uqfattach.owned`, written by the operations that
    change something and read by the ones that undo it.
  * No type guessing between Python and q. kola turns a Python str into a q
    symbol and a nested dict into whatever it likes; JSON text in and out
    sidesteps both, and every operation validates its own input.
  * The same seam is the test seam: `Ipc` is a Protocol, tests pass a scripted
    one, and failure injection wraps the real one.

A fresh connection per call: an attachment makes a handful of calls over
minutes, a held handle would go stale, and a connection holds one of the
process's inbound slots only for the length of the call.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any, Protocol

from uqs.attach.config import AttachmentError


class IpcError(AttachmentError):
    """A process did not answer, or answered with a q error."""

    def __init__(self, endpoint: Endpoint, op: str, reason: str) -> None:
        super().__init__(f"{endpoint.label} ({op}): {reason}")
        self.endpoint = endpoint
        self.op = op
        self.reason = reason


@dataclass(frozen=True)
class Endpoint:
    procname: str
    host: str
    port: int

    @property
    def label(self) -> str:
        return f"{self.procname} at {self.host}:{self.port}"


class Ipc(Protocol):
    """What an attachment needs of a connection to a process."""

    def call(self, endpoint: Endpoint, op: str, request: dict[str, Any]) -> dict[str, Any]:
        """Run operation `op` on `endpoint`; the decoded JSON answer.

        Raises IpcError when the process cannot be reached or q raises.
        """
        ...


@cache
def source(op: str) -> str:
    """The lambda text of operation `op`: the .q file without its header
    comment (lines starting with `/` in column 0)."""
    text = resources.files("uqs.attach").joinpath("q", f"{op}.q").read_text(encoding="utf-8")
    body = "\n".join(line for line in text.splitlines() if not line.startswith("/")).strip()
    if not body.startswith("{"):
        raise AttachmentError(f"attach operation {op}.q must be one lambda")
    return body


class KolaIpc:
    """The real thing: kola, one connection per call."""

    def __init__(self, user: str, password: str, timeout_ms: int = 8000) -> None:
        self._user, self._password, self._timeout = user, password, timeout_ms

    def call(self, endpoint: Endpoint, op: str, request: dict[str, Any]) -> dict[str, Any]:
        import kola  # lazy: only the commands that talk to a stack need it

        try:
            # 127.0.0.1, not "localhost": with a timeout kola tries ::1 only and q listens on IPv4.
            host = "127.0.0.1" if endpoint.host == "localhost" else endpoint.host
            conn = kola.Q(
                host, endpoint.port, user=self._user, passwd=self._password, timeout=self._timeout
            )
        except Exception as exc:  # noqa: BLE001 - kola raises its own, untyped, errors
            raise IpcError(endpoint, op, f"cannot connect: {_clean(exc)}") from None
        try:
            answer = conn.sync(source(op), json.dumps(request))
        except Exception as exc:  # noqa: BLE001
            raise IpcError(endpoint, op, f"q error: {_clean(exc)}") from None
        finally:
            _close(conn)
        try:
            decoded = json.loads(answer)
        except TypeError, ValueError:
            raise IpcError(
                endpoint, op, f"answered something other than JSON: {answer!r:.80}"
            ) from None
        if not isinstance(decoded, dict):
            raise IpcError(endpoint, op, f"answered JSON that is not an object: {answer!r:.80}")
        return decoded


def _clean(exc: Exception) -> str:
    return str(exc).replace("Internal Server Error - ", "").strip() or type(exc).__name__


def _close(conn: Any) -> None:
    try:
        conn.disconnect()
    except Exception:  # noqa: BLE001, S110 - closing a dead connection is not an error
        pass
