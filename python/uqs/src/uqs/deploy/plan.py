"""The plan `uqs deploy push --dry-run` shows: the overrides asked for, the
payload, every step and where each setting came from - split from driver.py,
which runs it."""

from __future__ import annotations

from uqs.deploy import kafka, overrides
from uqs.deploy.artifact import Artifact
from uqs.deploy.config import Config
from uqs.deploy.selection import Selection, smoke_args, verify_args
from uqs.deploy.stages import Deployment


def plan(
    cfg: Config,
    pkg: Artifact,
    rid: str,
    facts: dict[str, str],
    dep: Deployment,
    exceptions: list[dict] | None = None,
) -> str:
    """What --dry-run shows: the overrides asked for, first, then the payload,
    the commands and the planned restart."""
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
        + f"{pkg.manifest['python']['wheels']} wheels{kafka.plan_note(release, pkg.manifest)}",
        "hdb       " + facts.get("hdb", "not judged"),
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
        *(
            [
                f"prune     all but the newest {cfg.keep} releases - never current, the one "
                "a rollback returns to, or one still running"
            ]
            if cfg.keep is not None
            else []
        ),
    ]
    size = pkg.path.stat().st_size
    t = pkg.manifest["target"]
    return "\n".join(
        [
            *overrides.plan_lines(cfg, exceptions or []),
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
            *_source_lines(cfg),
            "planned:",
            *[f"  {s}" for s in steps],
        ]
    )


def _source_lines(cfg: Config) -> list[str]:
    """Where each setting came from, when a target supplied any (#873)."""
    if not cfg.target:
        return []
    by: dict[str, list[str]] = {}
    for key, source in sorted(cfg.sources.items()):
        by.setdefault(source, []).append(key)
    return [f"settings: {', '.join(keys)} from {source}" for source, keys in sorted(by.items())]


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
