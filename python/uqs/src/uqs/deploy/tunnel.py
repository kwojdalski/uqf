"""`uqs query --tunnel NAME`: a process on a deployed target, reached from
this machine through an ssh tunnel (#1561).

The global `uqs --target NAME query ...` (deploy/control.py) runs `uqs query`
ON the server. This is the other direction: the query runs here, and only
the bytes cross, over `ssh -N -L 127.0.0.1:<free>:127.0.0.1:<port>` - so an
interactive session gets this terminal's line editing and `--export` writes
a file on this machine.

Two things come from the server, and nothing else is assumed:

  - the ports, from the release's own `uqs list processes --json`, run as
    control.py runs every forwarded command (the release's deploy.env, its
    service account) - so they are what that release's process_ports.csv,
    port lock and runtime base port resolve to, never this checkout's;
  - the connection, through ssh with the options the deploy transport uses
    (BatchMode, ConnectTimeout), plus ExitOnForwardFailure, so a forward that
    cannot be set up ends ssh instead of leaving a tunnel to nowhere.

The tunnel's ssh runs in its own session, so a Ctrl-C meant for qcon or the
gateway session does not reach it; `tunnel()` ends it on every way out.
"""

from __future__ import annotations

import json
import socket
import subprocess
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from uqs.deploy import control
from uqs.deploy.remote import diagnostic
from uqs.paths import UqsError

#: The loopback address both ends of the tunnel use.
LOOPBACK = "127.0.0.1"
#: How long the remote `uqs list processes` may take, past ssh's own connect.
LIST_TIMEOUT = 60
#: How long the tunnel may take to accept, past ssh's ConnectTimeout.
READY_GRACE = 5.0

Runner = Callable[..., subprocess.CompletedProcess]
#: subprocess.Popen, or a test's stand-in with its poll/terminate/wait.
Popen = Callable[..., Any]


def list_args(runtime: str | None, base_port: int | None) -> list[str]:
    """The remote uqs's argv for its process list: the runtime only when one
    was chosen here, so the release's own deploy.env otherwise decides."""
    args = ["--runtime", runtime] if runtime else []
    args += ["list", "processes", "--json"]
    return args + (["--base-port", str(base_port)] if base_port is not None else [])


def remote_ports(
    r: control.Remote,
    runtime: str | None,
    base_port: int | None = None,
    runner: Runner = subprocess.run,
) -> dict[str, int]:
    """procname -> port, as the target's deployed release resolves them."""
    argv = control.ssh_argv(r, list_args(runtime, base_port), tty=False)
    try:
        done = runner(
            argv,
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=r.connect_timeout + LIST_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise UqsError(
            f"target {r.name}: listing its processes did not finish within "
            f"{r.connect_timeout + LIST_TIMEOUT}s"
        ) from None
    except OSError as exc:
        raise UqsError(f"could not run ssh: {exc}") from None
    control.refuse_unreached(r, done.returncode)
    if done.returncode:
        said = diagnostic(done) or "nothing"
        raise UqsError(
            f"target {r.name}: could not list its processes (exit {done.returncode}): {said}"
        )
    return _parse_ports(r, done.stdout or "")


def _parse_ports(r: control.Remote, out: str) -> dict[str, int]:
    """The JSON list `uqs list processes --json` prints, from where it starts."""
    start = out.find("[")
    try:
        rows: list = json.loads(out[start:]) if start >= 0 else []
        ports = {str(row["procname"]): int(row["port"]) for row in rows}
    except ValueError, TypeError, KeyError:
        raise UqsError(
            f"target {r.name}: its release did not list its processes as JSON - "
            f"`uqs --target {r.name} list processes --json` shows what it printed; "
            "a release older than `list --json` needs --port"
        ) from None
    if not ports:
        raise UqsError(f"target {r.name}: its release declares no processes")
    return ports


def remote_port(
    ports: dict[str, int], procname: str, r: control.Remote, runtime: str | None
) -> int:
    """The port of `procname` on the target, refusing one it does not declare."""
    if procname not in ports:
        on = f"target {r.name}" + (f" (runtime {runtime})" if runtime else "")
        raise UqsError(
            f"{procname} is not a declared process on {on} - it declares: "
            f"{', '.join(sorted(ports))}"
        )
    return ports[procname]


def free_port() -> int:
    """A loopback port nothing listens on now: the one the OS gives port 0."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((LOOPBACK, 0))
        return int(s.getsockname()[1])


def tunnel_argv(r: control.Remote, local: int, remote: int) -> list[str]:
    opts = ["-o", "BatchMode=yes", "-o", f"ConnectTimeout={r.connect_timeout}"]
    opts += ["-o", "ExitOnForwardFailure=yes", "-N", "-n"]
    return ["ssh", *opts, "-L", f"{LOOPBACK}:{local}:{LOOPBACK}:{remote}", r.host]


def _accepts(port: int) -> bool:
    try:
        with socket.create_connection((LOOPBACK, port), timeout=0.5):
            return True
    except OSError:
        return False


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


@contextmanager
def tunnel(
    r: control.Remote,
    remote: int,
    *,
    popen: Popen = subprocess.Popen,
    ready: Callable[[int], bool] = _accepts,
    local: int | None = None,
    wait: float | None = None,
) -> Iterator[int]:
    """The local port forwarded to `remote` on the target, for the `with`
    block; the ssh behind it is ended however the block is left."""
    local = free_port() if local is None else local
    argv = tunnel_argv(r, local, remote)
    try:
        proc = popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except OSError as exc:
        raise UqsError(f"could not run ssh: {exc}") from None
    try:
        _wait_ready(r, proc, local, remote, ready, wait)
        yield local
    finally:
        _stop(proc)


def _wait_ready(
    r: control.Remote,
    proc: subprocess.Popen,
    local: int,
    remote: int,
    ready: Callable[[int], bool],
    wait: float | None,
) -> None:
    limit = time.monotonic() + (r.connect_timeout + READY_GRACE if wait is None else wait)
    while True:
        code = proc.poll()
        if code is not None:
            said = _stderr(proc)
            raise UqsError(
                f"could not open an ssh tunnel to target {r.name} ({r.host}) for port "
                f"{remote} (ssh exit {code}){': ' + said if said else ''} - check that "
                f"`ssh -o BatchMode=yes {r.host} true` works"
            )
        if ready(local):
            return
        if time.monotonic() >= limit:
            raise UqsError(
                f"the ssh tunnel to target {r.name} ({r.host}) for port {remote} "
                "did not start accepting connections in time"
            )
        time.sleep(0.1)


def _stderr(proc: subprocess.Popen) -> str:
    """What ssh said - read through remote.diagnostic, so without its own
    notices - at most a few lines. ssh has exited, so this does not wait."""
    try:
        _out, err = proc.communicate(timeout=2)
    except OSError, ValueError, subprocess.TimeoutExpired:
        err = ""
    done = subprocess.CompletedProcess(proc.args, proc.returncode or 0, "", err or "")
    return diagnostic(done, lines=5).replace("\n", "; ")


def scrub(text: str, secrets: Sequence[str]) -> str:
    """`text` with each non-empty secret masked - for an error raised by a
    connection that was handed the password."""
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text
