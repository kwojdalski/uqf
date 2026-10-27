"""A deployment end to end: the run, and the rollback when a stage fails, or
the push is interrupted, after the previous release was stopped. What
--dry-run shows is plan.py's."""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime

from uqs.deploy import bundled, components, converted_override, fetch, hdb, overrides
from uqs.deploy.artifact import Artifact, ReleaseError, release_runtime
from uqs.deploy.config import Config, DeployError, load_artifact, redact
from uqs.deploy.plan import plan
from uqs.deploy.prune import prune_locked
from uqs.deploy.remote import Transport
from uqs.deploy.report import Report
from uqs.deploy.selection import Selection, select_jobs, started_by
from uqs.deploy.stages import Deployment
from uqs.logger import get_logger
from uqs.paths import repo_root
from uqs.stack.converted import ATTEMPT_VAR

log = get_logger()


def deploy(cfg: Config, remote: Transport, *, out=sys.stdout, on_report=None) -> int:
    local = fetch.resolve(cfg.artifact, repo=cfg.release_repo, root=repo_root())
    log.info("checking {}", local)
    pkg = load_artifact(local)
    if pkg.manifest["dirty"] and not cfg.allow_dirty:
        raise DeployError(
            "artifact",
            f"{pkg.path.name} was built from uncommitted changes (dirty: true) - deploy one "
            "built from a commit, ideally by CI, or pass --allow-dirty",
        )
    exceptions = overrides.for_artifact(cfg, pkg)
    rid = pkg.release
    dep = Deployment(cfg, remote, pkg.manifest["target"], rid)
    try:
        dep.runtime = release_runtime(pkg.manifest)
    except ReleaseError as exc:
        raise DeployError(exc.stage, str(exc)) from None
    exceptions += bundled.attach(cfg, dep, pkg)
    if cfg.runtime and cfg.runtime != dep.runtime:
        raise DeployError(
            "artifact",
            f"target {cfg.target} runs the {cfg.runtime} runtime, but {pkg.path.name} was built "
            f"for {dep.runtime} - build it with `uqs --runtime {cfg.runtime} deploy build`",
        )
    dep.selection = select_jobs(pkg.manifest, cfg.jobs)
    report = Report(
        release=rid,
        revision=pkg.manifest["revision"],
        artifact={"name": pkg.path.name, "sha256": pkg.sha256},
        dirty=pkg.manifest["dirty"],
        host=cfg.host,
        dest=cfg.dest,
        profile=cfg.profile,
        jobs=dep.selection.jobs,
        extra_processes=dep.selection.processes,
        bundles=dep.selection.bundles,
        live=cfg.live,
    )

    log.info("preflight on {}{}", cfg.host, f" as {cfg.remote_user}" if cfg.remote_user else "")
    dep.check_sudo()
    facts = dep.preflight()
    report.previous_release = facts.get("current")
    report.qversion = facts.get("qversion") or None
    python = pkg.manifest["target"]["python"]
    shipped = bundled.records(dep)
    report.components = components.observed(cfg, facts, python, f"{dep.releases}/{rid}", shipped)
    exceptions += overrides.for_q(cfg, facts.get("qversion", ""))
    report.exceptions = exceptions
    for e in exceptions:
        if e["applied"]:
            log.warning("--{}: {} found, {} required: {}", e["override"], e["observed"],
                        e["requirement"], e["reason"])  # fmt: skip
    if cfg.dry_run:
        facts = {**facts, "hdb": hdb.planned(dep, pkg.manifest)}
        print(plan(cfg, pkg, rid, facts, dep, exceptions, report.components), file=out)
        return 0
    return _run(dep, cfg, pkg, rid, report, facts, out, on_report)


