"""Sidecar jobs (#800): what `uqs deploy push --jobs` resolves to against the
artifact's bundles, and how a release is asked to verify that selection."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from uqs.deploy.config import DeployError
from uqs.deploy.remote import q
from uqs.model import profiles


@dataclass
class Selection:
    """What --jobs resolves to against the artifact's bundles."""

    #: the streaming jobs asked for
    jobs: list[str] = field(default_factory=list)
    #: their processes and every uqf process they need, beside the profile
    processes: list[str] = field(default_factory=list)
    #: every bundle's plant tables, which stp1 must carry
    tables: list[str] = field(default_factory=list)
    #: bounded workers the artifact installs and the deployment never runs
    workers: list[str] = field(default_factory=list)
    #: name -> version and revision, for the report
    bundles: dict[str, dict] = field(default_factory=dict)


def select_jobs(manifest: dict, wanted: Sequence[str]) -> Selection:
    """The processes --jobs starts, or a refusal naming what can be chosen."""
    bundles = manifest.get("bundles") or {}
    if wanted and not bundles:
        raise DeployError(
            "arguments",
            "--jobs selects sidecar jobs, and this artifact carries no bundle - "
            "build it with `uqs deploy build --bundle`",
        )
    found: dict[str, dict] = {}
    sel = Selection()
    for name, entry in sorted(bundles.items()):
        sel.bundles[name] = {"version": entry.get("version"), "revision": entry.get("revision")}
        sel.tables += entry.get("tables", [])
        for job in entry.get("jobs", []):
            if "name" not in job:
                continue
            found[job["name"]] = job
            if job["kind"] == "worker":
                sel.workers.append(job["name"])
    streaming = sorted(n for n, j in found.items() if j["kind"] == "streaming")
    for name in wanted:
        job = found.get(name)
        if job is None:
            raise DeployError(
                "arguments",
                f"--jobs {name}: no such sidecar job in this artifact - its streaming jobs are "
                + (", ".join(streaming) or "none"),
            )
        if job["kind"] != "streaming":
            raise DeployError(
                "arguments",
                f"--jobs {name} is a bounded worker: a deployment installs it and never runs "
                f"it - run `uqs backfill {name}` from the release when you mean to",
            )
        sel.jobs.append(name)
        sel.processes += [job["procname"], *job.get("needs", [])]
    sel.processes = list(dict.fromkeys(sel.processes))
    return sel


def smoke_args(profile: str, sel: Selection) -> list[str]:
    """deploy_smoke.q's flags: every process the deployment starts, so the
    smoke loads exactly their declarations against the server's schema (#902).
    The smoke passes over the names that run no declared job."""
    return ["-procs", *started_by(profile, sel.processes)]


def started_by(profile: str, extra: Sequence[str]) -> list[str]:
    """Every process `uqs start --profile <profile> <extra>` starts, and so
    what undoing that start must stop. Never `stop all`: torq.sh reads `all`
    as the startwithall=1 rows only, which leaves most profiles' jobs and
    every extra process running (#1040)."""
    procs = profiles.resolve(n for n in profile.split(",") if n)
    return list(dict.fromkeys([*procs, *extra]))


#: A release from before #835 carries scripts/deploy_verify.py and no
#: `uqs deploy verify`. A rollback can return to one, and a release must be
#: verified by its own code, so the command asks the release which it has.
#: Remove once no server keeps a release from before #835.
_LEGACY_VERIFIER = "scripts/deploy_verify.py"


def verify_args(sel: Selection, live: bool) -> list[str]:
    """`uqs deploy verify`'s flags for a selection; none without one."""
    argv = []
    if sel.processes:
        argv += ["--procs", ",".join(sel.processes)]
    if sel.tables:
        argv += ["--tables", ",".join(sel.tables)]
    return [*argv, "--live"] if live else argv


def verify_command(profile: str, sel: Selection, live: bool, *extra: str) -> str:
    """The shell command a release verifies itself with, from its own root:
    `uqs deploy verify`, or in a release from before #835 its verifier
    script, which spelled the selection as space-separated words."""
    argv = ["--profile", profile, *extra]
    legacy = list(argv)
    if sel.processes:
        legacy += ["--procs", *sel.processes]
    if sel.tables:
        legacy += ["--tables", *sel.tables]
    if live:
        legacy.append("--live")
    new = " ".join(q(a) for a in [*argv, *verify_args(sel, live)])
    old = " ".join(q(a) for a in legacy)
    return (
        f"if [ -f {_LEGACY_VERIFIER} ]; then .venv/bin/python {_LEGACY_VERIFIER} {old}; "
        f"else .venv/bin/uqs deploy verify {new}; fi"
    )
