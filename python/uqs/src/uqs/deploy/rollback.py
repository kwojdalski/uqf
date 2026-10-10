"""`uqs deploy rollback`: put a server back on an earlier release, on purpose (#865).

A push rolls back by itself when a stage fails before activation. Once a
release HAS activated there was no way back but symlinks and restarts by hand
- for a wrong number found an hour later, say. This is that way back, built
from the same stages a push uses:

  1. the lock, as a push takes it;
  2. what `current` names, and which release to return to: --to, else the
     one `current`'s own deploy-report.json says it replaced
     (`previous_release`) - recorded by the push that activated it, so this
     never guesses from directory order;
  3. stop `current`'s processes - the profile and sidecar processes its
     report lists - from its own release;
  4. start the target's, from the TARGET's release, with its own deploy.env;
  5. the target's own verifier must pass (verify_command, which also reads a
     release from before `uqs deploy verify` existed);
  6. `current` moves to the target in one rename.

If the target fails to start or verify, its processes are stopped, `current`'s
are started again and verified, and `current` never moves: the server ends
where it began. Nothing is deleted - the release rolled back from stays in
releases/, so a push of the same artifact is refused and a rollback with
--to can return to it.
"""

from __future__ import annotations

import json
import sys

from uqs.deploy.config import REPORT, UNREADABLE_REPORT, Config, DeployError
from uqs.deploy.remote import Transport, q
from uqs.deploy.selection import Selection, started_by
from uqs.deploy.stages import Deployment
from uqs.logger import get_logger

log = get_logger(__name__)


def _report(dep: Deployment, release: str) -> dict:
    out = dep.run(
        "rollback",
        f"reading release {release}'s report",
        f"cat {q(dep.releases)}/{q(release)}/{REPORT}",
    )
    try:
        report = json.loads(out)
        return {
            **report,
            "profile": report["profile"],
            "processes": [p["process"] for p in report["processes"]],
            "extra_processes": list(report.get("extra_processes", [])),
        }
    except UNREADABLE_REPORT:
        raise DeployError(
            "rollback", f"release {release} has no readable {REPORT} to say what it runs"
        ) from None


def _started(report: dict) -> list[str]:
    """What a release's start ran: its verified processes - or, in a report
    that names none, its profile resolved - and its extra processes (#1040)."""
    procs = report["processes"] or started_by(report["profile"], [])
    return list(dict.fromkeys([*procs, *report["extra_processes"]]))


def _current(dep: Deployment) -> str:
    """The release `current` names, read under the lock; "" when none."""
    out = dep.run(
        "rollback",
        "reading what current names",
        f'if [ -L {q(dep.current)} ]; then basename "$(readlink {q(dep.current)})"; fi',
    )
    return out.strip().splitlines()[-1] if out.strip() else ""


def _start_and_verify(dep: Deployment, release: str, report: dict, what: str) -> dict:
    path = f"{dep.releases}/{release}"
    dep.uqs(
        path, "rollback", f"starting {what}", "start", "--profile", report["profile"],
        *report["extra_processes"],
    )  # fmt: skip
    try:
        return dep.verify(
            path, report["profile"], "rollback", Selection(processes=report["extra_processes"])
        )
    except DeployError as exc:
        return {"passed": False, "reason": str(exc)}


def rollback(
    cfg: Config, remote: Transport, *, to: str | None = None, dry_run: bool = False, out=sys.stdout
) -> int:
    """Move the server at cfg.host:cfg.dest back to an earlier release.
    Returns 0 when the target is running, verified and `current`."""
    dep = Deployment(cfg, remote)
    dep.check_sudo()
    dep.take_lock("rollback")
    try:
        current = _current(dep)
        if not current:
            raise DeployError("rollback", f"{cfg.dest} has no current release to roll back from")
        now = _report(dep, current)
        target = to or now.get("previous_release")
        if not target:
            raise DeployError(
                "rollback",
                f"release {current}'s report names no release it replaced - pass --to RELEASE",
            )
        if target == current:
            raise DeployError("rollback", f"{target} is already current")
        then = _report(dep, target)
        plan = {
            "from": current,
            "to": target,
            "stop": _started(now),
            "start": {"profile": then["profile"], "extra_processes": then["extra_processes"]},
        }
        if dry_run:
            print(json.dumps({"rollback": "planned", **plan}, indent=2), file=out)
            return 0
        log.info("stopping release {}'s processes", current)
        dep.uqs(f"{dep.releases}/{current}", "rollback", "stopping the current processes",
                "stop", *_started(now))  # fmt: skip
        dep.beat()
        log.info("starting release {}'s profile {}", target, then["profile"])
        result = _start_and_verify(dep, target, then, f"release {target}")
        dep.beat()
        if not result.get("passed"):
            reason = result.get("reason") or "verification failed"
            log.error("release {} did not verify: {}; restoring {}", target, reason, current)
            dep.uqs(f"{dep.releases}/{target}", "rollback", "stopping the target's processes",
                    "stop", *_started(then))  # fmt: skip
            restored = _start_and_verify(dep, current, now, f"release {current} again")
            outcome = "restored" if restored.get("passed") else "NOT restored - check by hand"
            failed = {"rollback": "failed", **plan, "reason": reason, "current": current}
            print(json.dumps({**failed, "restore": outcome}, indent=2), file=out)
            return 1
        dep.activate(target)
        print(json.dumps({"rollback": "done", **plan, "current": target}, indent=2), file=out)
        return 0
    finally:
        dep.release_lock()
