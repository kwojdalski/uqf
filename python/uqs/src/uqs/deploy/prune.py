"""`uqs deploy prune`: remove the old releases a server no longer needs (#866).

Every push adds releases/<id>/ with its own offline .venv, typically hundreds
of MB, and nothing else ever removes one. This keeps the newest --keep N and
removes the rest, under the deploy lock, except three kinds it never touches
whatever N is - N=0 included:

  - the release `current` names;
  - the release a rollback would return to: the `previous_release` current's
    own deploy-report.json records (`uqs deploy rollback`'s default target);
  - any release whose report still says `running`: a push that died
    mid-run, whose processes may still be up.

What each release holds is read on the server by one python3 program
(LIST_PY): name, size, and its report's status and previous_release. Only a
name shaped like a release id is ever removed, so nothing else under
releases/ can be. --dry-run lists what would go and removes nothing.
"""

from __future__ import annotations

import json
import re
import sys

from uqs.deploy.config import Config, DeployError
from uqs.deploy.remote import Transport, q
from uqs.deploy.stages import Deployment
from uqs.logger import get_logger

log = get_logger(__name__)

#: A release id as `uqs deploy build` names one: UTC build time, then the revision.
RELEASE_ID = re.compile(r"\d{8}T\d{6}Z-[0-9a-f]{12}")

#: Every release under argv[1], with `current` (argv[2]) resolved: one JSON
#: object. Sizes are what removing each would free - files, not links.
LIST_PY = r"""
import json, os, sys
root, cur = sys.argv[1], sys.argv[2]
current = os.path.basename(os.readlink(cur)) if os.path.islink(cur) else ""
out = {"current": current, "releases": []}
for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
    path = os.path.join(root, name)
    if os.path.islink(path) or not os.path.isdir(path):
        continue
    size = 0
    for d, _, files in os.walk(path):
        for f in files:
            try:
                size += os.lstat(os.path.join(d, f)).st_size
            except OSError:
                pass
    try:
        with open(os.path.join(path, "deploy-report.json")) as fh:
            report = json.load(fh)
    except (OSError, ValueError):
        report = {}
    if not isinstance(report, dict):
        report = {}
    out["releases"].append({
        "release": name,
        "bytes": size,
        "status": report.get("status", ""),
        "previous_release": report.get("previous_release"),
    })
print(json.dumps(out))
"""


def _listing(dep: Deployment) -> dict:
    out = dep.run(
        "prune",
        "listing the releases",
        f"python3 -c {q(LIST_PY)} {q(dep.releases)} {q(dep.current)}",
    )
    try:
        return json.loads(out.strip().splitlines()[-1])
    except IndexError, ValueError:
        raise DeployError("prune", "the release listing printed no result") from None


def decide(listing: dict, keep: int) -> dict:
    """Which releases stay and why, and which go - pure, so it is tested
    without a server. Releases are named by UTC time, so name order is age."""
    releases = {r["release"]: r for r in listing["releases"]}
    current = listing.get("current") or ""
    rollback_to = (releases.get(current) or {}).get("previous_release") or ""
    reasons: dict[str, str] = {}
    newest = sorted(releases)[::-1][:keep] if keep else []
    for name in newest:
        reasons[name] = f"one of the newest {keep}"
    for name, r in releases.items():
        if r.get("status") == "running":
            reasons[name] = "its report says a push is still running"
    if rollback_to in releases:
        reasons[rollback_to] = "a rollback would return to it"
    if current in releases:
        reasons[current] = "current"
    removed = [
        name for name in sorted(releases) if name not in reasons and RELEASE_ID.fullmatch(name)
    ]
    odd = sorted(n for n in releases if n not in reasons and not RELEASE_ID.fullmatch(n))
    for name in odd:
        reasons[name] = "not named like a release, so never removed"
    return {
        "kept": [{"release": n, "why": reasons[n]} for n in sorted(reasons)],
        "removed": removed,
        "freed_bytes": sum(releases[n]["bytes"] for n in removed),
    }


def prune_locked(dep: Deployment, keep: int, dry_run: bool = False) -> dict:
    """Prune with the lock already held - by `prune`, or by a push after it
    activated. Returns what stayed, what went and the bytes freed."""
    if keep < 0:
        raise DeployError("arguments", "--keep must be 0 or more")
    result = decide(_listing(dep), keep)
    if result["removed"] and not dry_run:
        dep.run(
            "prune",
            "removing old releases",
            *(f"rm -rf {q(dep.releases)}/{q(name)}" for name in result["removed"]),
        )
    for name in result["removed"]:
        log.info("{} release {}", "would remove" if dry_run else "removed", name)
    return {"prune": "planned" if dry_run else "done", **result}


def prune(
    cfg: Config, remote: Transport, *, keep: int, dry_run: bool = False, out=sys.stdout
) -> int:
    """Remove the server's releases beyond the newest `keep`, under the lock."""
    dep = Deployment(cfg, remote)
    dep.check_sudo()
    dep.take_lock("prune")
    try:
        result = prune_locked(dep, keep, dry_run)
    finally:
        dep.release_lock()
    print(json.dumps(result, indent=2), file=out)
    return 0
