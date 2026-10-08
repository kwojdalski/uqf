"""The stages that change the server: transfer, prepare, smoke, start,
verify, activate - and the report a deployment leaves in its release."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from uqs.deploy.artifact import Artifact
from uqs.deploy.config import (
    REPLACE_PY,
    REPORT,
    SECRETS,
    SHA256_PY,
    SHARED_CONFIG,
    SMOKE_MARKER,
    UNPARSED,
    UNPARSED_BUSY,
    VERIFY_MARKER,
    WRITE_NEW_PY,
    DeployError,
    redact,
)
from uqs.deploy.remote import q, script
from uqs.deploy.selection import Selection, smoke_args, verify_command
from uqs.deploy.server import Server
from uqs.logger import get_logger

log = get_logger(__name__)

#: Run from the release with its deploy.env, after a soak that began at
#: argv[1] (epoch seconds, the server's clock): a verdict for each streaming
#: process among argv[2] (comma-separated), from the release's own
#: stream_health reader and pipeline registry. .z.p, which each record's `at`
#: is, is UTC.
SOAK_PY = r"""
import json, sys
from datetime import UTC, datetime
from uqs.model.pipeline import PipelineKind
from uqs.model.registry import PIPELINES
from uqs.paths import default_paths
from uqs.stack import stream_health
since = float(sys.argv[1])
started = {p for p in sys.argv[2].split(",") if p}
streaming = {p.procname for p in PIPELINES if p.kind is not PipelineKind.BACKFILL}
records = stream_health.read(default_paths())
out = {}
def verdict(r):
    if r is None:
        return "no beat", "it wrote no stream_health record"
    at = datetime.strptime(str(r.get("at", ""))[:26], "%Y.%m.%dD%H:%M:%S.%f")
    if at.replace(tzinfo=UTC).timestamp() < since:
        return "no beat", f"its last record is from {r['at']}, before the soak"
    if r.get("failing"):
        return "failing", f"{r.get('failed')} batch(es) failed, last: {r.get('last_error')}"
    return "ok", f"{r.get('ok')} batch(es), none failed since the last beat"
for proc in sorted(started & streaming):
    v, detail = verdict(records.get(proc))
    out[proc] = {"verdict": v, "detail": detail}