def _run(
    dep: Deployment,
    cfg: Config,
    pkg: Artifact,
    rid: str,
    report: Report,
    facts: dict,
    out,
    on_report=None,
) -> int:
    dep.take_lock()
    release: str | None = None
    try:
        state = dep.locked_state()
    except DeployError:
        dep.release_lock()
        raise
    previous = state.get("current")
    if previous != facts.get("current"):
        log.warning("current changed since preflight: now {}", previous or "none")
    report.previous_release = previous
    prev_profile: str | None = None
    outage = 0.0
    prev_procs: list[str] = []
    prev_extra: list[str] = []
    stopped_previous = False
    started = False
    try:
        bundled.install(dep, rid, report)
        log.info("transferring release {}", rid)
        release = dep.transfer(pkg, rid)
        dep.beat()
        log.info("preparing the release environment")
        dep.prepare(release, pkg)
        dep.beat()
        log.info("checking the shared HDB against the release's schema")
        report.hdb = hdb.check(dep, release)
        dep.beat()
        log.info("offline smoke test")
        dep.smoke(release)
        dep.beat()
        report.checks["smoke"] = "ok"
        start_env: dict[str, str] = {}
        if cfg.accept_converted_release is not None:
            log.warning(
                "--accept-converted-release: starting release {} on q {}: {}",
                rid,
                facts.get("qversion") or "of unknown version",
                cfg.accept_converted_release,
            )
            record = converted_override.write(dep, release, facts)
            report.exceptions.append(overrides.converted(record))
            start_env[ATTEMPT_VAR] = record["attempt"]
        # Before anything is stopped or started: a push killed outright from
        # here on leaves a release whose report says `running`, which prune
        # will not remove and status reports as a push in progress (#1664).
        dep.write_report(release, report)
        if previous:
            prev_profile, prev_procs, prev_extra = dep.previous_processes(previous)
            log.info("stopping release {}'s processes", previous)
            # Set before the stop, not after: a stop that fails part-way has
            # still taken some of them down, and rollback must start them.
            stopped_previous = True
            # The outage starts here: clients of the old processes lose them
            # as the stop runs, and get the new ones once they verify.
            outage = time.monotonic()
            report.downtime = {"stopped_at": _now(), "verified_at": None, "seconds": None}
            dep.uqs(
                f"{dep.releases}/{previous}",
                "restart",
                "stopping the previous processes",
                "stop",
                *prev_procs,
            )
        dep.beat()
        dep.ports_free(release)
        log.info("starting profile {}", cfg.profile)
        started = True
        dep.uqs(
            release,
            "start",
            "starting the profile",
            "start",
            "--profile",
            cfg.profile,
            *dep.selection.processes,
            env=start_env,
        )
        dep.beat()
        log.info("verifying every process answers (up to {}s)", cfg.verify_timeout)
        result = dep.verify(release)
        dep.beat()
        report.processes = result.get("processes", [])
        if not result.get("passed"):
            raise DeployError("verify", result.get("reason") or "verification failed")
        report.checks["verify"] = "ok"
        if report.downtime is not None:
            report.downtime["verified_at"] = _now()
            report.downtime["seconds"] = round(time.monotonic() - outage, 1)
            log.info("downtime: {}s from stopping {} to {} verified", report.downtime["seconds"],
                     previous, rid)  # fmt: skip
        if cfg.live_check:
            log.info("checking {} live, before activation", ", ".join(cfg.live_check))
            dep.live_check(release)
            dep.beat()
            report.checks["live-check"] = "ok"
        if cfg.soak:
            log.info("soaking for {}s: every streaming job must beat, none failing", cfg.soak)
            report.soak = dep.soak(release, [p["process"] for p in report.processes])
            if not report.soak["passed"]:
                raise DeployError("soak", report.soak["reason"])
            report.checks["soak"] = "ok"
        # Recorded BEFORE activation, and fatal if it cannot be: the next
        # upgrade reads this report to know what to stop.
        report.status = "deployed"
        report.stage = "done"
        dep.write_report(release, report, required=True)
        dep.activate(rid)
        if cfg.keep is not None:
            dep.beat()
            report.pruned = _prune_after(dep, cfg.keep)
    except DeployError as exc:
        report.status = "failed"
        report.stage = exc.stage
        report.error = str(exc)
        log.error("FAILED at {}: {}", exc.stage, redact(str(exc)))
        report.rollback = _rollback(
            dep, release, started, stopped_previous, previous, (prev_profile, prev_extra)
        )
    except BaseException as exc:
        # Ctrl-C in the verify wait, or a bug's KeyError: the previous release
        # may already be down, so it is rolled back as a failed stage is, and
        # then the interruption goes on to the caller (#1664).
        report.status = "failed"
        report.stage = "interrupted"
        report.error = ": ".join(filter(None, (type(exc).__name__, str(exc))))
        log.error("INTERRUPTED: {}", redact(report.error))
        report.rollback = _rollback(
            dep, release, started, stopped_previous, previous, (prev_profile, prev_extra)
        )
        raise
    finally:
        dep.write_report(release, report)
        dep.discard_staging(rid)
        dep.release_lock()
        if on_report:  # the workstation's deployment history (#956)
            on_report(report)
    print(json.dumps(report.as_dict(), indent=2), file=out)
    return 0 if report.status == "deployed" else 1


def _prune_after(dep: Deployment, keep: int) -> dict:
    """--keep, once the release is current and the lock still held. The
    deployment has succeeded by then, so a prune that fails is reported, not
    rolled back."""
    try:
        return prune_locked(dep, keep)
    except DeployError as exc:
        log.warning("the deployment succeeded, but pruning old releases failed: {}", exc)
        return {"prune": "failed", "error": redact(str(exc))}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _rollback(dep, release, started, stopped_previous, previous, prev) -> str:
    """Stop what the failed release started, and bring the previous one back:
    its profile and the sidecar processes it ran beside it (`prev`)."""
    prev_profile, prev_extra = prev
    notes = []
    if started and release:
        try:
            dep.uqs(release, "rollback", "stopping the new processes", "stop",
                    *started_by(dep.cfg.profile, dep.selection.processes))  # fmt: skip
            notes.append("stopped the new release's processes")
        except DeployError as exc:
            notes.append(f"FAILED to stop the new release's processes: {exc}")
    if stopped_previous and previous and prev_profile:
        prev_release = f"{dep.releases}/{previous}"
        try:
            dep.uqs(
                prev_release,
                "rollback",
                "restarting the previous processes",
                "start",
                "--profile",
                prev_profile,
                *prev_extra,
            )
        except DeployError as exc:
            notes.append(f"FAILED to restart release {previous}: {exc}")
        else:
            # A start command that returned is not a recovery: the previous
            # release's own verifier, with its own deploy.env, must pass.
            try:
                sel = Selection(processes=prev_extra)
                result = dep.verify(prev_release, prev_profile, "rollback", sel)
            except DeployError as exc:
                result = {"passed": False, "reason": str(exc)}
            if result.get("passed"):
                notes.append(f"restarted and verified release {previous}'s profile {prev_profile}")
            else:
                notes.append(
                    f"FAILED to verify release {previous} after restarting {prev_profile}: "
                    + str(result.get("reason") or "verification failed")
                )
    if previous:
        notes.append(f"current still names {previous}")
    return "; ".join(notes) or "nothing to roll back"
