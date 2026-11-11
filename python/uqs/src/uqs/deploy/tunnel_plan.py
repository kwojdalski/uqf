"""The tunnel plan: which of a target's processes to forward, and to which
local ports (#2030).

One contract for every caller - `uqs target tunnel plan`, `uqs target tunnel
check` (deploy/tunnel_check.py) and the frontend's Services view, which calls
`for_target` and holds no ssh of its own. A plan is a PURE function of the
target and of what its release declares (`plan`): nothing here opens a
connection except `discover`, which reads the declared processes the way
`query --tunnel` does (deploy/tunnel.py's `remote_processes`).

    mapping = {process, proctype, remote_host, remote_port, local_host, local_port,
               production_endpoint, local_endpoint, query_command}

  - process, proctype and remote_port are the release's own - `uqs process list
    --json` on the server, so never this checkout's ports or a list in the UI;
  - only an rdb, hdb, wdb or gateway proctype is forwarded
    (FORWARDED_PROCTYPES): those are the processes an operator reads from;
  - local_port is remote_port + the offset (LOCAL_OFFSET unless given), or the
    port `--local PROC:PORT` names - validated: in range, unprivileged, and
    no two mappings sharing one.

The launch command is the ssh `query --tunnel` runs (tunnel.forwards_argv),
holding every forward: the ssh destination and the ports, never a password -
the q credentials are not part of a tunnel.

Configured is not verified: a plan says what WOULD be forwarded. Whether a
mapping was ever proved is `last_success`, read from the record `uqs target
tunnel check` keeps (`annotate`), and a mapping is `verified`, `stale` or
`unverified` by it - never by the plan itself.
"""

from __future__ import annotations

import fcntl
import json
import os
import shlex
import socket
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from uqs.deploy import control, history, targets, tunnel
from uqs.paths import UqsError

#: The process types a plan forwards: what an operator reads data from.
FORWARDED_PROCTYPES = ("rdb", "hdb", "wdb", "gateway")
#: A local port is the remote one plus this, unless given: clear of a local
#: stack on the same base port, and predictable from the production port.
LOCAL_OFFSET = 10000
#: The lowest local port a plan binds: below it needs root.
MIN_LOCAL_PORT = 1024
MAX_PORT = 65535
#: A verified mapping older than this is stale: the process may have moved.
STALE_AFTER = timedelta(hours=1)
#: The record of each mapping's last successful check, beside the registry.
CHECKS = "tunnel_checks.json"
CHECKS_ENV = "UQS_TUNNEL_CHECKS"

Runner = Callable[..., subprocess.CompletedProcess]