print(json.dumps(out))
"""


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
    #: --soak's verdict and duration, and each streaming job's, when it ran (#869)
    soak: dict | None = None

    def as_dict(self) -> dict:
        return dict(self.__dict__)


class Deployment(Server):
    """Every stage, on top of what Server reads and locks."""

    def upload(self, pkg: Artifact, staging: str) -> str:
        """The archive into `staging`, as the service user; its path there.

        Without --remote-user, scp writes it straight in. With one, scp can
        only write as the login user: into a private directory of its own
        (mktemp -d is mode 0700), from which svc copies it with a single
        sudo call that creates a NEW file and reads the bytes from stdin -
        no chmod, no chown, nothing anyone else can read. The upload
        directory goes in `finally`, whatever happened.
        """
        remote_archive = f"{staging}/{pkg.path.name}"
        user = self.cfg.remote_user
        if not user:
            self.remote.put(pkg.path, remote_archive, self.cfg.command_timeout, "transfer")
            return remote_archive
        upload = (
            self.run_as_login(
                "transfer",
                "creating a private upload directory",
                "mktemp -d /tmp/uqf-upload.XXXXXX",
            )
            .strip()
            .splitlines()[-1]
        )
        if not upload.startswith("/tmp/uqf-upload."):
            raise DeployError("transfer", f"mktemp returned {upload!r}, not an upload directory")
        try:
            uploaded = f"{upload}/{pkg.path.name}"
            self.remote.put(pkg.path, uploaded, self.cfg.command_timeout, "transfer")
            self.run_as_login(
                "transfer",
                f"handing the archive to {user}",
                f"sudo -n -u {q(user)} -- python3 -c {q(WRITE_NEW_PY)} {q(remote_archive)} "
                f"< {q(uploaded)}",
            )
        finally:
            try:
                self.run_as_login("cleanup", "removing the upload directory", f"rm -rf {q(upload)}")
            except DeployError as exc:
                log.warning(
                    "could not remove the upload directory {}: {}", upload, redact(str(exc))
                )
        return remote_archive

    def transfer(self, pkg: Artifact, rid: str) -> str:
        staging = f"{self.cfg.dest}/staging/{rid}"
        release = f"{self.releases}/{rid}"
        self.run(
            "transfer",
            "creating the staging directory",
            f"mkdir -p {q(staging)} {q(self.releases)}",
        )
        remote_archive = self.upload(pkg, staging)
        self.run(
            "transfer",
            "checking the archive and extracting it",
            f"got=$(python3 -c {q(SHA256_PY)} {q(remote_archive)})",
            f'if [ "$got" != {q(pkg.sha256)} ]; then',
            '  echo "checksum mismatch: the archive arrived as $got" >&2',
            f"  rm -rf {q(staging)}; exit 1",
            "fi",
            f"test ! -e {q(release)} || {{ echo 'release {rid} already exists' >&2; exit 1; }}",
            f"mkdir {q(release)}",
            f"tar -xzf {q(remote_archive)} -C {q(release)}",
            f"rm -rf {q(staging)}",
        )
        return release

    def prepare(self, release: str, pkg: Artifact) -> None:
        c = self.cfg
        py = pkg.manifest["python"]
        secrets = f"{self.shared_config}/{SECRETS}"
        lines = [
            f"cd {q(release)}",
            "cat > deploy.env <<'DEPLOYENV'",
            *self.env_lines(),
            f"if [ -f {q(secrets)} ]; then set -a; . {q(secrets)}; set +a; fi",
            # the private ODBC setup: its overlay QHOME, driver registry and
            # libraries, for every process the release starts
            *([f". {q(c.odbc_home)}/current/env.sh"] if c.odbc_home else []),
            "DEPLOYENV",
            f'if [ -f {q(secrets)} ] && [ -n "$(find {q(secrets)} -perm /077)" ]; then',
            f"  echo {q(f'{secrets} may be read by others - make it owner-only')} >&2; exit 1",
            "fi",
        ]
        if c.init_data:
            lines.append(f"mkdir -p {q(c.data_root)}")
        missing = q(f"no runtime data directory {c.data_root}")
        lines.append(f"test -d {q(c.data_root)} || {{ echo {missing} >&2; exit 1; }}")
        for rel in SHARED_CONFIG:
            src = f"{self.shared_config}/{rel}"
            lines.append(
                f"if [ -f {q(src)} ]; then mkdir -p {q(str(Path(rel).parent))}; "
                f"ln -sfn {q(src)} {q(rel)}; fi"
            )
        # OFFLINE, from the artifact's wheels: the dependencies with their
        # locked hashes required, then uqs itself, which the lock does not pin
        offline = ["--quiet", "--offline", "--no-index", "--python", ".venv/bin/python"]
        lines += [
            "export UV_OFFLINE=1",
            f"uv venv --quiet --python {q(self.target['python'])} .venv",
            " ".join(
                [
                    "uv pip install",
                    *offline,
                    "--find-links",
                    q(py["wheel_dir"]),
                    "--require-hashes",
                    "-r",
                    q(py["requirements"]),
                ]
            ),
            " ".join(["uv pip install", *offline, "--no-deps", q(py["app_wheel"])]),
        ]
        self.run("prepare", "installing the release's Python environment offline", *lines)

    def smoke(self, release: str) -> None:
        out = self.run(
            "smoke",
            "the offline smoke test",
            *self.in_release(
                release,
                f'timeout {self.cfg.smoke_timeout} "$QCMD" scripts/deploy_smoke.q -q '
                + " ".join(q(a) for a in smoke_args(self.cfg.profile, self.selection)),
            ),
            timeout=self.cfg.smoke_timeout + 30,
        )
        if SMOKE_MARKER not in out:
            raise DeployError("smoke", f"the smoke test exited 0 without printing {SMOKE_MARKER}")

    def uqs(
        self, release: str, stage: str, what: str, *args: str, timeout: int | None = None
    ) -> str:
        argv = " ".join(q(a) for a in args)
        return self.run(
            stage,
            what,
            *self.in_release(release, f".venv/bin/uqs {argv}"),
            timeout=timeout,
        )

    def verify(
        self,
        release: str,
        profile: str | None = None,
        stage: str = "verify",
        sel: Selection | None = None,
    ) -> dict:
        """Run the release's own verifier, from its own environment. `sel` is
        what it checks beyond the profile: this deployment's own selection,
        with --live when asked, unless another is given - a rollback's."""
        live = self.cfg.live if sel is None else False
        command = verify_command(
            profile or self.cfg.profile,
            self.selection if sel is None else sel,
            live,
            "--deadline",
            str(self.cfg.verify_timeout),
        )
        r = self.remote.run(
            script(*self.in_release(release, command)), self.cfg.verify_timeout + 60, stage
        )
        lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
        try:
            result = json.loads(lines[-2])
        except UNPARSED:
            result = {"passed": False, "reason": "the verifier printed no result", "processes": []}
        if r.returncode or not lines or lines[-1] != VERIFY_MARKER or not result.get("passed"):
            result["passed"] = False
        return result

    def live_check(self, release: str) -> None:
        """Check the named sources live, from the release, with the stack up:
        a check that needs the running gateway can only run now."""
        c = self.cfg
        names = " ".join(q(s) for s in c.live_check)
        r = self.remote.run(
            script(
                *self.in_release(
                    release,
                    f".venv/bin/uqs config sources check {names} --timeout {c.live_check_timeout}",
                )
            ),
            c.live_check_timeout + 60,
            "live-check",
        )
        if r.returncode:
            tail = "\n".join(((r.stdout or "") + (r.stderr or "")).strip().splitlines()[-20:])
            raise DeployError("live-check", "a source failed its live check:\n" + redact(tail))

    def soak(self, release: str, processes: list[str]) -> dict:
        """Let data flow for --soak seconds, then judge every started
        streaming job by its own stream_health record (#832), read by the
        release's own code (SOAK_PY). A job that is failing, or wrote no record
        since the soak began - on the server's clock - fails it."""
        seconds = self.cfg.soak or 0
        since = self.run("soak", "reading the server's clock", "date +%s").strip()
        time.sleep(seconds)
        out = self.run(
            "soak",
            "reading the streaming jobs' health",
            *self.in_release(
                release,
                f".venv/bin/python -c {q(SOAK_PY)} {q(since.splitlines()[-1])} "
                f"{q(','.join(processes))}",
            ),
        )
        try:
            jobs = json.loads(out.strip().splitlines()[-1])
        except UNPARSED:
            raise DeployError("soak", "the soak check printed no result") from None
        bad = {p: j for p, j in jobs.items() if j["verdict"] != "ok"}
        result = {"seconds": seconds, "passed": not bad, "jobs": jobs}
        if bad:
            why = "; ".join(f"{p}: {j['verdict']} - {j['detail']}" for p, j in sorted(bad.items()))
            result["reason"] = why
        return result

    def ports_free(self, release: str) -> None:
        sel = Selection(processes=self.selection.processes)
        command = verify_command(self.cfg.profile, sel, False, "--ports-free")
        r = self.remote.run(
            script(*self.in_release(release, command)), self.cfg.command_timeout, "ports"
        )
        lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
        if r.returncode == 0 and lines and lines[-1] == VERIFY_MARKER:
            return
        try:
            busy = json.loads(lines[-2])["busy"]
            what = ", ".join(f"{name} ({port})" for name, port in busy.items())
        except UNPARSED_BUSY:
            what = redact((r.stderr or "").strip()[-300:]) or "unknown"
        raise DeployError("ports", f"ports the profile needs are already in use: {what}")

    def activate(self, rid: str) -> None:
        self.run(
            "activate",
            "moving current to the new release",
            f"ln -sfn releases/{q(rid)} {q(self.cfg.dest)}/.current.new",
            f"python3 -c {q(REPLACE_PY)} {q(self.cfg.dest)}/.current.new {q(self.current)}",
        )

    def write_report(self, release: str | None, report: Report, *, required: bool = False) -> None:
        """The report into the release. `required` before activation: the next
        upgrade reads it to know which processes to stop, so a release without
        one must never become `current`. Afterwards a failed write is logged."""
        if release is None:
            return
        body = json.dumps(report.as_dict(), indent=2)
        try:
            self.run(
                "report" if required else (report.stage or "report"),
                "writing the report",
                f"cat > {q(release)}/{REPORT}.new <<'DEPLOYREPORT'",
                body,
                "DEPLOYREPORT",
                f"python3 -c {q(REPLACE_PY)} {q(release)}/{REPORT}.new {q(release)}/{REPORT}",
            )
        except DeployError as exc:
            if required:
                raise DeployError("report", f"could not record the deployment: {exc}") from None
            log.warning("could not write the report on the server: {}", redact(str(exc)))
