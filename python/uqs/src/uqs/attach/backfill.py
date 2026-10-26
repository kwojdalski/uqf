"""Backfilling into an ATTACHED native TorQ stack's HDB (#1454): which stack, which
directory, and what a run there would do - decided before anything runs.

`uqs job backfill` writes into the HDB of the uqf runtime it belongs to
(`KDBHDB`); `--port` changes which stack it registers with, not where the rows
go. A backfill for a dataset an attachment publishes must therefore name its
destination out loud, and take it from the saved attachment - the stack id, the
storage the stack reported and that `verify` has since checked - never from a
default. This module is that resolution plus the read-only admission plan; it
starts no worker and writes nothing, so a plan can be run against any stack,
any number of times, and leave it as it found it.

Two stages, the second in uqs.attach.backfill_plan because it needs a live stack:

  resolve   (here) the saved attachment and the worker, offline: refuse a detached or
            unverified attachment, an unpinned or missing HDB, a dataset the
            attachment does not publish, a table whose shape differs from the
            worker's, a worker with a custom IO manager, a fixture run, and an
            override that names another stack
  admit     the live stack: the processes that write the destination (their
            HDB directory is the attachment's), and the HDB readers that serve
            it - the ones a backfill would have to tell to reload

The coverage ledger, checkpoint and run lock are not shared with anything else:
they live in a directory named for the attachment and the destination's
identity (`Destination.state_dir`), so coverage recorded for one HDB can never
suppress a run into another.

EXECUTION is uqs.attach.backfill_run's: it admits afresh, with the coordination checks of
uqs.attach.backfill_coordination, immediately before anything starts.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from uqs.attach import config
from uqs.attach.config import Attachment, AttachmentError
from uqs.attach.manifest import Manifest
from uqs.attach.schema import SchemaError, TableSpec, differences, parse_definition, parse_nested
from uqs.model.declarations import Declaration, read_declarations
from uqs.paths import TABLES_FILE, UqsError, UqsPaths
from uqs.stack import backfill as stack_backfill
from uqs.stack import backfill_status, runs
from uqs.stack.attach_state import VERIFIED

#: What an attached backfill may do: `validate` and `plan` read configuration, code
#: and the local ledgers; `dry-run` and `run` start the runner (uqs.attach.backfill_run),
#: which admits afresh first.
READ_ONLY_MODES = ("validate", "plan")
EXECUTION_MODES = ("dry-run", "run")

#: The q script that evaluates a plan, and the subdirectory (of the status
#: directory) attached runs keep their state in.
PLAN_SCRIPT = Path("scripts") / "dev" / "attached_plan.q"
STATE_SUBDIR = backfill_status.STATE_SUBDIR
_TABLE_LINE = re.compile(r"^(?:\.qetl\.plant\.)?([a-z_][a-z0-9_]*):\(\[\]")


@dataclass(frozen=True)
class Destination:
    """Where an attached backfill writes, fully resolved: no field is a default."""

    attachment: str
    stack_id: int
    host: str
    #: the canonical path of the native HDB (symlinks resolved)
    hdb_dir: Path
    dataset: str
    #: 12 hex digits naming (host, stack id, canonical HDB): the key coverage,
    #: the checkpoint and the run lock are filed under
    identity: str
    #: where this destination's coverage ledger, checkpoint, lock and run
    #: ledger live - nothing else writes there
    state_dir: Path

    def as_dict(self) -> dict[str, Any]:
        return {
            "attachment": self.attachment,
            "stack_id": self.stack_id,
            "host": self.host,
            "hdb_dir": str(self.hdb_dir),
            "dataset": self.dataset,
            "identity": self.identity,
            "state_dir": str(self.state_dir),
        }


def identity_of(host: str, stack_id: int, hdb_dir: Path) -> str:
    """The destination's identity: a stable digest of where the rows go.

    Host and stack id are in it as well as the path because two stacks (or two
    machines) can name the same relative layout; the path is canonical so a
    symlink and its target are one destination.
    """
    text = f"{host}\0{stack_id}\0{hdb_dir}"
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def state_dir_for(paths: UqsPaths, attachment: str, identity: str) -> Path:
    """The directory an attached run's ledgers live in."""
    return runs.status_dir(paths) / STATE_SUBDIR / f"{attachment}-{identity}"


def state_dir_of(paths: UqsPaths, attachment: str, attach_root: Path | None = None) -> Path:
    """The state directory of `attachment`'s destination - where its attached backfills keep
    coverage, checkpoints and the run ledger - without needing a worker: the identity is the
    attachment's host, stack and pinned HDB alone."""
    att = config.load(attachment, attach_root)
    if not att.storage.hdb_dir:
        raise UqsError(f"attachment {att.name} pins no HDB directory: it has no destination")
    ident = identity_of(att.stack.host, att.stack.id, canonical(att.storage.hdb_dir))
    return state_dir_for(paths, att.name, ident)