class TunnelPlanError(UqsError):
    """A plan refused, with the kind of refusal a caller can act on:
    `discovery` (the release's processes could not be read), `unknown_process`,
    `unsupported_proctype`, `invalid_port` or `port_conflict`."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind

    def json_details(self) -> dict[str, Any]:
        return {"kind": self.kind}


@dataclass(frozen=True)
class Declared:
    """One process a release declares, as discovery read it."""

    process: str
    proctype: str
    port: int


@dataclass(frozen=True)
class TunnelMapping:
    process: str
    proctype: str
    remote_host: str
    remote_port: int
    local_host: str
    local_port: int
    production_endpoint: str
    local_endpoint: str
    #: A credential-free command that queries the process from here.
    query_command: str


@dataclass(frozen=True)
class TunnelPlan:
    target: str
    ssh: str
    mappings: list[TunnelMapping]
    launch_argv: list[str]
    launch_command: str
    release: str | None = None
    #: Declared processes of a forwarded proctype the plan did not select.
    available: list[str] = field(default_factory=list)


def plan(
    target: str,
    ssh: str,
    connect_timeout: int,
    declared: Sequence[Declared],
    selected: Sequence[str] = (),
    local: Mapping[str, int] | None = None,
    offset: int = LOCAL_OFFSET,
    release: str | None = None,
) -> TunnelPlan:
    """The plan for `selected` (every process of a forwarded proctype when none
    is named) among `declared`. Pure: refuses with a TunnelPlanError."""
    local = dict(local or {})
    by_name = {d.process: d for d in declared}
    forwardable = sorted(d.process for d in declared if d.proctype in FORWARDED_PROCTYPES)
    names = list(dict.fromkeys(selected)) or forwardable
    if not names:
        raise TunnelPlanError(
            "discovery",
            f"target {target}: its release declares no "
            f"{', '.join(FORWARDED_PROCTYPES)} process to forward",
        )
    unknown = [n for n in names if n not in by_name]
    if unknown:
        raise TunnelPlanError(
            "unknown_process",
            f"{', '.join(unknown)}: not declared on target {target} - it declares: "
            f"{', '.join(sorted(by_name))}",
        )
    other = [n for n in names if by_name[n].proctype not in FORWARDED_PROCTYPES]
    if other:
        said = ", ".join(f"{n} ({by_name[n].proctype or 'no proctype'})" for n in other)
        raise TunnelPlanError(
            "unsupported_proctype",
            f"{said}: a tunnel forwards only {', '.join(FORWARDED_PROCTYPES)} processes - "
            f"on target {target} those are: {', '.join(forwardable) or 'none'}",
        )
    stray = sorted(set(local) - set(names))
    if stray:
        raise TunnelPlanError(
            "invalid_port",
            f"--local names {', '.join(stray)}, which the plan does not forward",
        )
    host = history.server_of(ssh)
    mappings = []
    for name in names:
        d = by_name[name]
        port = local.get(name, d.port + offset)
        if not MIN_LOCAL_PORT <= port <= MAX_PORT:
            raise TunnelPlanError(
                "invalid_port",
                f"{name}: local port {port} is outside {MIN_LOCAL_PORT}-{MAX_PORT} - "
                "give another with --local PROC:PORT, or a smaller --local-offset",
            )
        mappings.append(
            TunnelMapping(
                process=name,
                proctype=d.proctype,
                remote_host=host,
                remote_port=d.port,
                local_host=tunnel.LOOPBACK,
                local_port=port,
                production_endpoint=f"{host}:{d.port}",
                local_endpoint=f"{tunnel.LOOPBACK}:{port}",
                query_command=shlex.join(["uqs", "query", "--tunnel", target, name]),
            )
        )
    _refuse_shared(mappings)
    argv = tunnel.forwards_argv(
        ssh, connect_timeout, [(m.local_port, m.remote_port) for m in mappings]
    )
    return TunnelPlan(
        target=target,
        ssh=ssh,
        mappings=mappings,
        launch_argv=argv,
        launch_command=shlex.join(argv),
        release=release,
        available=[n for n in forwardable if n not in names],
    )


def _refuse_shared(mappings: Sequence[TunnelMapping]) -> None:
    seen: dict[int, str] = {}
    for m in mappings:
        if m.local_port in seen:
            raise TunnelPlanError(
                "port_conflict",
                f"{seen[m.local_port]} and {m.process} would both forward local port "
                f"{m.local_port} - give one another with --local PROC:PORT",
            )
        seen[m.local_port] = m.process


def discover(
    r: control.Remote, runtime: str | None = None, runner: Runner = subprocess.run
) -> list[Declared]:
    """What the target's release declares, read over ssh as `query --tunnel`
    reads it; a failure is a `discovery` refusal naming what failed."""
    try:
        rows = tunnel.remote_processes(r, runtime, runner=runner)
    except TunnelPlanError:
        raise
    except UqsError as exc:
        raise TunnelPlanError("discovery", str(exc)) from None
    return [Declared(row["process"], row["proctype"], row["port"]) for row in rows]


def for_target(
    name: str,
    root: Path,
    selected: Sequence[str] = (),
    local: Mapping[str, int] | None = None,
    offset: int = LOCAL_OFFSET,
    *,
    runner: Runner = subprocess.run,
    runtime: str | None = None,
) -> TunnelPlan:
    """The plan for target `name`: its declaration, its release's processes."""
    r = control.remote_for(name, root)
    return plan(r.name, r.host, r.connect_timeout, discover(r, runtime, runner),
                selected, local, offset)  # fmt: skip


