"""The server side of a deployment before anything changes: who the steps
run as, what preflight finds, what the destination holds, and the lock."""

from __future__ import annotations

import json

from uqs.deploy import preflight as checks
from uqs.deploy.artifact import compatible
from uqs.deploy.config import REPORT, UNREADABLE_REPORT, Config, DeployError, redact
from uqs.deploy.lock import Lock
from uqs.deploy.python_runtime import PythonRuntime
from uqs.deploy.q_runtime import QRuntime
from uqs.deploy.remote import Transport, _checked, diagnostic, q, script
from uqs.deploy.selection import Selection
from uqs.deploy.torq_runtime import Runtime
from uqs.interpreter import PEACHQ
from uqs.logger import get_logger
from uqs.runtimes import DEFAULT_RUNTIME
from uqs.stack import site_fields

log = get_logger()


class Server:
    def __init__(
        self,
        cfg: Config,
        remote: Transport,
        target: dict | None = None,
        release: str = "",
    ) -> None:
        self.cfg = cfg
        self.remote = remote
        #: what the artifact was built for: os, arch, python
        self.target = target or {"os": "linux", "arch": "x86_64", "python": "3.14"}
        #: the release id being deployed, to refuse one already on the server
        self.release = release
        #: QCMD and QHOME as preflight resolved and checked them on the server
        self.q_env: dict[str, str] = {}
        #: what --jobs resolved to against the artifact's bundles
        self.selection = Selection()
        #: the runtime the artifact was built for (#852): its bundles are in it.
        #: The driver sets it from the manifest (artifact.release_runtime).
        self.runtime = DEFAULT_RUNTIME
        #: torq_mode bundled's runtime (deploy/torq_runtime.py), which
        #: deploy.env then names in place of an existing TorQ
        self.torq_runtime: Runtime | None = None
        #: q_mode bundled's PeachQ (deploy/q_runtime.py): deploy.env's QCMD and QHOME
        self.q_runtime: QRuntime | None = None
        #: python_mode bundled's CPython (deploy/python_runtime.py): .venv's interpreter
        self.python_runtime: PythonRuntime | None = None
        d = cfg.dest
        self.releases = f"{d}/releases"
        self.current = f"{d}/current"
        self.lock = f"{d}/deploy.lock"
        self.locker = Lock(cfg, remote, self.lock)
        self.shared_config = f"{d}/shared/config"

    # ------- remote helpers

    def run(self, stage: str, what: str, *lines: str, timeout: int | None = None) -> str:
        r = self.remote.run(script(*lines), timeout or self.cfg.command_timeout, stage)
        return _checked(r, stage, what)

    def run_as_login(self, stage: str, what: str, *lines: str) -> str:
        """A step that must run as the ssh login user, not the service user."""
        r = self.remote.run(script(*lines), self.cfg.command_timeout, stage, as_login=True)
        return _checked(r, stage, what)

    def check_sudo(self) -> None:
        """--remote-user only: sudo -n works for that account, and lands in it.

        Read-only, and first: a missing sudo rule fails here, naming the rule
        an administrator would add, rather than half-way through a deployment.
        """
        user = self.cfg.remote_user
        if not user:
            return
        r = self.remote.run(
            script(f"sudo -n -iu {q(user)} id -un"),
            self.cfg.command_timeout,
            "preflight",
            as_login=True,
        )
        if r.returncode:
            raise DeployError(
                "preflight",
                f"the login user on {self.cfg.host} cannot run commands as {user} through "
                f"`sudo -n -iu {user}` without a password: " + diagnostic(r),
            )
        got = (r.stdout or "").strip().splitlines()[-1:] or [""]
        if got[0] != user:
            raise DeployError(
                "preflight", f"`sudo -n -iu {user}` runs as {got[0] or 'nobody'}, not {user}"
            )

    def env_lines(self) -> list[str]:
        """deploy.env: the TorQ and the q - external, or the bundled runtimes'
        - a bundled Python's runtime, and the stable data directory."""
        c = self.cfg
        torq_home, torq_app_home = c.torq_home, c.torq_app_home
        if self.torq_runtime is not None:
            torq_home, torq_app_home = self.torq_runtime.homes(c.dest)
        q_env = [(k, self.q_env.get(k, v)) for k, v in (("QCMD", c.qcmd), ("QHOME", c.qhome))]
        if self.q_runtime is not None:
            q_env = self.q_runtime.env(c.dest)
        python_env = self.python_runtime.env(c.dest) if self.python_runtime else []
        pairs = [
            ("TORQHOME", torq_home),
            ("TORQAPPHOME", torq_app_home),
            *q_env,
            *python_env,
            ("UQS_DATA_ROOT", c.data_root),
            ("UQS_RUNTIME", self.runtime),
            # a source with no credential is refused, never read as its fixture
            ("UQS_REQUIRE_LIVE_SOURCES", "1" if c.live else None),
            # the private ODBC setup deploy.env loads, for `uqs config sources check`
            ("UQS_ODBC_HOME", c.odbc_home),
            # the site launcher's own process.csv columns, empty unless set
            (site_fields.PROCESS_FIELDS_ENV, ",".join(c.process_fields)),
            # the release installs from its own wheels; nothing after that
            # may reach for an index either
            ("UV_OFFLINE", "1"),
        ]
        if c.torq_launcher:
            # the site launcher's own data variable is the deployment's, so it
            # cannot fall back to shared site data from whoever's shell
            launcher = {"TORQDATAHOME": c.data_root, **c.launcher_env}
            pairs += [("UQS_TORQ_LAUNCHER", c.torq_launcher), *launcher.items()]
        else:
            pairs += list(c.launcher_env.items())
        return [f"export {k}={q(v)}" for k, v in pairs if v]

    def in_release(self, release_dir: str, *lines: str) -> list[str]:
        return [f"cd {q(release_dir)}", "source ./deploy.env", *lines]

    # ------- stages

    def preflight_script(self) -> str:
        """PREFLIGHT, after the values it checks - each one quoted."""
        c = self.cfg
        values = {
            "dest": c.dest,
            "qcmd_flag": c.qcmd or "",
            "qhome_flag": c.qhome or "",
            "torq_home": c.torq_home or "",
            "torq_app_home": c.torq_app_home or "",
            "torq_launcher": c.torq_launcher or "",
            "odbc_home": c.odbc_home or "",
            "data": c.data_root,
            "current": self.current,
            "lock": self.lock,
            "probe_timeout": str(c.smoke_timeout),
            "python": self.target["python"],
            "release_dir": f"{self.releases}/{self.release}" if self.release else "",
            "expected_user": c.remote_user or "",
        }
        # A bundled q or Python is installed under the lock, and run there
        # (runtime_install): none is looked for on the server.
        body = checks.body(self.q_runtime is not None, self.python_runtime is not None)
        return script(*(f"{k}={q(v)}" for k, v in values.items())) + body

    def preflight(self) -> dict[str, str]:
        r = self.remote.run(self.preflight_script(), self.cfg.command_timeout, "preflight")
        out = _checked(r, "preflight", "the preflight checks")
        facts = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
        rt = self.q_runtime
        if rt is not None:  # the version it reported building; it is run once installed
            home = rt.directory(self.cfg.dest)
            facts.update(qhome=home, qcmd=f"{home}/q", qversion=rt.version, qimpl=PEACHQ)
            facts.update(qhome_from="bundled", qcmd_from="bundled")
        if self.python_runtime is not None:  # fitted to the release; run once installed
            facts["python"] = self.python_runtime.minor
        if self.cfg.remote_user and facts.get("user") != self.cfg.remote_user:
            raise DeployError(
                "preflight",
                f"the deployment steps run as {facts.get('user') or 'an unknown user'}, "
                f"not {self.cfg.remote_user}",
            )
        accept = self.cfg.accept_converted_release is not None
        if accept and not self.target.get("q"):
            raise DeployError(
                "preflight",
                "--accept-converted-release is for a release converted at build time "
                "(--q-target), and this one ships its q as written",
            )
        problems = compatible(self.target, facts, accept_converted=accept)
        if problems:
            raise DeployError(
                "preflight", "the artifact cannot run on this server: " + "; ".join(problems)
            )
        if not facts.get("qcmd") or not facts.get("qhome"):
            raise DeployError("preflight", "preflight did not report the q it resolved")
        self.q_env = {"QCMD": facts["qcmd"], "QHOME": facts["qhome"]}
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
        if facts.get("locked") and not self.cfg.break_lock:
            raise DeployError(
                "preflight",
                f"another deployment holds {self.lock} ({facts['locked']}). If it died, "
                "--break-lock removes it - refused while its holder still beats",
            )
        if facts.get("locked"):
            log.warning("{} is held ({}); --break-lock will try it", self.lock, facts["locked"])
        return facts

    def previous_processes(self, previous: str) -> tuple[str, list[str], list[str]]:
        out = self.run(
            "restart",
            "reading the previous deployment's report",
            f"cat {q(self.releases)}/{q(previous)}/{REPORT}",
        )
        try:
            prev = json.loads(out)
            procs = [p["process"] for p in prev["processes"]]
            return prev["profile"], procs, list(prev.get("extra_processes", []))
        except UNREADABLE_REPORT:
            raise DeployError(
                "restart", f"release {previous} has no readable {REPORT} to say what it runs"
            ) from None

    def locked_state(self) -> dict[str, str]:
        """What the destination holds NOW, read under the lock.

        Preflight's reading is from before the lock: another deployment could
        have activated between the two, and acting on the stale `current`
        would stop and roll back the wrong release. So the decisions that
        depend on it are taken again from this.
        """
        out = self.run(
            "lock",
            "reading the destination under the lock",
            f"if [ -L {q(self.current)} ]; then "
            f'echo "current=$(basename "$(readlink {q(self.current)})")"; fi',
            f"if [ -e {q(self.releases)}/{q(self.release)} ]; then echo release_exists=yes; fi",
        )
        state = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
        if state.get("release_exists"):
            raise DeployError(
                "lock", f"release {self.release} appeared on the server before the lock was taken"
            )
        if state.get("current") and not self.cfg.restart:
            raise DeployError(
                "lock",
                f"{self.cfg.dest} now runs release {state['current']} - "
                "pass --restart to replace it",
            )
        return state

    def take_lock(self, command: str = "push") -> None:
        """Hold deploy.lock, recording who holds it (uqs.deploy.lock); with
        --break-lock, first remove one whose holder has stopped beating."""
        self.locker.take(command, self.release)

    def beat(self) -> None:
        """Tell a later reader the lock's holder is alive: between stages."""
        self.locker.beat()

    def discard_staging(self, rid: str) -> None:
        """The staging directory goes whatever happened; the release stays."""
        try:
            self.remote.run(
                script(f"rm -rf {q(self.cfg.dest)}/staging/{q(rid)}"),
                self.cfg.command_timeout,
                "cleanup",
            )
        except DeployError as exc:
            log.warning("could not remove the staging directory: {}", redact(str(exc)))

    def release_lock(self) -> None:
        try:
            self.remote.run(script(f"rm -rf {q(self.lock)}"), self.cfg.command_timeout, "lock")
        except DeployError as exc:
            log.warning("could not release {}: {}", self.lock, redact(str(exc)))
