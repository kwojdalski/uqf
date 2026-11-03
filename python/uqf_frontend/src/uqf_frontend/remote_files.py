"""The file browser: list a directory under a registered target's deployment
root, over ssh, with the release's own Python (#1950).

WHAT RUNS. remote_helper.py's source goes in on ssh's stdin to
`<dest>/current/.venv/bin/python -I -`, the interpreter `uqs deploy push`
installed with the release. Nothing else is tried: a release without that
interpreter is refused with NO_PYTHON and its own message, and nothing is
installed. The helper reads one directory and one deploy report; it never
deploys, starts, stops or writes anything.

WHERE. Only a target the registry declares (deploy_targets.toml, read as
GET /services reads it) can be browsed, by its name: the ssh destination,
deployment root, service account and connect timeout are the registry's,
held to the shapes `uqs --target` holds them to (control.remote_from). No
host or root ever comes from the request.

QUOTING. The browse path never reaches a shell. The request travels as
base64 of its JSON - a fixed alphabet - in the one argument the helper takes,
and the only other values in the remote command are the registry's dest and
remote_user, each quoted with shlex.quote, through the same `sudo -n -iu
USER bash -c` (or `bash -c`) wrapper `uqs --target` uses. ssh runs with
BatchMode and ConnectTimeout, so a missing key or unknown host key fails
instead of prompting (control.ssh_argv).

BOUNDS. A path is checked by the helper's own split_path before ssh is
opened, and again on the server. The listing is capped (remote_helper's
MAX_LIMIT and SCAN_CAP), the whole call by TIMEOUT, and the answer's size
by MAX_ANSWER.

Each failure says which side failed: ssh could not connect (255), no
release (NO_RELEASE), no release Python (NO_PYTHON), the helper refused the
path (its ERRORS word), or the helper itself failed.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import Any

from uqf_frontend import remote_helper, services
from uqf_frontend.config import Settings
from uqf_frontend.errors import FrontendError, ValidationFailed

Runner = Callable[..., subprocess.CompletedProcess]

#: Seconds one browse may take, connection included - inside the browser's
#: own wait (config.REQUEST_HEADROOM over the default gateway timeout).
TIMEOUT = 20
#: Entries a listing returns when the request does not say.
DEFAULT_LIMIT = 500
#: Bytes of helper output read as an answer; more is a failure, not a parse.
MAX_ANSWER = 8 * 1024 * 1024
#: Where the release's interpreter is, under the deployment root.
PYTHON = "current/.venv/bin/python"
#: ssh's own exit status when it, not the remote command, failed.
SSH_FAILED = 255
#: The remote command's exit statuses before the helper runs. Distinct from
#: launch.py's 64-69 except NO_RELEASE, which means the same thing there.
NO_RELEASE = 64
NO_PYTHON = 70


class TargetNotFound(FrontendError):
    """The name is not a target the registry declares."""

    status_code = 404


class BrowseRefused(FrontendError):
    """The helper refused the path: outside the root, a link, not a directory."""

    status_code = 400


class PathNotFound(FrontendError):
    status_code = 404


class TargetUnusable(FrontendError):
    """The target is declared but cannot be browsed: no release, no release
    Python, or a declaration the remote shape check refuses."""

    status_code = 409


class RemoteFailed(FrontendError):
    """ssh could not connect, timed out, or the helper failed."""

    status_code = 502


class RemoteTimedOut(FrontendError):
    status_code = 504
    transient = True


#: The helper's refusal words, as the API's errors.
_REFUSALS: dict[str, type[FrontendError]] = {
    "bad_request": ValidationFailed,
    "refused": BrowseRefused,
    "not_found": PathNotFound,
    "not_a_directory": BrowseRefused,
    "denied": BrowseRefused,
}


@cache
def helper_source() -> str:
    """The helper, as it is sent: this package's own copy, every time."""
    return Path(remote_helper.__file__).read_text(encoding="utf-8")


def check_path(path: str) -> None:
    """Refuse an absolute, traversing or malformed path before any ssh."""
    try:
        remote_helper.split_path(path)
    except remote_helper.Refusal as exc:
        raise _REFUSALS[exc.error](exc.detail) from None


