"""deploy.py - put a uqf release on a Linux server that already runs TorQ,
and prove it works before calling it deployed (#773, #778).

    python3 scripts/build_release.py --output dist/
    python3 scripts/deploy.py --artifact dist/uqf-<release>.tar.gz \\
        --host uqf-server --dest /opt/uqf \\
        --torq-home /opt/torq --torq-app-home /opt/torq-finance-starter-pack \\
        --qcmd /opt/kx/bin/q --qhome /opt/kx --profile essential --dry-run

BUILDING IS NOT DEPLOYING. scripts/build_release.py makes the artifact once -
the tree, the uqs wheel and every pinned dependency's wheel, and a manifest
of the target and every member's sha256 - and this deploys that same file to
as many servers as it is pointed at. The artifact is checked whole here,
against its .sha256 and its manifest, before anything touches the server.

THE STAGES, in order, each stopping the deployment when it fails:

  artifact   the archive matches its .sha256, and every member its manifest
             hash, with nothing missing or extra.
  preflight  ssh works; the destination is writable; uv and the release's
             Python are there; the OS, architecture and Python match what the
             artifact was built for; q runs and is licensed; the TorQ and starter-pack trees
             hold what torq.sh needs; envsubst and rlwrap are on PATH; the
             runtime data directory exists (or --init-data was given); and a
             deployment already there is only replaced with --restart.
  transfer   scp into a staging directory of its own; the checksum is checked
             ON THE SERVER before anything is extracted.
  prepare    releases/<id>/ gets a release-local .venv installed OFFLINE
             from the artifact's own wheels - hashes required, no index, no
             download - and a deploy.env naming the external TorQ, q and the
             stable data directory (UQS_DATA_ROOT), with UV_OFFLINE=1 so
             nothing later fetches either. The ports this profile listens on
             are checked free.
  smoke      scripts/deploy_smoke.q: the quant library loads and computes known
             numbers. Needs its success marker AND exit 0, within a timeout.
  restart    with --restart, the previous deployment's processes stop - their
             replacements take the same ports, so v1 has downtime.
  start      `uqs start --profile <profile>` from the new release.
  verify     scripts/deploy_verify.py: every process the profile promises
             answers over q IPC as itself, and the library and ETL checks
             pass where they are loaded, before a deadline.
  activate   `current` moves to the new release in one rename. The previous
             release stays where it was.

A failure after the previous deployment was stopped stops the new one's
processes and starts the previous ones again; `current` is only ever moved
by activate, so it still names the previous release. The report says what
failed, at which stage, and - separately - whether the rollback worked.

Layout under --dest:

    releases/<id>/        one per deployment, kept; <id> is UTC time + revision
    current -> releases/<id>
    shared/data/          the runtime data (HDB, logs, status), unless --data-dir
    shared/config/        operator-supplied files linked into each release
    staging/<id>/         the archive while it is checked; removed after
    deploy.lock/          held while a deployment runs; serialises them

Nothing here disables host-key checking or reads a secret: ssh and scp run
with the operator's own configuration, in batch mode so a missing key fails
rather than prompts.

Standard library only, like scripts/peachq.py: no package to install first,
and parseable by Python 3.10 - it runs under the operator's own python3.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# beside this file, found once its directory is on the path; ty resolves
# imports from the package roots only
import build_release  # noqa: E402  # ty: ignore[unresolved-import]

ROOT = Path(__file__).resolve().parents[1]

#: timezone.utc, not datetime.UTC: UTC is 3.11+, and this runs under 3.10 too.
_UTC = timezone.utc  # noqa: UP017

#: Files an operator keeps on the server and every release links in, by their
#: path in the repository. Absent ones are simply not linked.
SHARED_CONFIG = ("scripts/torqconfig/permissions/gateway_users.csv",)

#: One-liners run with the server's python3: a file's sha256, and an atomic
#: rename (os.replace) - portable where `mv -T` and `sha256sum` are not.
SHA256_PY = "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())"
REPLACE_PY = "import os,sys; os.replace(sys.argv[1], sys.argv[2])"

REPORT = "deploy-report.json"
SMOKE_MARKER = "DEPLOY_SMOKE_OK"
VERIFY_MARKER = "DEPLOY_VERIFY_OK"

_DEST = re.compile(r"/[A-Za-z0-9._/-]*[A-Za-z0-9._-]")
_HOST = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._@-]*")
_PROFILE = re.compile(r"[a-z][a-z0-9_]*")
_SECRETISH = re.compile(r"(?i)\b(pwd|password|passwd|secret|token|api_?key)\s*[=:]\s*\S+")


#: Named tuples rather than `except (A, B):` inline: ruff-format targets 3.14,
#: where it drops those parentheses (PEP 758), and this file runs under the
#: operator's own python3, which may be older - as scripts/peachq.py does.
_UNREADABLE_REPORT = (ValueError, KeyError, TypeError)
_UNPARSED = (IndexError, ValueError)
_UNPARSED_BUSY = (IndexError, ValueError, KeyError, TypeError, AttributeError)


class DeployError(Exception):
    """A deployment that cannot go on; `stage` says where it stopped."""

    def __init__(self, stage: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage


def load_artifact(path: str) -> build_release.Artifact:
    """The artifact, checked whole (build_release.read_artifact), or why not."""
    try:
        return build_release.read_artifact(Path(path))
    except build_release.ReleaseError as exc:
        raise DeployError(exc.stage, str(exc)) from None


def redact(text: str) -> str:
    """`text` with anything shaped like a secret assignment masked."""
    return _SECRETISH.sub(lambda m: f"{m.group(1)}=<redacted>", text)


def log(message: str) -> None:
    print(f"deploy: {redact(message)}", file=sys.stderr, flush=True)


# --------------------------------------------------------------- arguments


@dataclass
class Config:
    host: str
    dest: str
    profile: str
    artifact: str = ""
    torq_home: str | None = None
    torq_app_home: str | None = None
    qcmd: str | None = None
    qhome: str | None = None
    data_dir: str | None = None
    dry_run: bool = False
    restart: bool = False
    init_data: bool = False
    connect_timeout: int = 10
    command_timeout: int = 900
    smoke_timeout: int = 120
    verify_timeout: int = 180

    @property
    def data_root(self) -> str:
        return self.data_dir or f"{self.dest}/shared/data"


def _absolute(name: str, value: str | None) -> str | None:
    if value is None:
        return None
    if not _DEST.fullmatch(value) or "/../" in f"{value}/" or "//" in value:
        raise DeployError(
            "arguments",
            f"{name} {value!r} must be an absolute path of letters, digits and . _ - /",
        )
    return value.rstrip("/") or "/"


def _required_absolute(name: str, value: str) -> str:
    path = _absolute(name, value)
    assert path is not None  # _absolute returns None only for None
    return path


def parse_args(argv: Sequence[str] | None = None) -> Config:
    p = argparse.ArgumentParser(
        prog="deploy.py", description="Deploy uqf onto a server with an existing TorQ."
    )
    p.add_argument(
        "--artifact", required=True, help="the release to deploy, from scripts/build_release.py"
    )
    p.add_argument("--host", required=True, help="ssh destination, as your ssh config knows it")
    p.add_argument("--dest", required=True, help="absolute directory on the server")
    p.add_argument("--profile", required=True, help="the uqs profile to start, e.g. essential")
    p.add_argument("--torq-home", help="an existing TorQ on the server (TORQHOME)")
    p.add_argument("--torq-app-home", help="an existing finance starter pack (TORQAPPHOME)")
    p.add_argument("--qcmd", help="the q binary on the server (QCMD); default: q on PATH")
    p.add_argument("--qhome", help="QHOME on the server, where q finds its licence")
    p.add_argument("--data-dir", help="runtime data directory; default: <dest>/shared/data")
    p.add_argument("--dry-run", action="store_true", help="change nothing; show what would run")
    p.add_argument(
        "--restart",
        action="store_true",
        help="replace a deployment already there: stop its processes, start the new ones",
    )
    p.add_argument(
        "--init-data",
        action="store_true",
        help="create the runtime data directory if it does not exist yet",
    )
    p.add_argument("--connect-timeout", type=int, default=10, help="ssh/scp, seconds")
    p.add_argument("--command-timeout", type=int, default=900, help="each remote step, seconds")
    p.add_argument("--smoke-timeout", type=int, default=120, help="the offline check, seconds")
    p.add_argument("--verify-timeout", type=int, default=180, help="readiness deadline, seconds")
    a = p.parse_args(argv)
    if not _HOST.fullmatch(a.host):
        raise DeployError("arguments", f"--host {a.host!r} is not an ssh destination")
    if not _PROFILE.fullmatch(a.profile):
        raise DeployError("arguments", f"--profile {a.profile!r} is not a profile name")
    for name in ("connect_timeout", "command_timeout", "smoke_timeout", "verify_timeout"):
        if getattr(a, name) <= 0:
            raise DeployError("arguments", f"--{name.replace('_', '-')} must be positive")
    dest = _required_absolute("--dest", a.dest)
    if dest == "/":
        raise DeployError("arguments", "--dest must not be /")
    return Config(
        host=a.host,
        dest=dest,
        artifact=a.artifact,
        profile=a.profile,
        torq_home=_absolute("--torq-home", a.torq_home),
        torq_app_home=_absolute("--torq-app-home", a.torq_app_home),
        qcmd=_absolute("--qcmd", a.qcmd),
        qhome=_absolute("--qhome", a.qhome),
        data_dir=_absolute("--data-dir", a.data_dir),
        dry_run=a.dry_run,
        restart=a.restart,
        init_data=a.init_data,
        connect_timeout=a.connect_timeout,
        command_timeout=a.command_timeout,
        smoke_timeout=a.smoke_timeout,
        verify_timeout=a.verify_timeout,
    )


# ------------------------------------------------------------------ remote

Runner = Callable[..., subprocess.CompletedProcess]


class Remote:
    """ssh and scp to one host, under the operator's own configuration.

    BatchMode makes a missing key or an unknown host key FAIL instead of
    prompting; host-key checking stays whatever ssh_config says. Scripts go
    in on stdin to `bash -s`, so the only text the remote login shell parses
    is fixed - every value is quoted inside the script with shlex.quote.
    """

    def __init__(self, host: str, connect_timeout: int, runner: Runner = subprocess.run) -> None:
        self.host = host
        self.options = ["-o", "BatchMode=yes", "-o", f"ConnectTimeout={connect_timeout}"]
        self.runner = runner

    def ssh_argv(self) -> list[str]:
        return ["ssh", *self.options, self.host, "bash -s"]

    def run(self, script: str, timeout: int, stage: str) -> subprocess.CompletedProcess:
        try:
            return self.runner(
                self.ssh_argv(),
                input=script,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise DeployError(stage, f"the remote step did not finish within {timeout}s") from None
        except OSError as exc:
            raise DeployError(stage, f"could not run ssh: {exc}") from None

    def put(self, local: Path, remote_path: str, timeout: int, stage: str) -> None:
        argv = ["scp", "-q", *self.options, str(local), f"{self.host}:{remote_path}"]
        try:
            r = self.runner(argv, capture_output=True, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            raise DeployError(stage, f"scp did not finish within {timeout}s") from None
        except OSError as exc:
            raise DeployError(stage, f"could not run scp: {exc}") from None
        if r.returncode:
            raise DeployError(stage, f"scp failed: {redact(r.stderr.strip())}")


def q(value: str) -> str:
    """One shell word, whatever `value` holds."""
    return shlex.quote(value)


def script(*lines: str) -> str:
    return "set -euo pipefail\n" + "\n".join(lines) + "\n"


def _checked(r: subprocess.CompletedProcess, stage: str, what: str) -> str:
    if r.returncode:
        detail = (r.stderr or r.stdout or "").strip().splitlines()[-15:]
        raise DeployError(
            stage, f"{what} failed (exit {r.returncode}): " + redact("\n".join(detail))
        )
    return r.stdout


# --------------------------------------------------------------- the steps


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

    def as_dict(self) -> dict:
        return dict(self.__dict__)


class Deployment:
    def __init__(
        self,
        cfg: Config,
        remote: Remote,
        target: dict | None = None,
        root: Path = ROOT,
        release: str = "",
    ) -> None:
        self.cfg = cfg
        self.remote = remote
        self.root = root
        #: what the artifact was built for: os, arch, python
        self.target = target or {"os": "linux", "arch": "x86_64", "python": "3.14"}
        #: the release id being deployed, to refuse one already on the server
        self.release = release
        d = cfg.dest
        self.releases = f"{d}/releases"
        self.current = f"{d}/current"
        self.lock = f"{d}/deploy.lock"
        self.shared_config = f"{d}/shared/config"

    # ------- remote helpers

    def run(self, stage: str, what: str, *lines: str, timeout: int | None = None) -> str:
        r = self.remote.run(script(*lines), timeout or self.cfg.command_timeout, stage)
        return _checked(r, stage, what)

    def env_lines(self) -> list[str]:
        """deploy.env: the external TorQ, q and the stable data directory."""
        c = self.cfg
        pairs = [
            ("TORQHOME", c.torq_home),
            ("TORQAPPHOME", c.torq_app_home),
            ("QCMD", c.qcmd),
            ("QHOME", c.qhome),
            ("UQS_DATA_ROOT", c.data_root),
            ("UQS_RUNTIME", "uqf"),
            # the release installs from its own wheels; nothing after that
            # may reach for an index either
            ("UV_OFFLINE", "1"),
        ]
        return [f"export {k}={q(v)}" for k, v in pairs if v]

    def in_release(self, release_dir: str, *lines: str) -> list[str]:
        return [f"cd {q(release_dir)}", "source ./deploy.env", *lines]

    # ------- stages

    def preflight_script(self) -> str:
        """PREFLIGHT, after the values it checks - each one quoted."""
        c = self.cfg
        values = {
            "dest": c.dest,
            "qcmd": c.qcmd or "q",
            "qhome": c.qhome or "",
            "torq_home": c.torq_home or "",
            "torq_app_home": c.torq_app_home or "",
            "data": c.data_root,
            "current": self.current,
            "lock": self.lock,
            "probe_timeout": str(c.smoke_timeout),
            "python": self.target["python"],
            "release_dir": f"{self.releases}/{self.release}" if self.release else "",
        }
        return script(*(f"{k}={q(v)}" for k, v in values.items())) + PREFLIGHT

    def preflight(self) -> dict[str, str]:
        r = self.remote.run(self.preflight_script(), self.cfg.command_timeout, "preflight")
        out = _checked(r, "preflight", "the preflight checks")
        facts = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
        problems = build_release.compatible(self.target, facts)
        if problems:
            raise DeployError(
                "preflight", "the artifact cannot run on this server: " + "; ".join(problems)
            )
        if facts.get("data") == "absent" and not self.cfg.init_data:
            raise DeployError(
                "preflight",
                f"the runtime data directory {self.cfg.data_root} does not exist - pass "
                "--init-data to create it (it is never created implicitly)",
            )
        if facts.get("release_exists"):
            raise DeployError(
                "preflight",
                f"release {self.release} is already on {self.cfg.host} under {self.releases} - "
                "an artifact is deployed to a destination once; build a new one to redeploy",
            )
        if facts.get("current") and not self.cfg.restart:
            raise DeployError(
                "preflight",
                f"{self.cfg.dest} already runs release {facts['current']} - pass --restart to "
                "stop its processes and replace it",
            )
        if facts.get("locked"):
            raise DeployError(
                "preflight",
                f"another deployment holds {self.lock} ({facts['locked']}); if it died, "
                "remove that directory by hand",
            )
        return facts

    def previous_processes(self, previous: str) -> tuple[str, list[str]]:
        out = self.run(
            "restart",
            "reading the previous deployment's report",
            f"cat {q(self.releases)}/{q(previous)}/{REPORT}",
        )
        try:
            prev = json.loads(out)
            return prev["profile"], [p["process"] for p in prev["processes"]]
        except _UNREADABLE_REPORT:
            raise DeployError(
                "restart", f"release {previous} has no readable {REPORT} to say what it runs"
            ) from None

    def take_lock(self) -> None:
        owner = f"{datetime.now(_UTC).isoformat(timespec='seconds')} release-pending"
        r = self.remote.run(
            script(
                f"mkdir -p {q(self.cfg.dest)}",
                f"mkdir {q(self.lock)} 2>/dev/null || {{ echo held >&2; exit 3; }}",
                f"printf '%s\\n' {q(owner)} > {q(self.lock)}/owner",
            ),
            self.cfg.command_timeout,
            "lock",
        )
        if r.returncode == 3:
            raise DeployError("lock", f"another deployment holds {self.lock}")
        _checked(r, "lock", "taking the deployment lock")

    def discard_staging(self, rid: str) -> None:
        """The staging directory goes whatever happened; the release stays."""
        try:
            self.remote.run(
                script(f"rm -rf {q(self.cfg.dest)}/staging/{q(rid)}"),
                self.cfg.command_timeout,
                "cleanup",
            )
        except DeployError as exc:
            log(f"could not remove the staging directory: {exc}")

    def release_lock(self) -> None:
        try:
            self.remote.run(script(f"rm -rf {q(self.lock)}"), self.cfg.command_timeout, "lock")
        except DeployError as exc:
            log(f"could not release {self.lock}: {exc}")

    def transfer(self, pkg: build_release.Artifact, rid: str) -> str:
        staging = f"{self.cfg.dest}/staging/{rid}"
        release = f"{self.releases}/{rid}"
        self.run(
            "transfer",
            "creating the staging directory",
            f"mkdir -p {q(staging)} {q(self.releases)}",
        )
        remote_archive = f"{staging}/{pkg.path.name}"
        self.remote.put(pkg.path, remote_archive, self.cfg.command_timeout, "transfer")
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

    def prepare(self, release: str, pkg: build_release.Artifact) -> None:
        c = self.cfg
        py = pkg.manifest["python"]
        lines = [
            f"cd {q(release)}",
            "cat > deploy.env <<'DEPLOYENV'",
            *self.env_lines(),
            "DEPLOYENV",
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
                f'timeout {self.cfg.smoke_timeout} "${{QCMD:-q}}" scripts/deploy_smoke.q -q',
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

    def verify(self, release: str) -> dict:
        r = self.remote.run(
            script(
                *self.in_release(
                    release,
                    ".venv/bin/python scripts/deploy_verify.py "
                    f"--profile {q(self.cfg.profile)} --deadline {self.cfg.verify_timeout}",
                )
            ),
            self.cfg.verify_timeout + 60,
            "verify",
        )
        lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
        try:
            result = json.loads(lines[-2])
        except _UNPARSED:
            result = {"passed": False, "reason": "the verifier printed no result", "processes": []}
        if r.returncode or not lines or lines[-1] != VERIFY_MARKER or not result.get("passed"):
            result["passed"] = False
        return result

    def ports_free(self, release: str) -> None:
        r = self.remote.run(
            script(
                *self.in_release(
                    release,
                    ".venv/bin/python scripts/deploy_verify.py "
                    f"--profile {q(self.cfg.profile)} --ports-free",
                )
            ),
            self.cfg.command_timeout,
            "ports",
        )
        lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
        if r.returncode == 0 and lines and lines[-1] == VERIFY_MARKER:
            return
        try:
            busy = json.loads(lines[-2])["busy"]
            what = ", ".join(f"{name} ({port})" for name, port in busy.items())
        except _UNPARSED_BUSY:
            what = redact((r.stderr or "").strip()[-300:]) or "unknown"
        raise DeployError("ports", f"ports the profile needs are already in use: {what}")

    def activate(self, rid: str) -> None:
        self.run(
            "activate",
            "moving current to the new release",
            f"ln -sfn releases/{q(rid)} {q(self.cfg.dest)}/.current.new",
            f"python3 -c {q(REPLACE_PY)} {q(self.cfg.dest)}/.current.new {q(self.current)}",
        )

    def write_report(self, release: str | None, report: Report) -> None:
        if release is None:
            return
        body = json.dumps(report.as_dict(), indent=2)
        try:
            self.run(
                report.stage or "report",
                "writing the report",
                f"cat > {q(release)}/{REPORT} <<'DEPLOYREPORT'",
                body,
                "DEPLOYREPORT",
            )
        except DeployError as exc:
            log(f"could not write the report on the server: {exc}")


#: The preflight checks, run on the server. Read-only - safe under --dry-run.
#: Each failure says what is missing on stderr and exits 1; the facts the
#: caller decides on (data present, a current release, a held lock) come
#: back on stdout as name=value lines.
PREFLIGHT = r"""
fail() { echo "$*" >&2; exit 1; }
if [ -e "$dest" ]; then
  test -w "$dest" || fail "destination $dest is not writable"
