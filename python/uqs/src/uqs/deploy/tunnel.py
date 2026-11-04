"""`uqs query --tunnel NAME`: a process on a deployed target, reached from
this machine through an ssh tunnel (#1561).

The global `uqs --target NAME query ...` (deploy/control.py) runs `uqs query`
ON the server. This is the other direction: the query runs here, and only
the bytes cross, over `ssh -N -L 127.0.0.1:<free>:127.0.0.1:<port>` - so an
interactive session gets this terminal's line editing and `--export` writes
a file on this machine.

Two things come from the server, and nothing else is assumed:

  - the ports, from the release's own `uqs process list --json`, run as
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
#: How long the remote `uqs process list` may take, past ssh's own connect.
LIST_TIMEOUT = 60
#: How long the tunnel may take to accept, past ssh's ConnectTimeout.
READY_GRACE = 5.0

Runner = Callable[..., subprocess.CompletedProcess]
#: subprocess.Popen, or a test's stand-in with its poll/terminate/wait.
Popen = Callable[..., Any]


#: A click usage error - an older release, which has no `uqs process list`.
USAGE_ERROR = 2


def list_args(runtime: str | None, base_port: int | None, current: bool = True) -> list[str]:
    """The remote uqs's argv for its process list: the runtime only when one
    was chosen here, so the release's own deploy.env otherwise decides. A
    release from before #1858 lists them as `uqs process list` (`current`
    False)."""
    args = ["--runtime", runtime] if runtime else []
    args += ["process", "list", "--json"] if current else ["list", "processes", "--json"]
    return args + (["--base-port", str(base_port)] if base_port is not None else [])


def remote_ports(
    r: control.Remote,
    runtime: str | None,
    base_port: int | None = None,
    runner: Runner = subprocess.run,
) -> dict[str, int]:
    """procname -> port, as the target's deployed release resolves them: by
    `uqs process list`, or on a release from before it `uqs process list`."""
    return {row["process"]: row["port"] for row in remote_processes(r, runtime, base_port, runner)}


def remote_processes(
    r: control.Remote,
    runtime: str | None,
    base_port: int | None = None,
    runner: Runner = subprocess.run,
) -> list[dict[str, Any]]:
    """Every process the target's deployed release declares, as
    `{process, proctype, port}` - proctype "" where a release's list did not
    carry it. The one discovery both `query --tunnel` and the tunnel plan
    (deploy/tunnel_plan.py) read."""
    try:
        done = _list(r, list_args(runtime, base_port), runner)
        if done.returncode == USAGE_ERROR:
            done = _list(r, list_args(runtime, base_port, current=False), runner)
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
    return _parse_rows(r, done.stdout or "")


def _list(r: control.Remote, args: list[str], runner: Runner) -> subprocess.CompletedProcess:
    return runner(
        control.ssh_argv(r, args, tty=False),
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
        timeout=r.connect_timeout + LIST_TIMEOUT,
    )


def _parse_rows(r: control.Remote, out: str) -> list[dict[str, Any]]:
    """The document `uqs process list --json` prints, from where it starts:
    `{"processes": [{process, port}]}` (#1882), or the bare list of
    `{procname, port}` a release from before it printed - the release on the
    server is whatever was deployed there, not this checkout."""
    start = min((i for i in (out.find("{"), out.find("[")) if i >= 0), default=-1)
    try:
        doc = json.loads(out[start:]) if start >= 0 else []
        rows: list = doc["processes"] if isinstance(doc, dict) else doc
        found = [
            {
                "process": str(row.get("process", row.get("procname"))),
                "proctype": str(row.get("proctype") or ""),
                "port": int(row["port"]),
            }
            for row in rows
        ]
    except ValueError, TypeError, KeyError, AttributeError:
        raise UqsError(
            f"target {r.name}: its release did not list its processes as JSON - "
            f"`uqs --target {r.name} process list --json` shows what it printed; "
            "a release older than `list --json` needs --port"
        ) from None
    if not found:
        raise UqsError(f"target {r.name}: its release declares no processes")
    return found


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
    return forwards_argv(r.host, r.connect_timeout, [(local, remote)])


def forwards_argv(
    host: str, connect_timeout: int, forwards: Sequence[tuple[int, int]]
) -> list[str]:
    """ssh holding one `-L` per (local, remote) pair, loopback at both ends,
    with the deploy transport's options - the one spelling of a tunnel, for
    `query --tunnel`'s own and for the launch command a tunnel plan prints."""
    opts = ["-o", "BatchMode=yes", "-o", f"ConnectTimeout={connect_timeout}"]
    opts += ["-o", "ExitOnForwardFailure=yes", "-N", "-n"]
    pairs = [w for local, remote in forwards for w in ("-L", forward_spec(local, remote))]
    return ["ssh", *opts, *pairs, host]


def forward_spec(local: int, remote: int) -> str:
    return f"{LOOPBACK}:{local}:{LOOPBACK}:{remote}"


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
    with forwarded(r, [(local, remote)], popen=popen, ready=ready, wait=wait):
        yield local


@contextmanager
def forwarded(
    r: control.Remote,
    forwards: Sequence[tuple[int, int]],
    *,
    popen: Popen = subprocess.Popen,
    ready: Callable[[int], bool] = _accepts,
    wait: float | None = None,
) -> Iterator[None]:
    """One ssh holding every (local, remote) forward, for the `with` block:
    ready once each local port accepts, ended however the block is left."""
    argv = forwards_argv(r.host, r.connect_timeout, forwards)
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
        _wait_ready(r, proc, forwards, ready, wait)
        yield
    finally:
        _stop(proc)


def _wait_ready(
    r: control.Remote,
    proc: subprocess.Popen,
    forwards: Sequence[tuple[int, int]],
    ready: Callable[[int], bool],
    wait: float | None,
) -> None:
    limit = time.monotonic() + (r.connect_timeout + READY_GRACE if wait is None else wait)
    ports = ", ".join(str(remote) for _local, remote in forwards)
    which = f"port{'s' if len(forwards) > 1 else ''} {ports}"
    pending = [local for local, _remote in forwards]
    while True:
        code = proc.poll()
        if code is not None:
            said = _stderr(proc)
            raise UqsError(
                f"could not open an ssh tunnel to target {r.name} ({r.host}) for {which} "
                f"(ssh exit {code}){': ' + said if said else ''} - check that "
                f"`ssh -o BatchMode=yes {r.host} true` works"
            )
        pending = [local for local in pending if not ready(local)]
        if not pending:
            return
        if time.monotonic() >= limit:
            raise UqsError(
                f"the ssh tunnel to target {r.name} ({r.host}) for {which} "
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
