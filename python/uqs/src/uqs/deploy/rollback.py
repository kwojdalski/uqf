"""`uqs deploy rollback`: put a server back on an earlier release, on purpose (#865).

A push rolls back by itself when a stage fails before activation. Once a
release HAS activated there was no way back but symlinks and restarts by hand
- for a wrong number found an hour later, say. This is that way back, built
from the same stages a push uses:

  1. the lock, as a push takes it;
  2. what `current` names, and which release to return to: --release, else the
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
where it began. So does an interruption - Ctrl-C in the verify wait, say -
before `current` moves, and the interruption then goes on, as a push's does
(#1664, #2175); one while `current` moves leaves the verified target running
and says what `current` names. Nothing is deleted - the release rolled back from stays in
releases/, so a push of the same artifact is refused and a rollback with
--release can return to it.
"""

from __future__ import annotations

import json

from uqs.deploy import report as deploy_report
from uqs.deploy.config import REPORT, UNREADABLE_REPORT, Config, DeployError, redact
from uqs.deploy.remote import Transport, q
from uqs.deploy.selection import Selection, started_by
from uqs.deploy.stages import Deployment
from uqs.logger import get_logger

log = get_logger()


def _report(dep: Deployment, release: str) -> dict:
    out = dep.run(
        "rollback",
        f"reading release {release}'s report",
        f"cat {q(dep.releases)}/{q(release)}/{REPORT}",
    )
    try:
        report = deploy_report.read(json.loads(out))
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
    """A start that fails is a failed verification, not an escape: the caller
    must still stop what it part-started and restore `current` (#1541)."""
    path = f"{dep.releases}/{release}"
    try:
        dep.uqs(
            path, "rollback", f"starting {what}", "start", "--profile", report["profile"],
            *report["extra_processes"],
        )  # fmt: skip
        dep.beat()
        return dep.verify(
            path, report["profile"], "rollback", Selection(processes=report["extra_processes"])
        )
    except DeployError as exc:
        return {"passed": False, "reason": str(exc)}


