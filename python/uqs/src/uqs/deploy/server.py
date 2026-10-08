"""The server side of a deployment before anything changes: who the steps
run as, what preflight finds, what the destination holds, and the lock."""

from __future__ import annotations

import json

from uqs.deploy.artifact import compatible
from uqs.deploy.config import REPORT, UNREADABLE_REPORT, Config, DeployError, redact
from uqs.deploy.lock import Lock
from uqs.deploy.remote import Transport, _checked, q, script
from uqs.deploy.selection import Selection
from uqs.logger import get_logger

log = get_logger(__name__)


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
        #: the runtime the artifact was built for (#852): its bundles are in it
        self.runtime = "uqf"
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
                f"`sudo -n -iu {user}` without a password: "
                + redact((r.stderr or r.stdout or "").strip()[-300:]),
            )
        got = (r.stdout or "").strip().splitlines()[-1:] or [""]
        if got[0] != user:
            raise DeployError(
                "preflight", f"`sudo -n -iu {user}` runs as {got[0] or 'nobody'}, not {user}"
            )

    def env_lines(self) -> list[str]:
        """deploy.env: the external TorQ, q and the stable data directory."""
        c = self.cfg
        pairs = [
            ("TORQHOME", c.torq_home),
            ("TORQAPPHOME", c.torq_app_home),
            ("QCMD", self.q_env.get("QCMD", c.qcmd)),
            ("QHOME", self.q_env.get("QHOME", c.qhome)),
            ("UQS_DATA_ROOT", c.data_root),
            ("UQS_RUNTIME", self.runtime),
            # a source with no credential is refused, never read as its fixture
            ("UQS_REQUIRE_LIVE_SOURCES", "1" if c.live else None),
            # the private ODBC setup deploy.env loads, for `uqs config sources check`
            ("UQS_ODBC_HOME", c.odbc_home),
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
        return script(*(f"{k}={q(v)}" for k, v in values.items())) + PREFLIGHT

    def preflight(self) -> dict[str, str]:
        r = self.remote.run(self.preflight_script(), self.cfg.command_timeout, "preflight")
        out = _checked(r, "preflight", "the preflight checks")
        facts = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
        if self.cfg.remote_user and facts.get("user") != self.cfg.remote_user:
            raise DeployError(
                "preflight",
                f"the deployment steps run as {facts.get('user') or 'an unknown user'}, "
                f"not {self.cfg.remote_user}",
            )
        problems = compatible(self.target, facts)
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


#: The preflight checks, run on the server. Read-only - safe under --dry-run.
#: Each failure says what is missing on stderr and exits 1; the facts the
#: caller decides on (data present, a current release, a held lock) come
#: back on stdout as name=value lines.
PREFLIGHT = r"""
fail() { echo "$*" >&2; exit 1; }
echo "user=$(id -un)"
if [ -n "$expected_user" ] && [ "$(id -un)" != "$expected_user" ]; then
  fail "running as $(id -un), not $expected_user"
fi
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
me=$(id -un)
# QHOME: --qhome, else this account's own $QHOME - never the caller's.
if [ -n "$qhome_flag" ]; then qhome_eff=$qhome_flag; qhome_from="--qhome"
elif [ -n "${QHOME:-}" ]; then qhome_eff=$QHOME; qhome_from="$me's QHOME"
else fail "QHOME is not set for $me and --qhome was not given -" \
  "pass --qhome <the directory q finds its licence in>"
fi
test -d "$qhome_eff" || fail "QHOME $qhome_eff ($qhome_from) is not a directory"
# QCMD: --qcmd, else $QCMD, else q on this account's PATH. A name or a path,
# never a command line: it is run as one word, quoted.
if [ -n "$qcmd_flag" ]; then qcmd_want=$qcmd_flag; qcmd_from="--qcmd"
elif [ -n "${QCMD:-}" ]; then qcmd_want=$QCMD; qcmd_from="$me's QCMD"
else qcmd_want=q; qcmd_from="q on $me's PATH"
fi
case "$qcmd_want" in
  /*) test -f "$qcmd_want" && test -x "$qcmd_want" ||
        fail "QCMD $qcmd_want ($qcmd_from) is not an executable file"
      qcmd_eff=$qcmd_want;;
  */*) fail "QCMD $qcmd_want ($qcmd_from) is a relative path - give an absolute one";;
  *[[:space:]]*) fail "QCMD '$qcmd_want' ($qcmd_from) is not an executable name -" \
                   "a command with arguments is not supported; pass --qcmd";;
  *) qcmd_eff=$(command -v -- "$qcmd_want") ||
       fail "QCMD $qcmd_want ($qcmd_from) is not on $me's PATH - pass --qcmd"
     case "$qcmd_eff" in
       /*) ;;
       *) fail "QCMD $qcmd_want ($qcmd_from) is a shell alias or function, not an executable";;
     esac;;
esac
case "$qhome_eff$qcmd_eff" in *$'\n'*) fail "QHOME or QCMD holds a newline";; esac
export QHOME="$qhome_eff"
echo "qhome=$qhome_eff"
echo "qhome_from=$qhome_from"
echo "qcmd=$qcmd_eff"
echo "qcmd_from=$qcmd_from"
probe=$(mktemp -d)
printf '%s\n' '-1 "DEPLOY_Q_OK ",string .z.K; exit 0' > "$probe/probe.q"
out=$(timeout "$probe_timeout" "$qcmd_eff" "$probe/probe.q" -q 2>&1 || true)
rm -rf "$probe"
case "$out" in
  *DEPLOY_Q_OK*)
    qversion=$(printf '%s\n' "$out" | sed -n 's/.*DEPLOY_Q_OK \([0-9.]*\).*/\1/p' | head -1)
    echo "qversion=$qversion";;
  *) fail "$qcmd_eff did not run a script with QHOME=$qhome_eff - is it licensed?" \
          "$(echo "$out" | tail -3)";;
esac
if [ -n "$odbc_home" ]; then
  test -f "$odbc_home/current/env.sh" ||
    fail "no ODBC setup at $odbc_home - run \`uqs odbc install\` there first"
fi
if [ -n "$torq_launcher" ]; then
  test -f "$torq_launcher" || fail "no TorQ launcher $torq_launcher"
  test -x "$torq_launcher" || fail "the TorQ launcher $torq_launcher is not executable"
fi
if [ -n "$torq_home" ]; then
  test -f "$torq_home/torq.q" || fail "no torq.q in $torq_home"
  if [ -z "$torq_launcher" ]; then
    test -f "$torq_home/torq.sh" || fail "no torq.sh in $torq_home - pass --torq-launcher" \
      "if this site keeps its launcher elsewhere"
  fi
fi
if [ -n "$torq_app_home" ]; then
  for f in database.q appconfig/process.csv; do
    test -f "$torq_app_home/$f" || fail "no $f in $torq_app_home"
  done
fi
if [ -d "$data" ]; then echo "data=present"; else echo "data=absent"; fi
if [ -L "$current" ]; then echo "current=$(basename "$(readlink "$current")")"; fi
if [ -n "$release_dir" ] && [ -e "$release_dir" ]; then echo "release_exists=yes"; fi
if [ -d "$lock" ]; then
  hb=$(cat "$lock/heartbeat" 2>/dev/null || stat -c %Y "$lock" 2>/dev/null || true)
  case "$hb" in ''|*[!0-9]*) age=unknown;; *) age=$(( $(date +%s) - hb ));; esac
  echo "locked=$(head -n 1 "$lock/owner" 2>/dev/null || echo unknown), last beat ${age}s ago"
fi
"""
