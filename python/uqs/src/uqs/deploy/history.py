"""This workstation's deployment history, and the default deployment per
server (#956).

Every `uqs deploy push` that gets as far as running - deployed or failed -
appends one entry to deploy_history.json, beside deploy_targets.toml (or
wherever $UQS_DEPLOY_HISTORY names). `uqs deploy list` reads it. The server
keeps its own truth in each release's deploy-report.json; this is the
operator's record of what THEY pushed and where, so it needs no ssh to read.

A push to a host and dest that no target names yet registers one, named
<server>-<dest's last folder>, so the next `uqs --target` needs no `uqs target
add`. A
target that already points there is reused, never duplicated.

THE DEFAULT PER SERVER is that server's most recent successful deployment:
`uqs --target HOST` (a host, rather than a target's name) resolves to it, and
`uqs target shell` with no name opens the most recent one of all.

Recording never fails a deployment: the deployment has already happened, and
a history that could not be written is worth a warning, not an error.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from uqs.deploy import targets
from uqs.deploy.config import Config, DeployError
from uqs.logger import get_logger

log = get_logger(__name__)

HISTORY = "deploy_history.json"
HISTORY_ENV = "UQS_DEPLOY_HISTORY"
#: Oldest entries go first past this many; a list, not an archive.
KEEP = 1000


def history_path(root: Path) -> Path:
    configured = os.environ.get(HISTORY_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    return targets.declaration_path(root).with_name(HISTORY)


def read(root: Path) -> list[dict]:
    """Every recorded deployment, oldest first; none when there is no file."""
    path = history_path(root)
    if not path.is_file():
        return []
    try:
        entries = json.loads(path.read_text()).get("deployments", [])
    except (json.JSONDecodeError, AttributeError) as exc:
        raise DeployError("arguments", f"{path} is not a deployment history: {exc}") from None
    return [e for e in entries if isinstance(e, dict)]


def _write(root: Path, entries: list[dict]) -> None:
    path = history_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"deployments": entries[-KEEP:]}, indent=2) + "\n")
    tmp.replace(path)


def server_of(host: str) -> str:
    """The machine a destination names: `svc@uat.example` -> `uat.example`."""
    return host.rsplit("@", 1)[-1]


def _target_for(root: Path, cfg: Config) -> str | None:
    """The target naming cfg's host and dest, registering one if none does."""
    if cfg.target:
        return cfg.target
    declared = targets.read(root) if targets.declaration_path(root).is_file() else {}
    for name, t in sorted(declared.items()):
        if t.get("host") == cfg.host and t.get("dest") == cfg.dest:
            return name
    # <server>-<dest's last folder>: never the bare server name, which means
    # that server's latest deployment, whichever dest it went to.
    raw = f"{server_of(cfg.host)}-{cfg.dest.rstrip('/').rsplit('/', 1)[-1]}"
    base = re.sub(r"[^a-z0-9_-]+", "-", raw.lower()).strip("-") or "server"
    if not base[0].isalpha():
        base = f"host-{base}"
    name, n = base[:60], 2
    while name in declared:
        name, n = f"{base[:56]}-{n}", n + 1
    entry = {"host": cfg.host, "dest": cfg.dest, "remote_user": cfg.remote_user}
    path = targets.append(root, name, entry)
    log.info("registered target {} in {}: uqs --target {} summary", name, path, name)
    return name


class Reported(Protocol):
    """What record needs of a deployment's report (deploy/stages.py's Report)."""

    def as_dict(self) -> dict: ...


def record(root: Path, cfg: Config, report: Reported, now: datetime | None = None) -> None:
    """Append this push to the history; register its target when it deployed."""
    r = report.as_dict()
    try:
        name = _target_for(root, cfg) if r.get("status") == "deployed" else cfg.target
        entry = {
            "at": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
            "target": name,
            "host": cfg.host,
            "dest": cfg.dest,
            "remote_user": cfg.remote_user,
            "release": r.get("release"),
            "revision": r.get("revision"),
            "profile": r.get("profile"),
            "runtime": cfg.runtime,
            "status": r.get("status"),
            "stage": r.get("stage"),
            "error": r.get("error") or None,
        }
        _write(root, [*read(root), entry])
    except (OSError, DeployError) as exc:
        log.warning("the deployment was not recorded in this machine's history: {}", exc)


def latest(root: Path, server: str | None = None) -> dict | None:
    """The most recent successful deployment - to `server` (a host, with or
    without its user@) when given."""
    for e in reversed(read(root)):
        if e.get("status") != "deployed":
            continue
        if server is None or server in (e.get("host"), server_of(str(e.get("host")))):
            return e
    return None


def defaults(root: Path) -> set[int]:
    """Which entries (by index) are their server's default: the latest
    successful deployment to each host and dest."""
    seen: set[tuple] = set()
    out: set[int] = set()
    entries = read(root)
    for i in range(len(entries) - 1, -1, -1):
        e = entries[i]
        key = (e.get("host"), e.get("dest"))
        if e.get("status") == "deployed" and key not in seen:
            seen.add(key)
            out.add(i)
    return out