def target(settings: Settings, name: str):  # noqa: ANN201 - control.Remote, imported lazily
    """The declared target `name`, as `uqs --target` would reach it."""
    from uqs.deploy import control, targets
    from uqs.deploy.config import DeployError
    from uqs.paths import UqsError

    where = services.root(settings)
    try:
        declared = targets.read(where) if targets.declaration_path(where).is_file() else {}
    except (DeployError, UqsError, OSError) as exc:
        raise TargetUnusable(f"the deployment registry cannot be read: {exc}") from None
    if name not in declared:
        raise TargetNotFound(
            f"no deployment target {name!r} in {targets.declaration_path(where)} - "
            "only registered targets can be browsed"
        )
    try:
        return control.remote_from(name, declared[name])
    except UqsError as exc:
        raise TargetUnusable(str(exc)) from None


def remote_command(dest: str, remote_user: str | None, encoded: str) -> str:
    """The one string the remote login shell parses. `encoded` is base64, and
    dest and remote_user come from the registry; each is quoted."""
    root = dest.rstrip("/") or "/"
    current = shlex.quote(f"{root.rstrip('/')}/current")
    python = shlex.quote(f"{root.rstrip('/')}/{PYTHON}")
    inner = (
        f"if [ ! -e {current} ]; then "
        f"echo uqf-browse: no deployed release at {current} >&2; exit {NO_RELEASE}; fi; "
        f"if [ ! -x {python} ]; then "
        f"echo uqf-browse: {python} is not an executable >&2; exit {NO_PYTHON}; fi; "
        f"exec {python} -I - {shlex.quote(encoded)}"
    )
    if remote_user:
        return f"sudo -n -iu {shlex.quote(remote_user)} bash -c {shlex.quote(inner)}"
    return f"bash -c {shlex.quote(inner)}"


def browse(
    settings: Settings, name: str, path: str, limit: int, runner: Runner = subprocess.run
) -> dict[str, Any]:
    """List `path` under target `name`'s deployment root: the helper's
    answer, with the root it was listed under."""
    from uqs.deploy import control
    from uqs.deploy.remote import diagnostic

    check_path(path)
    r = target(settings, name)
    root = r.dest.rstrip("/") or "/"
    request = {"protocol": remote_helper.PROTOCOL, "op": "list", "root": root,
               "path": path, "limit": limit}  # fmt: skip
    command = remote_command(r.dest, r.remote_user, remote_helper.encode(request))
    argv = control.ssh_argv(r, (), tty=False, command=command)
    try:
        done = runner(
            argv, input=helper_source(), capture_output=True, text=True,
            timeout=TIMEOUT, check=False,
        )  # fmt: skip
    except subprocess.TimeoutExpired:
        raise RemoteTimedOut(f"target {name}: browsing did not finish within {TIMEOUT}s") from None
    except OSError as exc:
        raise RemoteFailed(f"could not run ssh: {exc}") from None
    code = done.returncode
    if code == SSH_FAILED:
        raise RemoteFailed(
            f"could not reach target {name} ({r.host}) over ssh - check that "
            f"`ssh -o BatchMode=yes {r.host} true` works: a key loaded, the host key known"
        )
    if code == NO_RELEASE:
        raise TargetUnusable(f"target {name} has no deployed release under {root}")
    if code == NO_PYTHON:
        raise TargetUnusable(
            f"target {name}: {root}/{PYTHON} is missing or not executable. The browser "
            "runs only with the release's own Python and installs nothing - deploy the "
            "release with `uqs deploy push`, which creates it"
        )
    if code:
        said = diagnostic(done) or "nothing"
        raise RemoteFailed(f"target {name}: the browser helper failed (exit {code}): {said}")
    return {**_answer(name, done.stdout or ""), "root": root}


def _answer(name: str, stdout: str) -> dict[str, Any]:
    """The helper's JSON line - the last line, past anything a login
    profile printed - or the failure it names."""
    if len(stdout) > MAX_ANSWER:
        raise RemoteFailed(f"target {name}: the listing exceeded {MAX_ANSWER} bytes")
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    try:
        answer = json.loads(lines[-1]) if lines else None
    except ValueError:
        answer = None
    if not isinstance(answer, dict) or answer.get("protocol") != remote_helper.PROTOCOL:
        raise RemoteFailed(f"target {name}: the browser helper gave no answer it understands")
    if not answer.get("ok"):
        error = _REFUSALS.get(str(answer.get("error")), RemoteFailed)
        raise error(str(answer.get("detail") or "refused"))
    return answer