def _plan(dep: Deployment, cfg: Config, to: str | None) -> tuple[dict, dict, dict]:
    """(what a rollback would stop and start, current's report, the target's):
    read from the server, changing nothing."""
    current = _current(dep)
    if not current:
        raise DeployError("rollback", f"{cfg.dest} has no current release to roll back from")
    now = _report(dep, current)
    target = to or now.get("previous_release")
    if not target:
        raise DeployError(
            "rollback",
            f"release {current}'s report names no release it replaced - pass --release RELEASE",
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
    return plan, now, then


def rollback(
    cfg: Config, remote: Transport, *, to: str | None = None, dry_run: bool = False
) -> tuple[int, dict]:
    """Move the server at cfg.ssh:cfg.dest back to an earlier release.
    Returns (0 when the target is running, verified and `current`, else 1;
    what happened, as the command's document - its verdict under `rollback`:
    planned, done or failed). Printed by the command, never here (#2125)."""
    dep = Deployment(cfg, remote)
    dep.check_sudo()
    if dry_run:  # reads the lock, takes nothing: Deployment.read_lock says why
        dep.read_lock("rollback")
        return 0, {"ok": True, "rollback": "planned", **_plan(dep, cfg, to)[0]}
    dep.take_lock("rollback")
    # How far it got, for an interruption: "stop" once current's processes may
    # be going down, "start" once the target's may be coming up, "restore"
    # once current is being restored, "activate" once `current` may move.
    reached = ""
    try:
        plan, now, then = _plan(dep, cfg, to)
        current, target = plan["from"], plan["to"]
        log.info("stopping release {}'s processes", current)
        reached = "stop"  # before it: a stop that fails part-way took some down
        dep.uqs(f"{dep.releases}/{current}", "rollback", "stopping the current processes",
                "stop", *_started(now))  # fmt: skip
        dep.beat()
        log.info("starting release {}'s profile {}", target, then["profile"])
        reached = "start"
        result = _start_and_verify(dep, target, then, f"release {target}")
        dep.beat()
        if not result.get("passed"):
            reason = result.get("reason") or "verification failed"
            log.error(
                "release {} did not start or verify: {}; restoring {}", target, reason, current
            )
            reached = "restore"
            outcome = _restore(dep, plan, now, then, stop_target=True)
            failed = {"rollback": "failed", **plan, "reason": reason, "current": current}
            return 1, {"ok": False, **failed, "restore": outcome}
        reached = "activate"
        dep.activate(target)
        return 0, {"ok": True, "rollback": "done", **plan, "current": target}
    except BaseException as exc:
        # A stop that failed, or Ctrl-C in the target's verify wait: the same
        # restore a target that fails to verify gets, then the exception goes
        # on to the caller - as a push does (#1664, #2175).
        if reached:
            _recover(dep, plan, now, then, reached, exc)
        raise
    finally:
        dep.release_lock()


def _restore(dep: Deployment, plan: dict, now: dict, then: dict, *, stop_target: bool) -> str:
    """Stop what the target started (when it was started), then start and
    verify `current`'s release again: "restored" or "NOT restored - ..."."""
    current, target = plan["from"], plan["to"]
    if stop_target:
        try:
            dep.uqs(f"{dep.releases}/{target}", "rollback", "stopping the target's processes",
                    "stop", *_started(then))  # fmt: skip
        except DeployError as exc:
            log.error("stopping release {}'s processes failed: {}; restoring anyway", target,
                      redact(str(exc)))  # fmt: skip
        dep.beat()
    restored = _start_and_verify(dep, current, now, f"release {current} again")
    return "restored" if restored.get("passed") else "NOT restored - check by hand"


def _recover(dep, plan, now, then, reached: str, exc: BaseException) -> None:
    """After `exc` at `reached`: leave a running release, or say exactly why
    there is none. Logged, since the exception - not a document - goes on."""
    current, target = plan["from"], plan["to"]
    what = "FAILED" if isinstance(exc, DeployError) else "INTERRUPTED"
    why = redact(": ".join(filter(None, (type(exc).__name__, str(exc)))))
    lost = (f"current still names {current}, but neither release may be running - "
            "check by hand")  # fmt: skip
    if reached == "restore":  # a second Ctrl-C, while current was being restored
        log.error("rollback {} ({}) while restoring release {}: {}", what, why, current, lost)
        return
    if reached == "activate":
        # The target is up and verified: undoing it now would trade a running
        # release for a guess. Say what `current` names, read back.
        try:
            names = _current(dep) or "nothing"
        except Exception:  # noqa: BLE001 - the original exception is what goes on
            names = f"{current} or {target} (it could not be read back)"
        log.error("rollback {} ({}) while moving current: release {}'s processes are running "
                  "and verified; current names {}", what, why, target, names)  # fmt: skip
        return
    doing = f"stopping release {current}" if reached == "stop" else f"starting release {target}"
    log.error("rollback {} ({}) while {}: restoring release {}", what, why, doing, current)
    try:
        outcome = _restore(dep, plan, now, then, stop_target=reached == "start")
    except BaseException:
        log.error("rollback interrupted again while restoring release {}: {}", current, lost)
        raise
    if outcome == "restored":
        log.error("release {} restored - the server is where it began; current still names {}",
                  current, current)  # fmt: skip
    else:
        log.error("release {} NOT restored: {}", current, lost)


def render(doc: dict) -> str:
    """A rollback's document for a terminal."""
    start = doc["start"]
    extra = start["extra_processes"]
    starts = f"profile {start['profile']}" + (f" and {', '.join(extra)}" if extra else "")
    stops = ", ".join(doc["stop"]) or "nothing"
    verdict = doc["rollback"]
    if verdict == "planned":
        return f"would roll back from {doc['from']} to {doc['to']}: stop {stops}; start {starts}"
    if verdict == "done":
        return f"rolled back from {doc['from']} to {doc['to']} ({starts}); current is {doc['to']}"
    return (
        f"rollback from {doc['from']} to {doc['to']} FAILED: {redact(str(doc['reason']))}; "
        f"release {doc['current']} {doc['restore']}"
    )