def parse_local(pairs: Sequence[str]) -> dict[str, int]:
    """`--local PROC:PORT`, each pair once."""
    out: dict[str, int] = {}
    for pair in pairs:
        proc, sep, port = pair.partition(":")
        if not sep or not proc or not (port.isascii() and port.isdecimal()):
            raise TunnelPlanError("invalid_port", f"--local {pair!r}: give PROC:PORT")
        if proc in out:
            raise TunnelPlanError("invalid_port", f"--local names {proc} twice")
        out[proc] = int(port)
    return out


# ------------------------------------------------------------ last checks


def checks_path(root: Path) -> Path:
    configured = os.environ.get(CHECKS_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    return targets.declaration_path(root).with_name(CHECKS)


def read_checks(root: Path) -> dict[str, dict[str, dict[str, Any]]]:
    """target -> process -> its last successful check; none when unreadable -
    a lost record makes a mapping unverified, never a plan fail. A target
    entry or a process record that is not an object is dropped (#2064), and
    so is one kept before #2236, with no `reported_process` (#2723): `uqs
    target tunnel check` verifies that mapping again and records it anew."""
    path = checks_path(root)
    try:
        doc = json.loads(path.read_text())
    except OSError, ValueError:
        return {}
    found = doc.get("targets") if isinstance(doc, dict) else None
    if not isinstance(found, dict):
        return {}
    return {
        target: {
            proc: rec
            for proc, rec in records.items()
            if isinstance(rec, dict) and "reported_process" in rec
        }
        for target, records in found.items()
        if isinstance(records, dict)
    }


def record_checks(root: Path, target: str, succeeded: Mapping[str, dict[str, Any]]) -> None:
    """Keep each process's newest successful check; a failure changes nothing,
    so the last success stays visible beside it.

    The read-modify-write holds an ``flock`` on a lock file beside the record,
    so a check from the frontend and one from `uqs target tunnel check` at the
    same moment each keep the other's targets (#2063); the document goes
    through a temp file of its own and one rename, so a reader never sees half
    of it. A write that fails raises, and leaves the record as it was."""
    if not succeeded:
        return
    path = checks_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name(path.name + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            every = read_checks(root)
            every.setdefault(target, {}).update(succeeded)
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as out:
                    out.write(json.dumps({"targets": every}, indent=2, sort_keys=True) + "\n")
                os.replace(tmp, path)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def local_listening(port: int) -> bool:
    """Something accepts on the loopback `port` - a tunnel, or not."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((tunnel.LOOPBACK, port)) == 0


def annotate(
    p: TunnelPlan,
    checks: Mapping[str, dict[str, Any]],
    *,
    listening: Callable[[int], bool] = local_listening,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The plan as its document: each mapping with its `state` - `verified`
    (a recent check proved this very mapping), `stale` (proved, but long ago
    or for other ports) or `unverified` (never) - its `last_success`, and
    whether its local port is `listening` now, which is a hint, not proof."""
    now = now or datetime.now(UTC)
    rows = []
    for m in p.mappings:
        last = checks.get(m.process)
        if checked_at(last) is None:
            last = None  # a record nobody can read is no record (#2064)
        rows.append(
            {
                **asdict(m),
                "state": mapping_state(m, last, now),
                "last_success": last,
                "listening": listening(m.local_port),
            }
        )
    doc = asdict(replace(p, mappings=[]))
    doc["mappings"] = rows
    return doc


def checked_at(last: object) -> datetime | None:
    """When the record `last` says its check ran - an aware datetime - or
    None when it is not a record, has no `at`, or one without a zone, which
    cannot be compared with now (#2064)."""
    if not isinstance(last, Mapping) or not isinstance(last.get("at"), str):
        return None
    try:
        at = datetime.fromisoformat(last["at"])
    except ValueError:
        return None
    return at if at.utcoffset() is not None else None


def mapping_state(m: TunnelMapping, last: Mapping[str, Any] | None, now: datetime) -> str:
    at = checked_at(last)
    if last is None or at is None:
        return "unverified"
    same = last.get("remote_port") == m.remote_port and last.get("local_port") == m.local_port
    return "verified" if same and now - at <= STALE_AFTER else "stale"
