"""`uqs --target NAME ...`: run a command on a deployed release, over ssh (#956).

The target is one already declared for `uqs deploy push --target`, in
deploy_targets.toml (deploy/targets.py), so a server is described once and
both pushed to and driven from that one entry. Only three of its keys matter
here: `host` (the ssh destination), `dest` (where push installed) and
`remote_user` (the service account push runs every step as), with
`connect_timeout` if it sets one. Nothing else about the stack is read on
this side. The command runs the release's own `<dest>/current/.venv/bin/uqs`
with that release's deploy.env loaded, exactly as the deployment runs it, so
processes, ports, jobs and configuration are the SERVER's - a workstation
whose checkout has moved on still drives what is actually deployed.

ssh runs as the deploy transport runs it (deploy/remote.py): BatchMode, so a
missing key or an unknown host key fails instead of prompting, host-key
checking left to the operator's ssh_config, and no credential on this side.

Three failures are told apart, because each needs a different fix:

  - ssh could not connect (its exit 255): the network, the key, the host key;
  - the target has no deployed release under its dest (NO_RELEASE);
  - the remote uqs failed: its own exit code and its own words, passed through.

A command that may change the server asks first, naming the target, or takes
`--yes`. READ_ONLY lists those that cannot; anything unlisted asks, so a new
command is safe before anyone classifies it.
"""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from uqs.deploy import targets
from uqs.deploy.config import DeployError
from uqs.paths import UqsError

#: The remote preflight's exit code when <dest>/current holds no release.
#: Outside what uqs itself exits with, and below the shell's 126 and up.
NO_RELEASE = 64
#: What ssh exits with when it, rather than the remote command, failed.
SSH_FAILED = 255
DEFAULT_CONNECT_TIMEOUT = 10

#: Commands that read and never change the server, as (command, subcommand);
#: None covers every subcommand. Anything else asks before it runs.
READ_ONLY: frozenset[tuple[str, str | None]] = frozenset(
    {
        ("summary", None),
        ("graph", None),
        ("gaps", None),
        ("query", None),
        ("schema", None),
        ("list", None),
        ("logs", None),
        ("run", "status"),
        ("run", "list"),
        ("run", "show"),
        ("run", "audit"),
        ("stream", "preview"),
        ("data", "hdb-check"),
        ("config", "get"),
        ("runtime", "diff"),
        ("feed", "status"),
        ("odbc", "status"),
        ("odbc", "env"),
        ("deploy", "status"),
        ("deploy", "verify"),
    }
)
#: Read-only three deep: `config sources check` reads, `config sources stub`
#: writes a file.
READ_ONLY_DEEP = frozenset({("config", "sources", "check")})


@dataclass(frozen=True)
class Remote:
    """What running a command on a target needs, from its declaration."""

    name: str
    host: str
    dest: str
    remote_user: str | None = None
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT

    def describe(self) -> str:
        who = f", as {self.remote_user}" if self.remote_user else ""
        return f"{self.host}:{self.dest}{who}"


def remote_for(name: str, root: Path) -> Remote:
    """Target `name` as a Remote, or a refusal naming what is missing."""
    try:
        declared = targets.read(root)
    except DeployError as exc:
        raise UqsError(str(exc)) from None
    if name not in declared:
        known = ", ".join(sorted(declared)) or "none"
        raise UqsError(
            f"no target {name!r} in {targets.declaration_path(root)} - it declares: {known}"
        )
    t = declared[name]
    absent = [k for k in ("host", "dest") if not t.get(k)]
    if absent:
        raise UqsError(f"target {name} sets no {', '.join(absent)}")
    host, dest, user = str(t["host"]), str(t["dest"]), t.get("remote_user")
    # The same shapes make_config holds a push to: both reach a remote shell.
    if host.startswith("-") or any(c.isspace() for c in host):
        raise UqsError(f"target {name}: host {host!r} is not an ssh destination")
    if not dest.startswith("/") or any(c.isspace() for c in dest):
        raise UqsError(f"target {name}: dest {dest!r} must be an absolute path without spaces")
    return Remote(
        name, host, dest, str(user) if user else None,
        int(t.get("connect_timeout", DEFAULT_CONNECT_TIMEOUT)),
    )  # fmt: skip


def split_global(argv: Sequence[str]) -> tuple[str | None, bool, list[str]]:
    """(--target's value, whether --yes was given, argv without either), read
    from the global options before the command - `uqs --debug --target uat
    summary`. Options after the command are the command's, left alone."""
    rest: list[str] = []
    target: str | None = None
    yes = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--target" and i + 1 < len(argv):
            target, i = argv[i + 1], i + 2
        elif a.startswith("--target="):
            target, i = a.split("=", 1)[1], i + 1
        elif a == "--yes":
            yes, i = True, i + 1
        elif a == "--runtime" and i + 1 < len(argv):
            rest += argv[i : i + 2]
            i += 2
        elif a.startswith("-"):
            rest.append(a)
            i += 1
        else:
            rest += argv[i:]
            break
    return target, yes, rest