def _declaration(paths: UqsPaths, worker: str) -> Declaration:
    stack_backfill.procname_for(worker)  # the refusal that names the known workers
    found = [d for d in read_declarations(paths.repo_root) if d.worker == worker]
    if not found or not found[0].dataset:
        raise UqsError(f"{worker} declares no dataset, so there is nothing to attach it to")
    return found[0]


def _table_in(path: Path, table: str) -> TableSpec | None:
    """The table's definition in `path`, with the element types its `nested[...]`
    lines declare: an untyped `()` column is refused without them (#1451)."""
    if not path.is_file():
        return None
    lines = path.read_text().splitlines()
    try:
        nested: dict[str, str] = {}
        for line in lines:
            found = parse_nested(line)
            if found and found[0] == table:
                nested.update(found[1])
        for line in lines:
            match = _TABLE_LINE.match(line)
            if match and match.group(1) == table:
                return parse_definition(line, nested)
    except SchemaError as exc:
        raise AttachmentError(f"{path}: table {table}: {exc}") from None
    return None


def worker_table(paths: UqsPaths, att: Attachment, dataset: str) -> TableSpec | None:
    """The table definition the worker's dataset has: the attachment's bundle
    first (its own `tables.q`), else this tree's `plant_tables.q`."""
    folder = att.origin.get("folder")
    if folder:
        found = _table_in(Path(folder) / "tables.q", dataset)
        if found is not None:
            return found
    return _table_in(paths.repo_root / TABLES_FILE, dataset)


def canonical(path: str) -> Path:
    return Path(os.path.realpath(path))


def require_verified(attachment: str, attach_root: Path | None = None) -> None:
    """Refuse unless the attachment's manifest, read now, says `verified`. Admission
    calls it, and launch again under the attachment's lock (#1526): a detach can land
    between the two."""
    folder = config.directory(attachment, attach_root)
    manifest = (
        Manifest.load(attachment, attach_root) if Manifest.exists(attachment, attach_root) else None
    )
    state = manifest.state if manifest else "planned"
    if state != VERIFIED:
        raise UqsError(
            f"attachment {attachment} is {state}, not {VERIFIED}: a backfill needs the "
            f"attachment's identity and storage checked (`uqs job attach verify {attachment}`)"
            + (
                " - a detached attachment's stack is not ours to write"
                if state == "detached"
                else ""
            )
            + f" [{folder}]"
        )


def resolve(
    paths: UqsPaths,
    attachment: str,
    worker: str,
    *,
    port: int | None = None,
    fixture: bool = False,
    attach_root: Path | None = None,
    on_this_machine: bool = True,
) -> Destination:
    """The destination `worker` would write for `attachment`, or a refusal.

    Offline: reads the saved attachment, its manifest and the tree's
    declarations, and looks at the HDB directory on disk. Nothing is created.
    `on_this_machine=False` is the extraction side of a remote writer (#1650):
    the HDB is the stack's, on another machine, so it is named as the
    attachment pinned it and not looked for here.
    """
    att = config.load(attachment, attach_root)
    if fixture:
        raise UqsError(
            "--fixture is refused with --attach: an attached backfill writes the "
            "stack's real HDB, and a fixture there would be indistinguishable from data"
        )
    if port is not None and port != att.stack.id:
        raise UqsError(
            f"--port {port} conflicts with attachment {att.name}, which is stack "
            f"{att.stack.id}: --attach takes the stack from the attachment, so drop --port "
            f"or pass --port {att.stack.id}"
        )
    require_verified(att.name, attach_root)
    decl = _declaration(paths, worker)
    try:
        table = att.table(decl.dataset)
    except AttachmentError:
        raise UqsError(
            f"{worker} writes {decl.dataset}, which attachment {att.name} does not publish "
            f"(it has {', '.join(t.name for t in att.tables) or 'no tables'}): "
            "a backfill only fills a dataset the attachment owns"
        ) from None
    wanted = worker_table(paths, att, decl.dataset)
    if wanted is None:
        raise UqsError(
            f"no table definition for {decl.dataset} in the attachment's bundle or this tree: "
            "its shape cannot be compared with the attachment's"
        )
    drift = differences(table, [[n, c] for n, c in wanted.columns])
    if drift:
        raise UqsError(
            f"{decl.dataset} differs between attachment {att.name} and the worker's "
            f"definition: {'; '.join(drift)}"
        )
    hdb = att.storage.hdb_dir
    if not hdb:
        raise UqsError(
            f"attachment {att.name} pins no HDB directory (storage.hdb_dir is empty): an attached "
            "backfill never falls back to uqf's own HDB - `uqs job attach plan --replace` "
            "against the running stack records it"
        )
    if on_this_machine and not Path(hdb).is_dir():
        raise UqsError(
            f"attachment {att.name}'s HDB directory {hdb} is not a directory on this machine: "
            "it is not created, and nothing falls back to another HDB"
        )
    real = canonical(hdb) if on_this_machine else Path(hdb)
    ident = identity_of(att.stack.host, att.stack.id, real)
    return Destination(
        att.name,
        att.stack.id,
        att.stack.host,
        real,
        decl.dataset,
        ident,
        state_dir_for(paths, att.name, ident),
    )
