"""`--process-file` and `--start-with-all/--no-start-with-all` as saved settings
(#1845): what a command line changes about an attachment's native registration.

Both are tri-state on the command line: an option left out keeps the saved
setting, `--no-start-with-all` is an explicit False, and only a NEW registration
takes the default, on demand. Neither needs `--replace`: they change how the
publisher is started, not which stack or tables it is attached to, as `--ssh`
does not. A registry path is refused unless it is a file on this machine and
the stack's host is this machine - a path is never read as another host's.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

from uqs.attach import config
from uqs.attach.config import Attachment, AttachmentError, Native
from uqs.attach.facts import Host
from uqs.attach.native import REMOTE

#: `host:/path`, the way scp and ssh name another machine's file.
_REMOTE_PATH = re.compile(r"^[A-Za-z0-9_.@-]+:/")


def local_file(given: Path, att: Attachment, host: Host | None = None) -> str:
    """`given` as the absolute path of a registry file on this machine."""
    if _REMOTE_PATH.match(str(given)) or not (host or Host()).is_local(att.stack.host):
        raise AttachmentError(f"--process-file {given}: " + REMOTE.format(host=att.stack.host))
    path = given.expanduser().resolve()
    if not path.is_file():
        raise AttachmentError(
            f"--process-file {given}: no such file on this machine - it names the stack's "
            "existing TorQ process registry (its process.csv), which uqs never creates"
        )
    return str(path)


def settle(
    att: Attachment,
    saved: Native | None,
    process_file: Path | None,
    start_with_all: bool | None,
    host: Host | None = None,
) -> tuple[Attachment, list[str]]:
    """`att` with the native settings the options leave it, and what changed
    against `saved` as "option: before -> after"."""
    base = saved or Native()
    path = base.process_file
    if process_file is not None:
        path = local_file(process_file, att, host)
        if base.process_file and path != base.process_file:
            raise AttachmentError(
                f"attachment {att.name} registers its publisher in {base.process_file}; it "
                f"cannot move to {path} in place: `uqs job attachment remove {att.name}` takes "
                "the row out, then add it again with the new --process-file"
            )
    if not path and start_with_all is not None:
        raise AttachmentError(
            "--start-with-all and --no-start-with-all set the startwithall of the publisher's "
            "row in the stack's process file: give --process-file to register it there"
        )
    wanted = base.start_with_all if start_with_all is None else start_with_all
    if wanted and att.persist not in config.DURABLE:
        raise AttachmentError(
            "--start-with-all needs durable table registration: with --runtime-only a "
            "tickerplant restart - which a `start all` is - loses the tables, so the publisher "
            "could never start. Drop --runtime-only, or give --no-start-with-all"
        )
    native = Native(path, wanted)
    changed = []
    if path != base.process_file:
        changed.append(f"--process-file: {base.process_file or '(none)'!r} -> {path!r}")
    if path and wanted != base.start_with_all:
        changed.append(f"--start-with-all: {base.start_with_all} -> {wanted}")
    return replace(att, native=native), changed
