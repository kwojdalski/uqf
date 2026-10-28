"""hdb_alter.q on another machine, over ssh, with that machine's own q (#1755).

An attached stack's HDB is on the stack's host, and that host need not have
uqf: a production TorQ server has q and TorQ. So the script and the vendored
dbmaint.q it loads go there for one run and are gone after it:

  1. one ssh session, a login shell (`bash -l -s`, the account's own QHOME,
     QCMD and PATH - as deploy/remote.py runs its steps), reading a script on
     stdin;
  2. that script makes a temporary directory, writes the files into it in
     the layout hdb_alter.q finds dbmaint by (scripts/gates/, lib/kx-dbmaint/),
     runs `$QCMD` (else `q`) on it, and removes the directory on any exit.

The files travel inside the script as base64 heredocs, so nothing but the
fixed ssh command line is parsed by the remote login shell before bash reads
stdin, and every argument is quoted with shlex. ssh runs with BatchMode, so a
missing key or an unknown host key fails rather than prompts; its own failure
(exit 255) is told apart from the script's, and named as the setting to fix.
"""

from __future__ import annotations

import base64
import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from uqs.paths import UqsError
from uqs.stack.hdb_alter import DBMAINT, SCRIPT, TIMEOUT_S, Request, args, parse

#: What ssh exits with when it, rather than the remote command, failed.
SSH_FAILED = 255
#: The script's own exits when the host cannot run it at all.
NO_TEMP, NO_Q = 97, 98
CONNECT_TIMEOUT_S = 10
#: The remote copy of a declaration, under the temporary directory.
SCHEMA_NAME = "database.q"

Runner = Callable[..., subprocess.CompletedProcess]


def _heredoc(target: str, data: bytes) -> str:
    """Write `data` to "$tmp/<target>" from an inline base64 block."""
    encoded = base64.b64encode(data).decode()
    lines = [encoded[i : i + 76] for i in range(0, len(encoded), 76)] or [""]
    return f"base64 -d > \"$tmp/{target}\" <<'UQF_EOF'\n" + "\n".join(lines) + "\nUQF_EOF\n"


def remote_script(repo_root: Path, req: Request, *, apply: bool) -> str:
    """The bash script the stack's host runs: copy, run, clean up, and exit
    with the q script's own code."""
    files = {
        f"scripts/{SCRIPT.as_posix()}": (repo_root / "scripts" / SCRIPT).read_bytes(),
        DBMAINT.as_posix(): (repo_root / DBMAINT).read_bytes(),
    }
    if req.schema is not None:
        files[SCHEMA_NAME] = req.schema.read_bytes()
    argv = args(req, apply=apply, schema_path="SCHEMA_PLACEHOLDER" if req.schema else None)
    quoted = " ".join(
        '"$tmp/' + SCHEMA_NAME + '"' if a == "SCHEMA_PLACEHOLDER" else shlex.quote(a) for a in argv
    )
    body = [
        "set -u",
        'tmp=$(mktemp -d "${TMPDIR:-/tmp}/uqf-hdb-alter.XXXXXX") || exit ' + str(NO_TEMP),
        "trap 'rm -rf \"$tmp\"' EXIT",
        'mkdir -p "$tmp/scripts/gates" "$tmp/lib/kx-dbmaint"',
        *(_heredoc(target, data) for target, data in files.items()),
        'q_cmd="${QCMD:-q}"',
        'if ! command -v "${q_cmd%% *}" >/dev/null 2>&1; then',
        '  echo "no q on this host: $q_cmd is not on PATH - set QCMD" >&2; exit ' + str(NO_Q),
        "fi",
        f'$q_cmd "$tmp/scripts/{SCRIPT.as_posix()}" {quoted} < /dev/null',
    ]
    return "\n".join(body) + "\n"


def run_ssh(
    destination: str,
    setting: str,
    repo_root: Path,
    req: Request,
    *,
    apply: bool,
    runner: Runner = subprocess.run,
    connect_timeout: int = CONNECT_TIMEOUT_S,
) -> dict[str, Any]:
    """Run the script on `destination` and read its JSON back. `setting`
    names where the destination came from, for the refusal when ssh cannot
    connect."""
    argv = [
        "ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={connect_timeout}",
        destination, "bash -l -s",
    ]  # fmt: skip
    try:
        result = runner(
            argv,
            input=remote_script(repo_root, req, apply=apply),
            capture_output=True,
            text=True,
            check=False,
            timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        raise UqsError(f"hdb_alter.q on {destination} did not finish within {TIMEOUT_S}s") from None
    except OSError as exc:
        raise UqsError(f"could not run ssh: {exc}") from None
    if result.returncode == SSH_FAILED:
        said = " ".join((result.stderr or "").strip().splitlines()[-2:])
        raise UqsError(
            f"cannot reach {destination} over ssh ({setting}): {said or 'ssh exited 255'} - "
            f"check that `ssh -o BatchMode=yes {destination} true` works, or set it with "
            "`uqs job attach NAME --ssh USER@HOST`. Nothing was run."
        )
    if result.returncode in (NO_TEMP, NO_Q):
        said = (result.stderr or "").strip() or "no temporary directory"
        raise UqsError(f"{destination} cannot run hdb_alter.q: {said}. Nothing was run.")
    return parse(result, f"on {destination}")
