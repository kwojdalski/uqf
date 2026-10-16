"""The operation manifest: what an attachment did to the stack, and what it
may therefore undo.

Reversal is only ever as safe as the record it works from, so the record has
three properties the rest of the workflow leans on:

  * WRITE AHEAD. An operation is journalled `pending` BEFORE its remote call
    and finished AFTER it. A crash or a lost connection between the two leaves
    `pending` - "outcome unknown" - never a done step that was not done, and
    never a change nobody wrote down.
  * OWNERSHIP, per operation. `created` (this attachment made it, and may
    remove it), `existing` (it was there; never removed), `shared` (another
    attachment made it and this one depends on it). Detach removes `created`
    and nothing else.
  * ONE TOKEN. The manifest's token is also written INTO the processes it
    changes (`.uqfattach.owned`), so a `pending` step can be resolved by asking
    the process whether our token is on the object.

Written whole by atomic replace, so a reader sees the old file or the new one.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from uqs.attach import config
from uqs.attach.config import Attachment, AttachmentError
from uqs.stack.attach_state import MANIFEST_FILE

SCHEMA_VERSION = 1
#: What an operation can be in.
PENDING, DONE, FAILED, REVERTED, RETAINED = "pending", "done", "failed", "reverted", "retained"
OP_STATUSES = (PENDING, DONE, FAILED, REVERTED, RETAINED)
CREATED, EXISTING, SHARED = "created", "existing", "shared"
#: The attachment's state. `verified` opens the publisher's start gate.
STATES = (
    "applying",
    "applied",
    "verified",
    "partial",
    "detaching",
    "detached",
    "rolled_back",
    "rollback_partial",
)


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass
class Op:
    id: int
    op: str
    process: str
    table: str
    status: str = PENDING
    ownership: str = ""
    pre: dict = field(default_factory=dict)
    post: dict = field(default_factory=dict)
    depends_on: list[int] = field(default_factory=list)
    at: str = ""
    note: str = ""


@dataclass
class Manifest:
    attachment: str
    token: str
    state: str = "applying"
    tables: dict[str, dict] = field(default_factory=dict)
    operations: list[Op] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)
    #: set once rows were seen anywhere downstream; evidence, not a flag to trust
    publication: dict = field(default_factory=lambda: {"observed": False})
    #: accepted risks apply was confirmed against: who confirmed, when, and the overrides
    #: (with the facts they were accepted against) in force at that moment
    risk_acceptance: dict = field(default_factory=dict)
    path: Path = field(default_factory=Path, repr=False, compare=False)

    # ----------------------------------------------------------- creation

    @classmethod
    def new(cls, att: Attachment, path: Path) -> Manifest:
        m = cls(
            att.name,
            secrets.token_hex(12),
            tables={t.name: {"columns": t.as_pairs(), "sha": t.sha} for t in att.tables},
            path=path,
        )
        m.event("created", f"{len(att.tables)} table(s) for stack {att.stack.id}")
        return m

    @classmethod
    def path_for(cls, name: str, root: Path | None = None) -> Path:
        return config.directory(name, root) / MANIFEST_FILE

    @classmethod
    def exists(cls, name: str, root: Path | None = None) -> bool:
        return cls.path_for(name, root).is_file()

    @classmethod
    def load(cls, name: str, root: Path | None = None) -> Manifest:
        path = cls.path_for(name, root)
        if not path.is_file():
            raise AttachmentError(f"attachment {name} has no manifest - nothing was applied")
        try:
            data = json.loads(path.read_text())
            if data["schema"] != SCHEMA_VERSION:
                raise AttachmentError(f"{path}: manifest schema {data['schema']!r} is not 1")
            ops = [Op(**o) for o in data.pop("operations")]
            data.pop("schema")
            return cls(**data, operations=ops, path=path)
        except (ValueError, KeyError, TypeError) as exc:
            raise AttachmentError(f"{path} is not a readable manifest ({exc})") from None

    # ------------------------------------------------------------ writing

    def save(self) -> None:
        data = asdict(self)
        data.pop("path")
        data["schema"] = SCHEMA_VERSION
        config.atomic_write(self.path, json.dumps(data, indent=2, sort_keys=True) + "\n")

    def event(self, what: str, detail: str = "") -> None:
        self.history.append({"at": now(), "event": what, "detail": detail})

    def set_state(self, state: str, detail: str = "") -> None:
        if state not in STATES:
            raise ValueError(f"unknown attachment state {state!r}")
        self.state = state
        self.event(state, detail)
        self.save()

    # --------------------------------------------------------- operations

    def find(self, op: str, process: str, table: str) -> Op | None:
        return next(
            (o for o in self.operations if (o.op, o.process, o.table) == (op, process, table)), None
        )

    def begin(
        self, op: str, process: str, table: str, pre: dict, depends_on: list[int] | None = None
    ) -> Op:
        """Journal `op` as pending - and return it - BEFORE the remote call.
        A retry reuses the operation's record rather than adding a second."""
        entry = self.find(op, process, table)
        if entry is None:
            entry = Op(len(self.operations) + 1, op, process, table)
            self.operations.append(entry)
        elif entry.status in (PENDING, FAILED):
            # A retry of an attempt whose outcome we never learned: what was true
            # before the FIRST try is the record. The second sees what the first made.
            pre = entry.pre
        entry.status, entry.pre, entry.at = PENDING, pre, now()
        entry.depends_on = depends_on or []
        entry.note = ""
        self.save()
        return entry

    def finish(self, entry: Op, status: str, ownership: str = "", post: dict | None = None,
               note: str = "") -> None:  # fmt: skip
        if status not in OP_STATUSES:
            raise ValueError(f"unknown operation status {status!r}")
        entry.status, entry.at = status, now()
        if ownership:
            entry.ownership = ownership
        if post is not None:
            entry.post = post
        entry.note = note
        self.save()

    def done(self, op: str, process: str, table: str) -> Op | None:
        entry = self.find(op, process, table)
        return entry if entry is not None and entry.status == DONE else None

    def created(self) -> list[Op]:
        """What this attachment made and may therefore remove, newest first."""
        return [
            o
            for o in reversed(self.operations)
            if o.ownership == CREATED and o.status in (DONE, PENDING)
        ]