def command_words(args: Sequence[str]) -> tuple[str, ...]:
    """Up to three command words: the leading non-option words of `args`,
    past the global options and --runtime's value."""
    words: list[str] = []
    skip = False
    for a in args:
        if skip:
            skip = False
        elif a == "--runtime":
            skip = True
        elif a.startswith("-"):
            if words:
                break
        else:
            words.append(a)
            if len(words) == 3:
                break
    return tuple(words)


def changes_the_server(args: Sequence[str]) -> bool:
    """Whether `args` may change the server. Help never does; an unclassified
    command is assumed to."""
    if "--help" in args or "-h" in args:
        return False
    words = command_words(args)
    if not words:
        return False
    if words in READ_ONLY_DEEP:
        return False
    sub = words[1] if len(words) > 1 else None
    if words[:2] == ("config", "sources"):
        return True
    return (words[0], None) not in READ_ONLY and (words[0], sub) not in READ_ONLY


def remote_command(r: Remote, args: Sequence[str]) -> str:
    """The one string the remote login shell runs: check there is a release,
    then the release's own uqs with its deploy.env. Every value is quoted."""
    current = shlex.quote(f"{r.dest.rstrip('/')}/current")
    inner = (
        f"if [ ! -L {current} ] || [ ! -x {current}/.venv/bin/uqs ]; then "
        f"echo uqs: no deployed release at {current} >&2; exit {NO_RELEASE}; fi; "
        f"cd {current} && . ./deploy.env && exec .venv/bin/uqs "
        + " ".join(shlex.quote(a) for a in args)
    ).rstrip()
    if r.remote_user:
        # as `uqs deploy push` runs every step: the service account's login
        return f"sudo -n -iu {shlex.quote(r.remote_user)} bash -c {shlex.quote(inner)}"
    # a login shell, like sudo -i: the account's own QHOME, QCMD and PATH
    return f"bash -lc {shlex.quote(inner)}"


def ssh_argv(r: Remote, args: Sequence[str], tty: bool) -> list[str]:
    opts = ["-o", "BatchMode=yes", "-o", f"ConnectTimeout={r.connect_timeout}"]
    # A terminal when there is one, so an interactive `query`, `logs -f` and
    # Ctrl-C behave as they do locally.
    return ["ssh", *opts, *(["-t"] if tty else []), r.host, remote_command(r, args)]


Runner = Callable[..., subprocess.CompletedProcess]


def run(r: Remote, args: Sequence[str], *, tty: bool, runner: Runner = subprocess.run) -> int:
    """Run `args` on `r` with its output streaming through; the remote exit
    code, after saying which side failed when it was not the command."""
    try:
        done = runner(ssh_argv(r, args, tty), check=False)
    except OSError as exc:
        raise UqsError(f"could not run ssh: {exc}") from None
    _refuse_unreached(r, done.returncode)
    return done.returncode


def _refuse_unreached(r: Remote, code: int) -> None:
    """Raise when `code` says the command never ran: ssh could not connect,
    or there is no release to run it in."""
    if code == SSH_FAILED:
        raise UqsError(
            f"could not reach target {r.name} ({r.host}) over ssh - check that "
            f"`ssh -o BatchMode=yes {r.host} true` works: a key loaded, the host key "
            "known, the host up"
        )
    if code == NO_RELEASE:
        raise UqsError(
            f"target {r.name} has no deployed release under {r.dest} - "
            "deploy one with `uqs deploy push`, or correct the target's dest"
        )


def current_release(r: Remote, runner: Runner = subprocess.run) -> str:
    """The release `r` runs - proving ssh, the account and the release in one
    round trip - or the refusal run() would give."""
    current = shlex.quote(f"{r.dest.rstrip('/')}/current")
    inner = f'if [ ! -L {current} ]; then exit {NO_RELEASE}; fi; basename "$(readlink {current})"'
    shell = (
        f"sudo -n -iu {shlex.quote(r.remote_user)} bash -c {shlex.quote(inner)}"
        if r.remote_user
        else f"bash -lc {shlex.quote(inner)}"
    )
    opts = ["-o", "BatchMode=yes", "-o", f"ConnectTimeout={r.connect_timeout}"]
    try:
        done = runner(["ssh", *opts, r.host, shell], capture_output=True, text=True, check=False)
    except OSError as exc:
        raise UqsError(f"could not run ssh: {exc}") from None
    _refuse_unreached(r, done.returncode)
    if done.returncode:
        said = (done.stderr or done.stdout or "").strip().splitlines()[-1:] or ["nothing"]
        raise UqsError(f"target {r.name}: the check failed (exit {done.returncode}): {said[0]}")
    return done.stdout.strip()
