"""`uqs target adopt`: drive a release copied onto a server by hand (#1756).

A release scp'd or rsync'd onto a server is not one `uqs --target` can run:
it expects `<dest>/current` to link to a release under `<dest>/releases/`,
and - unless that release has the .venv and deploy.env a push installs - a
target that runs it from source (`launch_mode = "source"`, deploy/launch.py)
with an interpreter that imports uqf's dependencies and an environment file.

adopt works that out by asking the server, in one read-only round trip:

  - does `<dest>/current` link to a release already; if not, which directory
    would become one (`--release`, default `<dest>` itself), moved to
    `<dest>/releases/adopted-<UTC time>` with `current` linked to it;
  - can that release launch uqs as pushed (.venv/bin/uqs and deploy.env), or
    must it run from source;
  - for source, the environment file (--launch-env-file, else the release's
    deploy.env, else its output/uqs/setenv.sh) and a Python that imports
    uqf's dependencies with that file loaded (--launch-python, else the
    release's .venv/bin/python, else python3), each candidate probed and
    reported.

A dry run stops there, having written nothing on either side. With --apply,
it makes the server changes (after asking), saves the target through the
same transaction as `uqs target add --reach` (deploy/target_changes.py), and
records the release as "adopted" in this machine's deployment history, so
`uqs deploy history` shows it and `uqs --target NAME` reaches it. A release
named adopted-* is not a release id, so `uqs deploy prune` never removes it.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime

from uqs.deploy import launch
from uqs.deploy.control import Remote, Runner, refuse_unreached
from uqs.deploy.remote import diagnostic
from uqs.paths import UqsError

#: Where a uqf checkout's own environment file is, as `uqs` writes it.
SETENV = "output/uqs/setenv.sh"
DEPLOY_ENV = "deploy.env"
VENV_PYTHON = ".venv/bin/python"
#: What a Python must import to run the release's uqs.
IMPORT = "import uqs.cli"


@dataclass
class Probe:
    """One candidate interpreter and what trying it said."""

    source: str  # where the candidate came from: --launch-python, the release, PATH
    given: str  # as given or found
    path: str | None  # resolved on the server; None when there is none
    ok: bool
    said: str


@dataclass
class Survey:
    """What the server said about adopting `release_dir` under `dest`."""

    dest: str
    current: str | None  # what <dest>/current links to, when it is a link
    release_dir: str  # the release: current's target, or what would become it
    exists: bool
    is_uqf: bool  # holds python/uqs/src/uqs
    pushed: bool  # .venv/bin/uqs and deploy.env: launches as a push installs it
    env_file: str | None  # the environment file found, as a path in the release or absolute
    env_source: str | None
    probes: list[Probe] = field(default_factory=list)
    release_name: str | None = None  # releases/<name> it would be moved to; None: already one

    @property
    def mode(self) -> str:
        return launch.RELEASE if self.pushed else launch.SOURCE

    @property
    def python(self) -> Probe | None:
        return next((p for p in self.probes if p.ok), None)


def _shell(r: Remote, inner: str) -> str:
    """`inner` as the target's account runs a step: its login shell."""
    if r.remote_user:
        return f"sudo -n -iu {shlex.quote(r.remote_user)} bash -c {shlex.quote(inner)}"
    return f"bash -lc {shlex.quote(inner)}"


def _ssh(r: Remote, inner: str, runner: Runner) -> subprocess.CompletedProcess:
    opts = ["-o", "BatchMode=yes", "-o", f"ConnectTimeout={r.connect_timeout}"]
    try:
        done = runner(
            ["ssh", *opts, r.host, _shell(r, inner)], capture_output=True, text=True, check=False
        )
    except OSError as exc:
        raise UqsError(f"could not run ssh: {exc}") from None
    refuse_unreached(r, done.returncode)
    return done