else
  test -w "$(dirname "$dest")" || fail "cannot create $dest: its parent is not writable"
fi
command -v uv >/dev/null || fail "uv is not on PATH"
py=$(uv python find "$python" 2>/dev/null) ||
  fail "uv finds no Python $python - the release's wheels need it"
echo "python=$("$py" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
echo "os=$(uname -s)"
echo "arch=$(uname -m)"
for tool in bash envsubst rlwrap tar timeout python3; do
  command -v "$tool" >/dev/null || fail "$tool is not on PATH - the release needs it"
done
if [ -n "$qhome" ]; then export QHOME="$qhome"; fi
command -v "$qcmd" >/dev/null || fail "q is not runnable: $qcmd"
probe=$(mktemp -d)
printf '%s\n' '-1 "DEPLOY_Q_OK"; exit 0' > "$probe/probe.q"
out=$(timeout "$probe_timeout" "$qcmd" "$probe/probe.q" -q 2>&1 || true)
rm -rf "$probe"
case "$out" in
  *DEPLOY_Q_OK*) ;;
  *) fail "q did not run a script - is it licensed (QHOME)? $(echo "$out" | tail -3)";;
esac
if [ -n "$torq_home" ]; then
  for f in torq.q torq.sh; do test -f "$torq_home/$f" || fail "no $f in $torq_home"; done
