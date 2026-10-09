"""ssh and scp to the server, and the quoting every remote script relies on."""

from __future__ import annotations

import re
import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from uqs.deploy.config import DeployError, redact

Runner = Callable[..., subprocess.CompletedProcess]


class Transport(Protocol):
    """What a deployment needs of the server: run a script, copy a file.
    Remote is the real one; a test passes a fake that records each script."""

    def run(
        self, script: str, timeout: int, stage: str, as_login: bool = False
    ) -> subprocess.CompletedProcess: ...

    def put(self, local: Path, remote_path: str, timeout: int, stage: str) -> None: ...


class Remote:
    """ssh and scp to one host, under the operator's own configuration.

    BatchMode makes a missing key or an unknown host key FAIL instead of
    prompting; host-key checking stays whatever ssh_config says. Scripts go
    in on stdin to `bash -s`, so the only text the remote login shell parses
    is fixed - every value is quoted inside the script with shlex.quote.
    """

    def __init__(
        self,
        host: str,
        connect_timeout: int,
        runner: Runner = subprocess.run,
        remote_user: str | None = None,
    ) -> None:
        self.host = host
        self.remote_user = remote_user
        self.options = ["-o", "BatchMode=yes", "-o", f"ConnectTimeout={connect_timeout}"]
        self.runner = runner

    def ssh_argv(self, as_login: bool = False) -> list[str]:
        """ssh's argv: the script as the service user, unless `as_login`.

        The one string the login shell parses is fixed apart from the account
        name, which parse_args validated and which is quoted anyway.
        """
        if self.remote_user and not as_login:
            command = f"sudo -n -iu {q(self.remote_user)} bash -s"
        elif self.remote_user:
            # the upload steps: plain, they read nothing from the environment
            command = "bash -s"
        else:
            # a login shell, like sudo -i: the account's own QHOME, QCMD, PATH
            command = "bash -l -s"
        return ["ssh", *self.options, self.host, command]

    def run(
        self, script: str, timeout: int, stage: str, as_login: bool = False
    ) -> subprocess.CompletedProcess:
        try:
            return self.runner(
                self.ssh_argv(as_login),
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


#: What ssh itself writes to stderr around a command, which is never the
#: command's own failure. Kept out of a diagnostic: a host-key notice or a
#: key-exchange warning used to stand where `uqs start`'s refusal belonged (#936).
SSH_NOISE = re.compile(
    r"^(Warning: Permanently added .*|Connection to \S+ closed\.?"
    r"|Pseudo-terminal will not be allocated.*|\*\* .*"
    r"|WARNING: connection is not using a post-quantum .*"
    r"|.*may be vulnerable to \"store now, decrypt later\".*"
    r"|.*server may need to be upgraded.*)$"
)


def diagnostic(r: subprocess.CompletedProcess, lines: int = 15) -> str:
    """What a failed remote command said: its stderr without ssh's own notices,
    then its stdout - a refusal printed on either survives, where taking
    stderr alone dropped stdout whenever ssh had written a warning."""
    err = [ln for ln in (r.stderr or "").splitlines() if ln.strip() and not SSH_NOISE.match(ln)]
    out = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
    return redact("\n".join((out + err)[-lines:]))


def _checked(r: subprocess.CompletedProcess, stage: str, what: str) -> str:
    if r.returncode:
        raise DeployError(stage, f"{what} failed (exit {r.returncode}): " + diagnostic(r))
    return r.stdout