def survey_script(dest: str, release: str, python: str | None, env_file: str | None) -> str:
    """The read-only shell text that reports, one key=value per line, what
    adopting `release` under `dest` would find."""
    q = shlex.quote
    return f"""
dest={q(dest.rstrip("/"))}; rel={q(release.rstrip("/") or "/")}
py_flag={q(python or "")}; env_flag={q(env_file or "")}
if [ -L "$dest/current" ]; then
  echo "current=$(readlink "$dest/current")"
  R=$(cd "$dest/current" 2>/dev/null && pwd -P) || R="$dest/current"
else
  echo "current="; R=$rel
fi
echo "release_dir=$R"
[ -d "$R" ] && echo exists=1 || echo exists=0
[ -f "$R/{launch.SOURCE_DIR}/uqs/__main__.py" ] && echo is_uqf=1 || echo is_uqf=0
[ -x "$R/.venv/bin/uqs" ] && [ -r "$R/{DEPLOY_ENV}" ] && echo pushed=1 || echo pushed=0
env=
if [ -n "$env_flag" ]; then
  [ -r "$env_flag" ] && env=$env_flag; echo "env_source=--launch-env-file"
elif [ -r "$R/{DEPLOY_ENV}" ]; then env="$R/{DEPLOY_ENV}"; echo "env_source={DEPLOY_ENV}"
elif [ -r "$R/{SETENV}" ]; then env="$R/{SETENV}"; echo "env_source={SETENV}"
else echo env_source=
fi
echo "env_file=$env"
probe() {{
  ( cd "$R" 2>/dev/null || exit 0
    if [ -n "$env" ]; then . "$env" >/dev/null 2>&1 </dev/null; fi
    p=$2
    case $p in /*) ;; *) p=$(command -v "$p" 2>/dev/null) ;; esac
    if [ -z "$p" ] || [ ! -x "$p" ]; then echo "probe=$1|$2||missing|not an executable"; exit 0; fi
    out=$(PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$R/{launch.SOURCE_DIR}" \\
      "$p" -B -c {q(IMPORT)} 2>&1 </dev/null)
    if [ $? -eq 0 ]; then echo "probe=$1|$2|$p|ok|"
    else echo "probe=$1|$2|$p|fails|$(printf %s "$out" | tail -n 1)"; fi )
}}
[ -n "$py_flag" ] && probe --launch-python "$py_flag"
[ -e "$R/{VENV_PYTHON}" ] && probe release "$R/{VENV_PYTHON}"
probe PATH python3
"""


def survey(
    r: Remote,
    release: str | None,
    python: str | None,
    env_file: str | None,
    runner: Runner = subprocess.run,
    now: datetime | None = None,
) -> Survey:
    """Ask the server what adopting would find; change nothing."""
    for flag, value in (("--release", release), ("--launch-python", python),
                        ("--launch-env-file", env_file)):  # fmt: skip
        if value is not None and not value.startswith("/"):
            raise UqsError(f"{flag} {value!r} must be an absolute path on the server")
    done = _ssh(r, survey_script(r.dest, release or r.dest, python, env_file), runner)
    if done.returncode:
        said = diagnostic(done) or "nothing"
        raise UqsError(f"target {r.name}: the survey failed (exit {done.returncode}): {said}")
    facts: dict[str, str] = {}
    probes: list[Probe] = []
    for line in done.stdout.splitlines():
        key, _, value = line.partition("=")
        if key == "probe":
            source, given, path, verdict, said = (value.split("|", 4) + [""] * 5)[:5]
            probes.append(Probe(source, given, path or None, verdict == "ok", said))
        elif key:
            facts[key] = value
    current = facts.get("current") or None
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    return Survey(
        dest=r.dest.rstrip("/"),
        current=current,
        release_dir=facts.get("release_dir", ""),
        exists=facts.get("exists") == "1",
        is_uqf=facts.get("is_uqf") == "1",
        pushed=facts.get("pushed") == "1",
        env_file=facts.get("env_file") or None,
        env_source=facts.get("env_source") or None,
        probes=probes,
        release_name=None if current else f"adopted-{stamp}",
    )


