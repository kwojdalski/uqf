"""The report a deployment leaves in its release, deploy-report.json: what
was deployed, how each stage went, and what it ran on. The next upgrade
reads it to know what to stop; this machine's history keeps a copy.

The ssh destination is `ssh`, as the flag, the target file, the history and
`deploy status` name it (#2123). A report written before then names it
`host`, and a release from then may still be on a server: `read` takes both,
and a report is only ever written as `ssh`."""

from __future__ import annotations

from dataclasses import dataclass, field

from uqs.deploy.config import redact


@dataclass
class Report:
    release: str
    revision: str
    #: the artifact deployed: its file name and the sha256 the server checked
    #: its bytes against (#1557), whether or not a pin or an override named it
    artifact: dict[str, str]
    dirty: bool
    ssh: str
    dest: str
    profile: str
    status: str = "running"
    stage: str = ""
    error: str = ""
    previous_release: str | None = None
    processes: list[dict] = field(default_factory=list)
    checks: dict[str, str] = field(default_factory=dict)
    rollback: str = "not needed"
    #: --jobs, the processes they added beside the profile, and the bundles
    jobs: list[str] = field(default_factory=list)
    extra_processes: list[str] = field(default_factory=list)
    bundles: dict[str, dict] = field(default_factory=dict)
    live: bool = False
    #: the shared HDB against this release's schema, and what was filled (#870)
    hdb: dict | None = None

    #: what --keep removed after activation (#866), when it was given
    pruned: dict | None = None

    #: an upgrade's outage (#871): when the previous release's processes began
    #: to stop, when the new ones verified, and the seconds between. None
    #: when nothing was replaced.
    downtime: dict | None = None

    #: --soak's verdict and duration, and each streaming job's, when it ran (#869)
    soak: dict | None = None

    #: the q preflight ran (.z.K), and every policy override asked for
    #: (deploy/overrides.py, #1557) - --override accept-converted-release's included
    qversion: str | None = None
    exceptions: list[dict] = field(default_factory=list)

    #: q, TorQ and Python as preflight found them: each one's mode, path,
    #: observed version and what the server provides (deploy/components.py)
    components: dict[str, dict] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


#: The key the ssh destination had before #2123, in reports still on servers.
_OLD_SSH = "host"


def read(data: dict) -> dict:
    """A deploy-report.json as parsed, its ssh destination under `ssh`
    whichever release wrote it."""
    if _OLD_SSH in data and "ssh" not in data:
        data = {("ssh" if k == _OLD_SSH else k): v for k, v in data.items()}
    return data


def render(r: dict) -> str:
    """A report for a terminal: what went where, and how it ended."""
    where = f"{r['ssh']}:{r['dest']}"
    status = r.get("status")
    if status == "planned":
        return f"would deploy release {r['release']} to {where} (profile {r['profile']})"
    if status == "deployed":
        lines = [f"deployed release {r['release']} to {where} (profile {r['profile']})"]
        checks = ", ".join(f"{k} {v}" for k, v in (r.get("checks") or {}).items())
        if checks:
            lines.append(f"  checks    {checks}")
        if r.get("previous_release"):
            lines.append(f"  replaced  {r['previous_release']}")
        downtime = r.get("downtime") or {}
        if downtime.get("seconds") is not None:
            lines.append(f"  downtime  {downtime['seconds']}s")
        pruned = r.get("pruned")
        if pruned and pruned.get("prune") in ("failed", "interrupted"):
            # deployed all the same: the prune ran after `current` moved (#2174)
            lines[0] += "; prune incomplete"
            keep = "" if pruned.get("keep") is None else f" --keep {pruned['keep']}"
            lines.append(
                f"  pruned    {pruned['prune']}: {redact(str(pruned.get('error')))} - "
                f"`uqs deploy prune{keep}` finishes it"
            )
        elif pruned:
            gone = ", ".join(pruned.get("removed", [])) or "nothing"
            lines.append(f"  pruned    {pruned.get('prune')}: removed {gone}")
        return "\n".join(lines)
    lines = [f"FAILED to deploy release {r['release']} to {where} at {r.get('stage')}: "
             f"{redact(str(r.get('error')))}"]  # fmt: skip
    lines.append(f"  rollback  {redact(str(r.get('rollback')))}")
    return "\n".join(lines)
