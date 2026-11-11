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
                f"it - run `uqs job backfill {name}` from the release when you mean to",
            )
        sel.jobs.append(name)
        sel.processes += [job["procname"], *job.get("needs", [])]
    sel.processes = list(dict.fromkeys(sel.processes))
    return sel


def smoke_args(profile: str, sel: Selection) -> list[str]:
    """scripts/deploy/smoke.q's flags: every process the deployment starts, so the
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


def verify_args(sel: Selection, live: bool, current: bool = True) -> list[str]:
    """`uqs deploy check`'s flags for a selection; none without one. A process
    is `--proc NAME` since #1857, a table `--table NAME` since #1856;
    `current` False spells them as a release from before then takes them,
    `--procs NAME,...` and `--tables NAME,...`."""
    argv = []
    if sel.processes and current:
        argv += [a for p in sel.processes for a in ("--proc", p)]
    elif sel.processes:
        argv += ["--procs", ",".join(sel.processes)]
    if sel.tables and current:
        argv += [a for t in sel.tables for a in ("--table", t)]
    elif sel.tables:
        argv += ["--tables", ",".join(sel.tables)]
    return [*argv, "--live"] if live else argv


def verify_command(
    profile: str, sel: Selection, live: bool, *extra: str, timeout: int | None = None
) -> str:
    """The shell command a release verifies itself with, from its own root:
    `uqs deploy check`, or, on a release from before #1733 - a rollback's
    target - `uqs deploy verify`, the name it had then. A release from before
    #835, which had neither, is not a supported rollback target (#1648).

    The readiness wait (`timeout`) is `--verify-timeout`, a process `--proc`
    and a table `--table` on a release since #1856/#1857; one from before takes
    `--deadline`, `--procs` and `--tables`, so the release's own --help says
    which it is given, by the newest of them (`--table`) - the old spellings
    stop parsing a release after they were renamed (cli/rename_expiry.py)."""

    def argv(current: bool) -> str:
        wait = (
            []
            if timeout is None
            else ["--verify-timeout" if current else "--deadline", str(timeout)]
        )
        words = ["--profile", profile, *extra, *wait, *verify_args(sel, live, current)]
        return " ".join(q(a) for a in words)

    return (
        "if .venv/bin/uqs deploy check --help 2>/dev/null | grep -q -e '--table '; "
        f"then .venv/bin/uqs deploy check {argv(True)}; "
        "elif .venv/bin/uqs deploy check --help >/dev/null 2>&1; "
        f"then .venv/bin/uqs deploy check {argv(False)}; "
        f"else .venv/bin/uqs deploy verify {argv(False)}; fi"
    )
