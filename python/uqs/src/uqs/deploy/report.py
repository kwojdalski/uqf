"""The report a deployment leaves in its release, deploy-report.json: what
was deployed, how each stage went, and what it ran on. The next upgrade
reads it to know what to stop; this machine's history keeps a copy."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Report:
    release: str
    revision: str
    dirty: bool
    host: str
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
    #: (deploy/overrides.py, #1557) - --accept-converted-release's included
    qversion: str | None = None
    exceptions: list[dict] = field(default_factory=list)

    #: q, TorQ and Python as preflight found them: each one's mode, path,
    #: observed version and what the server provides (deploy/components.py)
    components: dict[str, dict] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return dict(self.__dict__)
