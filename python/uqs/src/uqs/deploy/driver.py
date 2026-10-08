"""A deployment end to end: the plan --dry-run shows, the run, and the
rollback when a stage fails after the previous release was stopped."""

from __future__ import annotations

import json
import sys

from uqs.deploy import fetch
from uqs.deploy.artifact import Artifact
from uqs.deploy.config import Config, DeployError, load_artifact, redact
from uqs.deploy.remote import Transport
from uqs.deploy.selection import Selection, select_jobs, smoke_args, verify_args
from uqs.deploy.stages import Deployment, Report
from uqs.logger import get_logger
from uqs.paths import repo_root

log = get_logger(__name__)


def plan(cfg: Config, pkg: Artifact, rid: str, facts: dict[str, str], dep: Deployment) -> str:
    """What --dry-run shows: the payload, the commands, the planned restart."""
    previous = facts.get("current")
    restart = (
        f"stop release {previous}'s processes, then start {rid}'s on the same ports"
        if previous
        else "nothing running to replace"
    )
    release = f"{dep.releases}/{rid}"
    sel = dep.selection
    exported = ", ".join(line.split("=", 1)[0].removeprefix("export ") for line in dep.env_lines())
    steps = [
        "identity  "
        + (
            f"ssh/scp as {cfg.host}; every step below as {cfg.remote_user} "
            f"(sudo -n -iu {cfg.remote_user})"
            if cfg.remote_user
            else f"ssh/scp and every step as {cfg.host}"
        ),
        "q         "
        + f"QHOME={facts.get('qhome', '?')} ({facts.get('qhome_from', '?')}), "
        + f"QCMD={facts.get('qcmd', '?')} ({facts.get('qcmd_from', '?')}) - "
        + "resolved and run once in preflight",
        "torq      "
        + f"TORQHOME={cfg.torq_home or '(vendored)'}, launcher "
        + (cfg.torq_launcher or "$TORQHOME/torq.sh"),
        "lock      " + f"mkdir {dep.lock}",
        "transfer  "
        + (
            f"scp {pkg.path.name} to a private upload directory, then {cfg.remote_user} "
            f"copies it into {cfg.dest}/staging/{rid}/ (sha256 {pkg.sha256})"
            if cfg.remote_user
            else f"scp {pkg.path.name} {cfg.host}:{cfg.dest}/staging/{rid}/ (sha256 {pkg.sha256})"
        ),
        "prepare   "
        + f"{release}: deploy.env ({exported}); offline install of "
        + f"{pkg.manifest['python']['wheels']} wheels",
        "smoke     "
        + " ".join(["q scripts/deploy_smoke.q", *smoke_args(cfg.profile, sel)])
        + f" (timeout {cfg.smoke_timeout}s)",
        "restart   " + restart,
        "ports     "
        + " ".join(
            [
                f"uqs deploy verify --profile {cfg.profile} --ports-free",
                *verify_args(Selection(processes=sel.processes), False),
            ]
        ),
        "start     " + " ".join(["uqs start --profile", cfg.profile, *sel.processes]),
        "verify    "
        + " ".join(
            [
                f"uqs deploy verify --profile {cfg.profile} --deadline {cfg.verify_timeout}",
                *verify_args(sel, cfg.live),
            ]
        ),
        *(
            [
                "live-chk  uqs config sources check "
                + " ".join(cfg.live_check)
                + f" --timeout {cfg.live_check_timeout}"
                + (f" (ODBC from {cfg.odbc_home})" if cfg.odbc_home else "")
            ]
            if cfg.live_check
            else []
        ),
        *(
            [
                f"soak      {cfg.soak}s of data, then every started streaming job must have "
                "beaten with no batch failing (stream_health)"
            ]
            if cfg.soak
            else []
        ),
        "activate  " + f"{dep.current} -> releases/{rid}",
    ]
    size = pkg.path.stat().st_size
    t = pkg.manifest["target"]
    return "\n".join(
        [
            f"release {rid}: revision {pkg.manifest['revision']}"
            + (" (with uncommitted changes)" if pkg.manifest["dirty"] else ""),
            f"artifact: {pkg.path.name}, {len(pkg.files)} files, {size} bytes compressed, "
            f"sha256 {pkg.sha256}",
            f"target: {t['os']}/{t['arch']}, Python {t['python']} - "
            f"the server reports {facts.get('os', '?')}/{facts.get('arch', '?')}, "
            f"Python {facts.get('python', '?')}",
            f"data: {cfg.data_root} ({facts.get('data', 'unknown')}"
            + (", created by --init-data" if facts.get("data") == "absent" else "")
            + ")",
            *_selection_lines(sel, cfg.live),
            "planned:",
            *[f"  {s}" for s in steps],
        ]
    )


