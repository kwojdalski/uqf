"""The change log of deploy_targets.toml: deploy_targets.log.jsonl (#1756).

One JSON line per change `uqs` made to the file (deploy/target_changes.py):
when, who, from which machine, the command, each changed table's block before
and after, the diff, and the resulting file with its sha256. The file is per
machine and not in git, so this is its history; `uqs target log` reads it and
`uqs target revert` undoes an entry.

The last entry's file is what `uqs` last left. When the file differs from it,
it was edited by hand - which is allowed - and outside() makes the "edited
outside uqs" entry the next change records before its own.
"""

from __future__ import annotations

import difflib
import getpass
import hashlib
import json
import os
import socket
import tomllib
from datetime import UTC, datetime
from pathlib import Path

from uqs.deploy import target_file, targets
from uqs.deploy.config import DeployError

#: Entry kinds that are not `uqs target <verb>` commands.
OUTSIDE = "edited outside uqs"
REGISTERED = "registered by push"
ADOPTED = "adopted"


def log_path(root: Path) -> Path:
    """deploy_targets.log.jsonl, beside the file wherever it is."""
    path = targets.declaration_path(root)
    return path.with_name(f"{path.stem}.log.jsonl")


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def read_log(root: Path) -> list[dict]:
    """Every change recorded, oldest first; none when there is no log."""
    path = log_path(root)
    if not path.is_file():
        return []
    entries = []
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DeployError(
                "arguments", f"{path}, line {n}: not a change log entry ({exc})"
            ) from None
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def append(root: Path, entries: list[dict]) -> None:
    path = log_path(root)
    with open(path, "a") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def unified_diff(before: str, after: str, path: Path) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            f"{path.name} (before)",
            f"{path.name} (after)",
        )
    )


def entry(kind: str, command: str, target: str | None, before: str, after: str, path: Path) -> dict:
    return {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "user": getpass.getuser(),
        "host": socket.gethostname(),
        "kind": kind,
        "command": command,
        "target": target,
        "tables": target_file.changed(before, after),
        "diff": unified_diff(before, after, path),
        "hash": digest(after),
        "file": after,
    }


def pending_outside_edit(root: Path) -> dict | None:
    """The "edited outside uqs" entry the next change would record first:
    the file differs from what the log's last entry left; else None."""
    path = targets.declaration_path(root)
    current = path.read_text() if path.is_file() else ""
    return outside(read_log(root), current, path)


def outside(entries: list[dict], current: str, path: Path) -> dict | None:
    last = next((e for e in reversed(entries) if "hash" in e), None)
    if last is None or last["hash"] == digest(current):
        return None
    return entry(OUTSIDE, OUTSIDE, None, str(last.get("file", "")), current, path)


def keys_changed(table: dict) -> list[str]:
    """One table entry's changed settings, for a line of `uqs target log`:
    "+host", "-labels", "dest".  A whole table added or removed is "added"
    or "removed"."""
    if table["before"] is None:
        return ["added"]
    if table["after"] is None:
        return ["removed"]
    key = target_file.key_of(table["table"])
    old = target_file.lookup(tomllib.loads(table["before"]), key) or {}
    new = target_file.lookup(tomllib.loads(table["after"]), key) or {}
    out = [f"+{k}" for k in new if k not in old]
    out += [f"-{k}" for k in old if k not in new]
    out += [k for k in new if k in old and new[k] != old[k]]
    return out
