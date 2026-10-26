"""The attached job's own process: held stopped until verified, started under
the attachment's stack, stopped on detach.

The gate itself is in stack/attach_state.py (the start path is below this
package); this is the other direction - what the attach commands do to the
publisher. Every start goes through `runtime.start`, so the same refusals
apply to it as to `uqs start`, and with the attachment's base port passed
explicitly: it is never left to the runtime's default.
"""

from __future__ import annotations

from typing import Protocol

from uqs.attach import plan as planning
from uqs.attach.config import Attachment, AttachmentError
from uqs.attach.facts import Host
from uqs.paths import UqsError, UqsPaths, repo_root
from uqs.stack import runtime

#: Where the publisher runs: `runtime.start` starts it on this machine.
PUBLISHER_HOST = "localhost"


class Publisher(Protocol):
    def running(self, att: Attachment) -> bool: ...
    def start(self, att: Attachment) -> None: ...
    def stop(self, att: Attachment) -> None: ...


def stop_publisher(att: Attachment, publisher: Publisher) -> bool:
    """Stop the publisher if it runs; True when it did. Still listening afterwards is an error."""
    if not publisher.running(att):
        return False
    publisher.stop(att)
    if publisher.running(att):
        raise AttachmentError(
            f"{att.publisher.procname} is still listening after stop - detach will not touch the "
            "stack while the publisher may be publishing"
        )
    return True


class UqsPublisher:
    """The publisher as `uqs start` / `uqs stop` see it."""

    def __init__(self, paths: UqsPaths, host: Host | None = None) -> None:
        self._paths, self._host = paths, host or Host()

    def running(self, att: Attachment) -> bool:
        """Listening HERE: the publisher runs where `start` started it, not on
        the stack's host, which may hold another process on that port (#1651)."""
        port = planning.publisher_port(att, repo_root()).port
        return port is not None and self._host.listening(PUBLISHER_HOST, port)

    def start(self, att: Attachment) -> None:
        try:
            done = runtime.start(
                self._paths, att.publisher.procname, base_port=att.stack.id, capture=True
            )
        except UqsError as exc:
            raise AttachmentError(f"{att.publisher.procname} did not start: {exc}") from None
        if done.returncode != 0:
            raise AttachmentError(
                f"{att.publisher.procname} did not start (exit {done.returncode}): "
                f"{(done.stdout or '') + (done.stderr or '')}".strip()
            )

    def stop(self, att: Attachment) -> None:
        try:
            done = runtime.stop(
                self._paths, att.publisher.procname, base_port=att.stack.id, capture=True
            )
        except UqsError as exc:
            raise AttachmentError(f"{att.publisher.procname} did not stop: {exc}") from None
        if done.returncode != 0:
            raise AttachmentError(
                f"{att.publisher.procname} did not stop (exit {done.returncode}): "
                f"{(done.stdout or '') + (done.stderr or '')}".strip()
            )