fi
if [ -n "$torq_app_home" ]; then
  for f in database.q appconfig/process.csv; do
    test -f "$torq_app_home/$f" || fail "no $f in $torq_app_home"
  done
fi
if [ -d "$data" ]; then echo "data=present"; else echo "data=absent"; fi
if [ -L "$current" ]; then echo "current=$(basename "$(readlink "$current")")"; fi
if [ -n "$release_dir" ] && [ -e "$release_dir" ]; then echo "release_exists=yes"; fi
if [ -d "$lock" ]; then echo "locked=$(cat "$lock/owner" 2>/dev/null || echo unknown)"; fi
"""


# ------------------------------------------------------------------ driver


def plan(
    cfg: Config, pkg: build_release.Artifact, rid: str, facts: dict[str, str], dep: Deployment
) -> str:
    """What --dry-run shows: the payload, the commands, the planned restart."""
    previous = facts.get("current")
    restart = (
        f"stop release {previous}'s processes, then start {rid}'s on the same ports"
        if previous
        else "nothing running to replace"
    )
    release = f"{dep.releases}/{rid}"
    exported = ", ".join(line.split("=", 1)[0].removeprefix("export ") for line in dep.env_lines())
    steps = [
        "lock      " + f"mkdir {dep.lock}",
        "transfer  "
        + f"scp {pkg.path.name} {cfg.host}:{cfg.dest}/staging/{rid}/ (sha256 {pkg.sha256})",
        "prepare   "
        + f"{release}: deploy.env ({exported}); offline install of "
        + f"{pkg.manifest['python']['wheels']} wheels",
        "smoke     " + f"q scripts/deploy_smoke.q (timeout {cfg.smoke_timeout}s)",
        "restart   " + restart,
        "ports     " + f"scripts/deploy_verify.py --profile {cfg.profile} --ports-free",
        "start     " + f"uqs start --profile {cfg.profile}",
        "verify    "
        + f"scripts/deploy_verify.py --profile {cfg.profile} --deadline {cfg.verify_timeout}",
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
            "planned:",
            *[f"  {s}" for s in steps],
        ]
    )


def deploy(cfg: Config, remote: Remote, *, root: Path = ROOT, out=sys.stdout) -> int:
    log(f"checking {cfg.artifact}")
    pkg = load_artifact(cfg.artifact)
    rid = pkg.release
    dep = Deployment(cfg, remote, pkg.manifest["target"], root, rid)
    report = Report(
        release=rid,
        revision=pkg.manifest["revision"],
        dirty=pkg.manifest["dirty"],
        host=cfg.host,
        dest=cfg.dest,
        profile=cfg.profile,
    )

    log(f"preflight on {cfg.host}")
    facts = dep.preflight()
    report.previous_release = facts.get("current")
    if cfg.dry_run:
        print(plan(cfg, pkg, rid, facts, dep), file=out)
        return 0
    return _run(dep, cfg, pkg, rid, report, facts, out)


def _run(
    dep: Deployment,
    cfg: Config,
    pkg: build_release.Artifact,
    rid: str,
    report: Report,
    facts: dict,
    out,
) -> int:
    dep.take_lock()
    release: str | None = None
    previous = facts.get("current")
    prev_profile: str | None = None
    prev_procs: list[str] = []
    stopped_previous = False
    started = False
    try:
        log(f"transferring release {rid}")
        release = dep.transfer(pkg, rid)
        log("preparing the release environment")
        dep.prepare(release, pkg)
        log("offline smoke test")
        dep.smoke(release)
        report.checks["smoke"] = "ok"
        if previous:
            prev_profile, prev_procs = dep.previous_processes(previous)
            log(f"stopping release {previous}'s processes")
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
        dep.ports_free(release)
        log(f"starting profile {cfg.profile}")
        started = True
        dep.uqs(release, "start", "starting the profile", "start", "--profile", cfg.profile)
        log(f"verifying every process answers (up to {cfg.verify_timeout}s)")
        result = dep.verify(release)
        report.processes = result.get("processes", [])
        if not result.get("passed"):
            raise DeployError("verify", result.get("reason") or "verification failed")
        report.checks["verify"] = "ok"
        dep.activate(rid)
        report.status = "deployed"
        report.stage = "done"
    except DeployError as exc:
        report.status = "failed"
        report.stage = exc.stage
        report.error = str(exc)
        log(f"FAILED at {exc.stage}: {exc}")
        report.rollback = _rollback(dep, release, started, stopped_previous, previous, prev_profile)
    finally:
        dep.write_report(release, report)
        dep.discard_staging(rid)
        dep.release_lock()
    print(json.dumps(report.as_dict(), indent=2), file=out)
    return 0 if report.status == "deployed" else 1


def _rollback(dep, release, started, stopped_previous, previous, prev_profile) -> str:
    """Stop what the failed release started, and bring the previous one back."""
    notes = []
    if started and release:
        try:
            dep.uqs(release, "rollback", "stopping the new processes", "stop", "all")
            notes.append("stopped the new release's processes")
        except DeployError as exc:
            notes.append(f"FAILED to stop the new release's processes: {exc}")
    if stopped_previous and previous and prev_profile:
        try:
            dep.uqs(
                f"{dep.releases}/{previous}",
                "rollback",
                "restarting the previous processes",
                "start",
                "--profile",
                prev_profile,
            )
            notes.append(f"restarted release {previous}'s profile {prev_profile}")
        except DeployError as exc:
            notes.append(f"FAILED to restart release {previous}: {exc}")
    if previous:
        notes.append(f"current still names {previous}")
    return "; ".join(notes) or "nothing to roll back"


def main(argv: Sequence[str] | None = None) -> int:
    try:
        cfg = parse_args(argv)
        return deploy(cfg, Remote(cfg.host, cfg.connect_timeout))
    except DeployError as exc:
        log(f"FAILED at {exc.stage}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