def _selection_lines(sel: Selection, live: bool) -> list[str]:
    """The bundles, the jobs and the processes they add, for the plan."""
    lines = [
        f"bundle: {name} {b['version']}"
        + (f" ({b['revision']['commit'][:12]})" if b.get("revision") else " (no revision)")
        for name, b in sel.bundles.items()
    ]
    if sel.jobs:
        lines.append(
            f"jobs: {', '.join(sel.jobs)} -> beside the profile: {' '.join(sel.processes)}"
        )
    elif sel.bundles:
        lines.append("jobs: none selected (--jobs) - no sidecar job starts")
    if sel.workers:
        lines.append(f"workers installed, never run: {', '.join(sel.workers)}")
    lines.append(
        "sources: "
        + ("live only - a missing credential is refused (--live)" if live else "a source with "
           "no credential reads its fixture (pass --live to refuse instead)")
    )  # fmt: skip
    return lines


def deploy(cfg: Config, remote: Transport, *, out=sys.stdout) -> int:
    local = fetch.resolve(cfg.artifact, repo=cfg.release_repo, root=repo_root())
    log.info("checking {}", local)
    pkg = load_artifact(local)
    if pkg.manifest["dirty"] and not cfg.allow_dirty:
        raise DeployError(
            "artifact",
            f"{pkg.path.name} was built from uncommitted changes (dirty: true) - deploy one "
            "built from a commit, ideally by CI, or pass --allow-dirty",
        )
    rid = pkg.release
    dep = Deployment(cfg, remote, pkg.manifest["target"], rid)
    dep.selection = select_jobs(pkg.manifest, cfg.jobs)
    dep.runtime = pkg.manifest.get("runtime", dep.runtime)
    report = Report(
        release=rid,
        revision=pkg.manifest["revision"],
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
    if cfg.dry_run:
        print(plan(cfg, pkg, rid, facts, dep), file=out)
        return 0
    return _run(dep, cfg, pkg, rid, report, facts, out)


def _run(
    dep: Deployment,
    cfg: Config,
    pkg: Artifact,
    rid: str,
    report: Report,
    facts: dict,
    out,
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
    prev_procs: list[str] = []
    prev_extra: list[str] = []
    stopped_previous = False
    started = False
    try:
        log.info("transferring release {}", rid)
        release = dep.transfer(pkg, rid)
        dep.beat()
        log.info("preparing the release environment")
        dep.prepare(release, pkg)
        dep.beat()
        log.info("offline smoke test")
        dep.smoke(release)
        dep.beat()
        report.checks["smoke"] = "ok"
        if previous:
            prev_profile, prev_procs, prev_extra = dep.previous_processes(previous)
            log.info("stopping release {}'s processes", previous)
            # Set before the stop, not after: a stop that fails part-way has
            # still taken some of them down, and rollback must start them.
            stopped_previous = True
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
        )
        dep.beat()
        log.info("verifying every process answers (up to {}s)", cfg.verify_timeout)
        result = dep.verify(release)
        dep.beat()
        report.processes = result.get("processes", [])
        if not result.get("passed"):
            raise DeployError("verify", result.get("reason") or "verification failed")
        report.checks["verify"] = "ok"
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
    except DeployError as exc:
        report.status = "failed"
        report.stage = exc.stage
        report.error = str(exc)
        log.error("FAILED at {}: {}", exc.stage, redact(str(exc)))
        report.rollback = _rollback(
            dep, release, started, stopped_previous, previous, (prev_profile, prev_extra)
        )
    finally:
        dep.write_report(release, report)
        dep.discard_staging(rid)
        dep.release_lock()
    print(json.dumps(report.as_dict(), indent=2), file=out)
    return 0 if report.status == "deployed" else 1


def _rollback(dep, release, started, stopped_previous, previous, prev) -> str:
    """Stop what the failed release started, and bring the previous one back:
    its profile and the sidecar processes it ran beside it (`prev`)."""
    prev_profile, prev_extra = prev
    notes = []
    if started and release:
        try:
            dep.uqs(release, "rollback", "stopping the new processes", "stop", "all")
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
