"""What a deployment is asked to do (`uqs deploy push`'s options, checked),
and the names and limits every stage shares."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from uqs.deploy.artifact import Artifact, ReleaseError, read_artifact
from uqs.stack import redact as stack_redact

#: Files an operator keeps on the server and every release links in, by their
#: path in the repository. Absent ones are simply not linked.
SHARED_CONFIG = (
    "scripts/torqconfig/permissions/gateway_users.csv",
    # which source connects to what; a row names a secret's variable, never
    # the secret (#718)
    "scripts/torqconfig/sources.csv",
)
#: The server's credentials, as NAME=VALUE lines, in shared/config/. Never in
#: an artifact: deploy.env sources it, so every process uqs starts inherits the
#: variables a sources.csv row names, and nothing prints them. Refused when
#: anyone but its owner may read it.
SECRETS = "secrets.env"

#: One-liners run with the server's python3: a file's sha256, and an atomic
#: rename (os.replace) - portable where `mv -T` and `sha256sum` are not.
SHA256_PY = "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())"
REPLACE_PY = "import os,sys; os.replace(sys.argv[1], sys.argv[2])"
#: stdin into a file that must not exist yet ("xb"), as whoever runs it: the
#: one thing the service user does with the login user's upload.
WRITE_NEW_PY = "import shutil,sys; shutil.copyfileobj(sys.stdin.buffer, open(sys.argv[1], 'xb'))"

REPORT = "deploy-report.json"
SMOKE_MARKER = "DEPLOY_SMOKE_OK"
VERIFY_MARKER = "DEPLOY_VERIFY_OK"

_DEST = re.compile(r"/[A-Za-z0-9._/-]*[A-Za-z0-9._-]")
_HOST = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._@-]*")
#: A POSIX-portable account name, as useradd accepts by default.
_USER = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
_PROFILE = re.compile(r"[a-z][a-z0-9_]*")
_JOB = re.compile(r"[a-z][a-z0-9_]*")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
#: What deploy.env sets itself, so --launcher-env may not: TorQ's own core
#: and generated configuration, q, and uqs's variables.
_DEPLOY_OWNED = frozenset({"TORQHOME", "TORQAPPHOME", "SETENV", "TORQPROCESSES", "QCMD", "QHOME"})


#: What a report or the verifier's output can fail to parse with, as named
#: tuples so each `except` says what it expects.
UNREADABLE_REPORT = (ValueError, KeyError, TypeError)
UNPARSED = (IndexError, ValueError)
UNPARSED_BUSY = (IndexError, ValueError, KeyError, TypeError, AttributeError)


class DeployError(Exception):
    """A deployment that cannot go on; `stage` says where it stopped."""

    def __init__(self, stage: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage


def load_artifact(path: str) -> Artifact:
    """The artifact, checked whole (uqs.deploy.artifact.read_artifact), or why not."""
    try:
        return read_artifact(Path(path))
    except ReleaseError as exc:
        raise DeployError(exc.stage, str(exc)) from None


def redact(text: str) -> str:
    """`text` with anything shaped like a secret assignment masked."""
    return stack_redact.redact(text)


@dataclass
class Config:
    host: str
    dest: str
    profile: str
    artifact: str = ""
    remote_user: str | None = None
    torq_home: str | None = None
    torq_app_home: str | None = None
    torq_launcher: str | None = None
    launcher_env: dict[str, str] = field(default_factory=dict)
    qcmd: str | None = None
    qhome: str | None = None
    data_dir: str | None = None
    dry_run: bool = False
    restart: bool = False
    init_data: bool = False
    jobs: tuple[str, ...] = ()
    live: bool = False
    #: a private ODBC setup on the server (uqs odbc install), loaded by deploy.env
    odbc_home: str | None = None
    #: sources checked live after verify, before activation (#840)
    live_check: tuple[str, ...] = ()
    live_check_timeout: int = 120
    connect_timeout: int = 10
    command_timeout: int = 900
    smoke_timeout: int = 120
    verify_timeout: int = 180
    #: deploy an artifact built from uncommitted changes (#872)
    allow_dirty: bool = False
    #: owner/name whose releases a tag ARTIFACT names; default: origin's
    release_repo: str | None = None

    #: fill the shared HDB's missing tables and columns in prepare (#870)
    fix_hdb: bool = False

    #: after activation, keep the newest this many releases (#866); None keeps all
    keep: int | None = None

    #: seconds to let data flow after verify, then require every started
    #: streaming job to have beaten and none to be failing (#869); None skips it
    soak: int | None = None
    #: remove a deploy lock whose holder has stopped beating (#867)
    break_lock: bool = False

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


def _q_path(name: str, value: str | None) -> str | None:
    """An explicit --qcmd/--qhome: absolute, and spaces allowed - it is quoted
    everywhere it goes. No `..`, and no control characters."""
    if value is None:
        return None
    parts = value.split("/")
    if not value.startswith("/") or ".." in parts or any(ord(ch) < 32 for ch in value):
        raise DeployError("arguments", f"{name} {value!r} must be an absolute path")
    return value.rstrip("/") or "/"


def _launcher_env(values: Sequence[str]) -> dict[str, str]:
    """--launcher-env NAME=VALUE, for a site launcher's own variables. A name
    deploy.env already sets, or one uqs reads, is refused rather than allowed
    to quietly win or lose."""
    env: dict[str, str] = {}
    for item in values:
        name, sep, value = item.partition("=")
        if not sep or not _ENV_NAME.fullmatch(name):
            raise DeployError("arguments", f"--launcher-env {item!r} must be NAME=VALUE")
        if name in _DEPLOY_OWNED or name.startswith("UQS_"):
            raise DeployError("arguments", f"--launcher-env {name} is set by the deployment itself")
        if any(ord(ch) < 32 for ch in value):
            raise DeployError("arguments", f"--launcher-env {name} has a control character")
        env[name] = value
    return env


def _required_absolute(name: str, value: str) -> str:
    path = _absolute(name, value)
    assert path is not None  # _absolute returns None only for None
    return path


def make_config(
    *,
    artifact: str,
    host: str,
    dest: str,
    profile: str,
    remote_user: str | None = None,
    torq_home: str | None = None,
    torq_app_home: str | None = None,
    torq_launcher: str | None = None,
    launcher_env: Sequence[str] | None = None,
    qcmd: str | None = None,
    qhome: str | None = None,
    data_dir: str | None = None,
    dry_run: bool = False,
    restart: bool = False,
    init_data: bool = False,
    jobs: str = "",
    live: bool = False,
    odbc_home: str | None = None,
    live_check: str = "",
    live_check_timeout: int = 120,
    connect_timeout: int = 10,
    command_timeout: int = 900,
    smoke_timeout: int = 120,
    verify_timeout: int = 180,
    allow_dirty: bool = False,
    release_repo: str | None = None,
    fix_hdb: bool = False,
    keep: int | None = None,
    soak: int | None = None,
    break_lock: bool = False,
) -> Config:
    """A Config from `uqs deploy push`'s options - each value checked, since
    every one of them ends up inside a script the server's shell runs."""
    if not _HOST.fullmatch(host):
        raise DeployError("arguments", f"--host {host!r} is not an ssh destination")
    if remote_user is not None and not _USER.fullmatch(remote_user):
        raise DeployError("arguments", f"--remote-user {remote_user!r} is not an account name")
    if not _PROFILE.fullmatch(profile):
        raise DeployError("arguments", f"--profile {profile!r} is not a profile name")
    timeouts = {
        "connect-timeout": connect_timeout,
        "command-timeout": command_timeout,
        "smoke-timeout": smoke_timeout,
        "verify-timeout": verify_timeout,
        "live-check-timeout": live_check_timeout,
    }
    for name, value in timeouts.items():
        if value <= 0:
            raise DeployError("arguments", f"--{name} must be positive")
    names = tuple(dict.fromkeys(j.strip() for j in jobs.split(",") if j.strip()))
    for job in names:
        if not _JOB.fullmatch(job):
            raise DeployError("arguments", f"--jobs {job!r} is not a job name")
    checked = tuple(dict.fromkeys(s.strip() for s in live_check.split(",") if s.strip()))
    for source in checked:
        if not _JOB.fullmatch(source):
            raise DeployError("arguments", f"--live-check {source!r} is not a source name")
    if release_repo is not None and not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", release_repo
    ):
        raise DeployError("arguments", f"--release-repo {release_repo!r} is not OWNER/NAME")
    if keep is not None and keep < 0:
        raise DeployError("arguments", "--keep must be 0 or more")
    if soak is not None and soak <= 0:
        raise DeployError("arguments", "--soak must be positive")
    path = _required_absolute("--dest", dest)
    if path == "/":
        raise DeployError("arguments", "--dest must not be /")
    return Config(
        host=host,
        dest=path,
        artifact=artifact,
        remote_user=remote_user,
        profile=profile,
        torq_home=_absolute("--torq-home", torq_home),
        torq_app_home=_absolute("--torq-app-home", torq_app_home),
        torq_launcher=_absolute("--torq-launcher", torq_launcher),
        launcher_env=_launcher_env(launcher_env or ()),
        qcmd=_q_path("--qcmd", qcmd),
        qhome=_q_path("--qhome", qhome),
        data_dir=_absolute("--data-dir", data_dir),
        dry_run=dry_run,
        restart=restart,
        init_data=init_data,
        jobs=names,
        live=live,
        odbc_home=_absolute("--odbc-home", odbc_home),
        live_check=checked,
        live_check_timeout=live_check_timeout,
        connect_timeout=connect_timeout,
        command_timeout=command_timeout,
        smoke_timeout=smoke_timeout,
        verify_timeout=verify_timeout,
        allow_dirty=allow_dirty,
        release_repo=release_repo,
        fix_hdb=fix_hdb,
        keep=keep,
        soak=soak,
        break_lock=break_lock,
    )
