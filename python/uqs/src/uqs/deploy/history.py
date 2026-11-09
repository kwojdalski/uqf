"""This workstation's deployment history, and the default deployment per
server (#956).

Every `uqs deploy push` that gets as far as running - deployed or failed -
appends one entry to deploy_history.json, beside deploy_targets.toml (or
wherever $UQS_DEPLOY_HISTORY names). `uqs deploy history` reads it. The server
keeps its own truth in each release's deploy-report.json; this is the
operator's record of what THEY pushed and where, so it needs no ssh to read.

A push to a host and dest that no target names yet registers one, named
<server>-<dest's last folder>, so the next `uqs --target` needs no `uqs target
add`. A target that already points there is reused, never duplicated. The
registration is a change like `uqs target add`'s (deploy/target_changes.py):
validated, written atomically, in the change log, and its block printed;
`uqs deploy push --no-register` skips it (#1756). A release copied onto a
server by hand and taken over with `uqs target adopt` is recorded here too,
as "adopted", so it is that server's default like a deployment.

THE DEFAULT PER SERVER is that server's most recent successful deployment:
`uqs --target HOST` (a host, rather than a target's name) resolves to it, and
`uqs target shell` with no name opens the most recent one of all.

Recording never fails a deployment: the deployment has already happened, and
a history that could not be written is worth a warning, not an error.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from uqs.cells import Shown
from uqs.deploy import inspection, target_changelog, target_changes, targets
from uqs.deploy.config import Config, DeployError
from uqs.logger import get_logger
from uqs.paths import UqsError, UqsPaths
from uqs.stack import listing

log = get_logger()

HISTORY = "deploy_history.json"
HISTORY_ENV = "UQS_DEPLOY_HISTORY"
#: Oldest entries go first past this many; a list, not an archive.
KEEP = 1000
#: The statuses that leave a release running on its server: what a server's
#: default and a target's "last deployed" are chosen from.
INSTALLED = frozenset({"deployed", target_changelog.ADOPTED})


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
        entries = json.loads(path.read_text(encoding="utf-8")).get("deployments", [])
    except UnicodeDecodeError as exc:
        raise DeployError(
            "arguments", f"{path} is not a deployment history: not UTF-8 ({exc})"
        ) from None
    except (json.JSONDecodeError, AttributeError) as exc:
        raise DeployError("arguments", f"{path} is not a deployment history: {exc}") from None
    # Refused, not iterated: `{"deployments": 5}` was a TypeError, and a 500
    # on the Services view (#2026).
    if not isinstance(entries, list):
        raise DeployError(
            "arguments",
            f"{path} is not a deployment history: deployments is not a list "
            f"({type(entries).__name__})",
        )
    return [_ssh(e) for e in entries if isinstance(e, dict)]


def _ssh(entry: dict) -> dict:
    """An entry with its ssh destination under `ssh`, as every reader takes it
    (#1888): an entry written before then holds it under `host`."""
    if "host" in entry and "ssh" not in entry:
        entry["ssh"] = entry.pop("host")
    return entry


@contextmanager
def _updating(root: Path) -> Iterator[list[dict]]:
    """The history's entries to change in place, written back on exit.

    Two pushes from one workstation each read, append and write: under one
    flock, through a temp file of their own, so neither loses the other's
    entry nor publishes a half-written file (#2578)."""
    path = history_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name(path.name + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            entries = read(root)
            before = list(entries)
            yield entries
            if entries != before:
                text = json.dumps({"deployments": entries[-KEEP:]}, indent=2) + "\n"
                target_changes.write_atomically(path, text)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def server_of(host: str) -> str:
    """The machine a destination names: `svc@uat.example` -> `uat.example`."""
    return host.rsplit("@", 1)[-1]


def _target_for(root: Path, cfg: Config, register: bool, say: Callable[[str], None]) -> str | None:
    """The target naming cfg's host and dest, registering one if none does
    (unless `register` is off), and saying so through `say`."""
    if cfg.target:
        return cfg.target
    declared = targets.read(root) if targets.declaration_path(root).is_file() else {}
    for name, t in sorted(declared.items()):
        if t.get("ssh") == cfg.ssh and t.get("dest") == cfg.dest:
            return name
    if not register:
        return None
    # <server>-<dest's last folder>: never the bare server name, which means
    # that server's latest deployment, whichever dest it went to.
    raw = f"{server_of(cfg.ssh)}-{cfg.dest.rstrip('/').rsplit('/', 1)[-1]}"
    base = re.sub(r"[^a-z0-9_-]+", "-", raw.lower()).strip("-") or "server"
    if not base[0].isalpha():
        base = f"host-{base}"
    name, n = base[:60], 2
    while name in declared:
        name, n = f"{base[:56]}-{n}", n + 1
    entry = {"ssh": cfg.ssh, "dest": cfg.dest, "remote_user": cfg.remote_user}
    done = target_changes.add(
        root, name, entry, f"uqs deploy push to {cfg.ssh}:{cfg.dest}",
        kind=target_changelog.REGISTERED,
    )  # fmt: skip
    number = len(target_changelog.read_log(root))
    block = done.entry["tables"][0]["after"] if done.entry else ""
    say(
        f"registered target {name} in {done.path} (`uqs target log {name}`: change "
        f"{number}; `uqs deploy push --no-register` skips this):\n{block.rstrip()}\n"
        f"  try: uqs --target {name} status"
    )
    return name


def rename_target(root: Path, old: str, new: str) -> None:
    """The deployments recorded under target `old` recorded under `new`
    (`uqs target rename`), so its history follows it."""
    with _updating(root) as entries:
        for i, e in enumerate(entries):
            if e.get("target") == old:
                entries[i] = {**e, "target": new}


def record_adopted(
    root: Path, name: str, host: str, dest: str, user: str | None, release: str
) -> None:
    """`uqs target adopt --apply`: the release it linked as current, as that
    server's latest installation."""
    entry = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "target": name, "ssh": host, "dest": dest, "remote_user": user,
        "release": release, "status": target_changelog.ADOPTED, "stage": None,
    }  # fmt: skip
    with _updating(root) as entries:
        entries.append(entry)


class Reported(Protocol):
    """What record needs of a deployment's report (deploy/stages.py's Report)."""

    def as_dict(self) -> dict: ...


def record(
    root: Path,
    cfg: Config,
    report: Reported,
    now: datetime | None = None,
    *,
    register: bool = True,
    say: Callable[[str], None] = log.info,
) -> None:
    """Append this push to the history; register its target when it
    deployed, unless `register` is off."""
    r = report.as_dict()
    name = cfg.target
    if r.get("status") == "deployed":
        try:
            name = _target_for(root, cfg, register, say)
        except (OSError, DeployError) as exc:
            log.warning("the server was not registered as a target: {}", exc)
    try:
        entry = {
            "at": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
            "target": name,
            "ssh": cfg.ssh,
            "dest": cfg.dest,
            "remote_user": cfg.remote_user,
            "release": r.get("release"),
            "revision": r.get("revision"),
            # the bytes deployed, as the server checked them (#1557)
            "artifact": r.get("artifact"),
            "profile": r.get("profile"),
            "runtime": cfg.runtime,
            "status": r.get("status"),
            "stage": r.get("stage"),
            "error": r.get("error") or None,
            # the policy overrides this push asked for (#1557), as the report has them
            "exceptions": r.get("exceptions") or [],
            # what it ran on: each component's mode, path and observed version
            "components": r.get("components") or {},
        }
        with _updating(root) as entries:
            entries.append(entry)
    except (OSError, DeployError) as exc:
        log.warning("the deployment was not recorded in this machine's history: {}", exc)


def latest(root: Path, server: str | None = None) -> dict | None:
    """The most recent successful deployment - to `server` (a host, with or
    without its user@) when given."""
    for e in reversed(read(root)):
        if e.get("status") not in INSTALLED:
            continue
        if server is None or server in (e.get("ssh"), server_of(str(e.get("ssh")))):
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
        key = (e.get("ssh"), e.get("dest"))
        if e.get("status") in INSTALLED and key not in seen:
            seen.add(key)
            out.add(i)
    return out


def target_rows(root: Path) -> list[dict[str, Any]]:
    """`uqs target list`: every declared target - where it is, as whom, its
    labels - and the release last deployed there from this machine. The
    environment column appears only when a target names one (#1557), and the
    inspection column only when a target is in inspection mode (#2032)."""
    if not targets.declaration_path(root).is_file():
        return []
    declared = targets.read(root)
    deployed = {
        (e.get("ssh"), e.get("dest")): e for e in read(root) if e.get("status") in INSTALLED
    }
    envs = any(targets.ENVIRONMENT in t for t in declared.values())
    inspected = any(inspection.is_on(t) for t in declared.values())
    rows = []
    for name, t in sorted(declared.items()):
        last = deployed.get((t.get("ssh"), t.get("dest")))
        env = {"environment": str(t.get(targets.ENVIRONMENT) or "")} if envs else {}
        if inspected:
            env["inspection"] = inspection.is_on(t)
        rows.append(
            {
                "target": name,
                **env,
                "ssh": str(t.get("ssh", "")),
                "dest": str(t.get("dest", "")),
                "remote_user": str(t.get("remote_user") or ""),
                "labels": {str(k): str(v) for k, v in (t.get("labels") or {}).items()},
                # {release, at} in a document; "release (at)" in the table (uqs.cells)
                "last_deployed": Shown(
                    {"release": last["release"], "at": last["at"]},
                    f"{last['release']} ({last['at']})",
                )
                if last
                else None,
            }
        )
    return rows


def _list_targets(paths: UqsPaths, base_port: int | None) -> list[dict[str, Any]]:
    try:
        return target_rows(paths.repo_root)
    except DeployError as exc:
        raise UqsError(str(exc)) from exc


#: `uqs target list` (#1556). Registered from here, not in stack/listing.py,
#: because stack/ sits below deploy/ and may not read a deploy declaration.
listing.LISTABLE_KINDS["targets"] = _list_targets
