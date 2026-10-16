"""The persisted configuration of one attachment, and where attachments live.

Attaching a job to a stack that already exists went wrong in one specific way:
the process was started under uqf's own defaults, so its discovery lookup was
`{KDBBASEPORT}+1` of the RUNTIME's base port (6051) while the fleet's
discovery was at 6001. Everything that makes the answer to "which stack?" is
therefore written down here, once, by `plan`, and every later command reads it
back instead of recomputing it:

  stack id        the `-stackid` / KDBBASEPORT of the fleet. No default.
  discovery       host and port, explicit (derived from the stack id exactly
                  once, at plan time, and then stored - never re-derived).
  tickerplant     which process publishers write to, and the consumers that
                  must persist the table.
  storage         where the stack keeps tickerplant logs, its schema file and
                  the wdb/hdb data, as OBSERVED at plan time or pinned by the
                  operator; a later run that sees something else refuses.
  credentials     the user and the NAME of the environment variable holding
                  the password. The secret itself is never written.

A directory per attachment holds `attachment.json` (this) and `manifest.json`
(manifest.py). The directory is `$UQS_ATTACH_DIR`, else `$UQS_DATA_ROOT` or the
checkout's `output/`, plus `attachments/`.
"""

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

from uqs.attach.schema import TableSpec
from uqs.paths import UqsError
from uqs.stack import attach_state
from uqs.stack.attach_state import ATTACH_DIR_ENV as ATTACH_DIR_ENV  # re-exported
from uqs.stack.attach_state import CONFIG_FILE

SCHEMA_VERSION = 1
PERSIST_MODES = ("schema-file", "runtime-only")
#: The credential TorQ's starter pack ships (accesslist.txt). Used when no
#: password variable is configured, and said so in every plan.
DEFAULT_USER = "admin"
DEFAULT_PASSWORD = "admin"


class AttachmentError(UqsError):
    """An attachment cannot be read, written or used as asked."""


@dataclass(frozen=True)
class Storage:
    """Where the stack keeps what an attachment depends on. Empty = not pinned."""

    tplogdir: str = ""
    schema_file: str = ""
    wdb_savedir: str = ""
    hdb_dir: str = ""


@dataclass(frozen=True)
class Stack:
    """Which stack, by identity, and how to reach it."""

    id: int
    host: str
    discovery_host: str
    discovery_port: int
    tickerplant: str = ""
    consumers: tuple[str, ...] = ()
    user: str = DEFAULT_USER
    password_env: str = ""

    def password(self) -> str:
        if not self.password_env:
            return DEFAULT_PASSWORD
        value = os.environ.get(self.password_env)
        if value is None:
            raise AttachmentError(
                f"the password variable {self.password_env} is not set - it names the "
                "environment variable the stack's password comes from"
            )
        return value


@dataclass(frozen=True)
class Publisher:
    """The uqf process that publishes the tables - held stopped until verified."""

    job: str
    procname: str
    runtime: str = ""


@dataclass(frozen=True)
class Attachment:
    name: str
    stack: Stack
    publisher: Publisher
    tables: tuple[TableSpec, ...]
    storage: Storage = field(default_factory=Storage)
    persist: str = "schema-file"
    #: where the job came from: {"bundle": name, "version": v, "folder": path} or {}
    origin: dict = field(default_factory=dict)

    def table(self, name: str) -> TableSpec:
        for spec in self.tables:
            if spec.name == name:
                return spec
        raise AttachmentError(f"{self.name} has no table {name}")


def state_root() -> Path:
    return attach_state.state_root()


def directory(name: str, root: Path | None = None) -> Path:
    try:
        return attach_state.directory(name, root)
    except UqsError as exc:
        raise AttachmentError(str(exc)) from None


def names(root: Path | None = None) -> list[str]:
    base = root or state_root()
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if (p / CONFIG_FILE).is_file())


def to_json(att: Attachment) -> dict:
    data = asdict(att)
    data["schema"] = SCHEMA_VERSION
    data["tables"] = [{"name": t.name, "columns": t.as_pairs()} for t in att.tables]
    data["stack"]["consumers"] = list(att.stack.consumers)
    return data


def from_json(data: dict) -> Attachment:
    if data.get("schema") != SCHEMA_VERSION:
        raise AttachmentError(
            f"attachment config schema {data.get('schema')!r} is not {SCHEMA_VERSION} - "
            "written by another uqs; plan it again"
        )
    stack = dict(data["stack"])
    stack["consumers"] = tuple(stack.get("consumers", ()))
    if data.get("persist") not in PERSIST_MODES:
        raise AttachmentError(f"persist must be one of {PERSIST_MODES}")
    return Attachment(
        name=data["name"],
        stack=Stack(**stack),
        publisher=Publisher(**data["publisher"]),
        tables=tuple(
            TableSpec(t["name"], tuple((n, c) for n, c in t["columns"])) for t in data["tables"]
        ),
        storage=Storage(**data.get("storage", {})),
        persist=data["persist"],
        origin=data.get("origin", {}),
    )


def atomic_write(path: Path, text: str) -> None:
    """Replace `path` whole or not at all: a crash leaves the old file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def save(att: Attachment, root: Path | None = None) -> Path:
    path = directory(att.name, root) / CONFIG_FILE
    atomic_write(path, json.dumps(to_json(att), indent=2, sort_keys=True) + "\n")
    return path


def load(name: str, root: Path | None = None) -> Attachment:
    path = directory(name, root) / CONFIG_FILE
    if not path.is_file():
        raise AttachmentError(
            f"no attachment {name!r} (looked in {path.parent}) - `uqs job attach plan` creates one"
        )
    try:
        return from_json(json.loads(path.read_text()))
    except (ValueError, KeyError, TypeError) as exc:
        raise AttachmentError(f"{path} is not a readable attachment config ({exc})") from None


@contextmanager
def locked(name: str, root: Path | None = None) -> Iterator[None]:
    """One operation at a time per attachment, across processes.

    A second `apply` or `detach` started while another runs would interleave
    two journals over the same remote state; flock refuses it, and the lock is
    released by the kernel if the holder dies.
    """
    folder = directory(name, root)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / ".lock").open("w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise AttachmentError(
                f"another uqs operation on attachment {name} is running (lock held on "
                f"{folder / '.lock'}) - wait for it, or check `ps` if it is stale"
            ) from None
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