def _in_current(s: Survey, path: str) -> str:
    """`path`, inside the release, as it is reached through <dest>/current
    once adopted - so the target follows current, not today's directory."""
    prefix = s.release_dir.rstrip("/") + "/"
    if path.startswith(prefix):
        return f"{s.dest}/current/{path[len(prefix) :]}"
    return path


def problems(s: Survey) -> list[str]:
    """Why the release cannot be adopted as it stands; none when it can."""
    if not s.exists:
        return [f"{s.release_dir} does not exist on the server"]
    out = []
    if s.mode == launch.SOURCE:
        if not s.is_uqf:
            out.append(
                f"{s.release_dir} has neither the .venv/bin/uqs and {DEPLOY_ENV} a push "
                f"installs nor {launch.SOURCE_DIR}/uqs to run from source - is it a uqf release?"
            )
        if s.env_file is None:
            out.append(
                f"no environment file: neither {DEPLOY_ENV} nor {SETENV} in the release, "
                "and no readable --launch-env-file"
            )
        if s.python is None:
            tried = "; ".join(
                f"{p.source} {p.path or p.given}: {p.said or 'missing'}" for p in s.probes
            )
            out.append(f"no Python on the server imports uqf's dependencies ({IMPORT}) - {tried}")
    return out


def block(s: Survey, r: Remote) -> dict:
    """The target's settings adopting would save."""
    entry: dict = {"host": r.host, "dest": r.dest, "remote_user": r.remote_user}
    if s.mode == launch.SOURCE and s.python is not None and s.env_file is not None:
        entry["launch_mode"] = launch.SOURCE
        entry["launch_python"] = _in_current(s, str(s.python.path))
        entry["launch_env_file"] = _in_current(s, s.env_file)
    return {k: v for k, v in entry.items() if v}


def server_changes(s: Survey) -> list[str]:
    """What --apply does on the server, in words; none when current is
    already a release."""
    if s.current is not None:
        return []
    new = f"{s.dest}/releases/{s.release_name}"
    if s.release_dir.rstrip("/") == s.dest:
        moved = f"move what {s.dest} holds (but releases/, current and shared/) into {new}"
    else:
        moved = f"move {s.release_dir} to {new}"
    return [moved, f"link {s.dest}/current -> releases/{s.release_name}"]


def apply_script(s: Survey) -> str:
    """The shell text that makes server_changes() - refused, changing
    nothing, when the release's place is taken or current appeared since."""
    q = shlex.quote
    dest, name = s.dest, str(s.release_name)
    new = f"{dest}/releases/{name}"
    if s.release_dir.rstrip("/") == dest:
        move = (
            f"mkdir {q(new)} && for f in {q(dest)}/* {q(dest)}/.[!.]* {q(dest)}/..?*; do "
            '[ -e "$f" ] || [ -L "$f" ] || continue; '
            'case "${f##*/}" in releases|current|shared) continue ;; esac; '
            f'mv "$f" {q(new)}/ || exit 1; done'
        )
    else:
        move = f"mv {q(s.release_dir)} {q(new)}"
    return (
        f"if [ -e {q(dest)}/current ] || [ -L {q(dest)}/current ]; then "
        f"echo uqs: {q(dest)}/current appeared since the survey >&2; exit 3; fi; "
        f"if [ -e {q(new)} ]; then echo uqs: {q(new)} already exists >&2; exit 3; fi; "
        f"mkdir -p {q(dest)}/releases && {move} && ln -s releases/{q(name)} {q(dest)}/current"
    )


def apply(r: Remote, s: Survey, runner: Runner = subprocess.run) -> None:
    """Make the server changes; raise, saying what the server said, if not."""
    if s.current is not None:
        return
    done = _ssh(r, apply_script(s), runner)
    if done.returncode:
        said = diagnostic(done) or "nothing"
        raise UqsError(
            f"target {r.name}: the server was not changed as planned "
            f"(exit {done.returncode}): {said}"
        )
