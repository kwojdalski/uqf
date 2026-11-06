"""`uqs target tunnel check`: whether each mapping of a tunnel plan reaches
the process it names, layer by layer (#2031).

A listening local port proves nothing about the production process behind
it, so each endpoint is checked in the order a connection is made, and the
first layer that fails names the endpoint's category:

  ssh               ssh to the target, as `uqs target check` reaches it
                    (control.current_release: the host, the key, the release);
  discovery         the release's process list, which the plan is built from
                    (tunnel_plan.discover) - a plan refusal keeps its own kind;
  unreachable       nothing listens on the remote port: asked ON the server,
                    over one ssh, so a closed destination is never mistaken
                    for a refused login through the tunnel;
  auth_refused      the process refused the q login (kola's auth error);
  identity_mismatch the port answered as another process - the identity probe
                    `uqs deploy check` uses (verify.IDENTITY_EXPR);
  not_ready         a gateway with no active server behind it (.gw.servers);
  local_bind        the plan's local port is held by something that is not
                    this process's tunnel - checked last, because the remote
                    layers are checked through a tunnel of the check's own,
                    on free ports, so a held local port hides nothing else.

One endpoint failing never hides the others: every endpoint is checked and
reported. Each that passes is recorded (tunnel_plan.record_checks) with the
identity it answered and when, so a later plan - and the Services view -
shows its last success beside a failure that came after it.

Nothing secret is reported: the q password is never in the document, and any
error text that could carry it is scrubbed (tunnel.scrub). The configured
mapping (`configured`: what the plan says) is kept apart from what was proved
(`identity`, `category`, `connected`). An endpoint's `category` and
`error` are the top level's names: None when it passed, else the first
failing layer's category (a key of `categories`) and its text.
"""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from uqs.deploy import control, tunnel, tunnel_plan, verify
from uqs.deploy.remote import diagnostic
from uqs.logger import get_logger
from uqs.paths import UqsError
from uqs.stack import runtime

log = get_logger()

#: Every category an endpoint (or the whole check) can fail with, and what it means.
CATEGORIES = {
    "ssh": "ssh could not reach the target, or could not hold the check's tunnel",
    "discovery": "the target's release did not list its processes",
    "unknown_process": "a process named is not declared on the target",
    "unsupported_proctype": "a process named is not an rdb, hdb, wdb or gateway",
    "invalid_port": "a local port is out of range or names no forwarded process",
    "port_conflict": "two mappings would share one local port",
    "unreachable": "nothing on the target answers on the remote port",
    "auth_refused": "the process refused the q login",
    "identity_mismatch": "the remote port answered as another process",
    "not_ready": "the gateway has no active server behind it",
    "local_bind": "the local port is held by something other than this process's tunnel",
}
#: The layers of one endpoint, in the order a connection crosses them.
LAYERS = ("ssh", "destination", "auth", "identity", "ready", "local")
#: The servers a gateway would route to; empty when none is active.
GATEWAY_READY_EXPR = "@[{distinct exec servertype from .gw.servers where active};::;`symbol$()]"
#: Seconds one q probe may take.
QUERY_TIMEOUT = 5

Runner = Callable[..., subprocess.CompletedProcess]
Query = Callable[[str, int], object]


@dataclass
class EndpointCheck:
    process: str
    proctype: str
    production_endpoint: str
    local_endpoint: str
    remote_port: int
    local_port: int
    #: None when the endpoint passed, else the category of the first layer
    #: that failed - a key of CATEGORIES, as the report's own `category`.
    category: str | None = None
    error: str | None = None
    layers: dict[str, str] = field(default_factory=lambda: dict.fromkeys(LAYERS, "skipped"))
    #: The planned local port already serves this very process.
    connected: bool = False
    #: What the process answered as, when it answered.
    identity: str | None = None
    #: A gateway's active server types.
    gateway_servers: list[str] | None = None
    last_success: dict[str, Any] | None = None

    def fail(self, layer: str, category: str, error: str) -> None:
        self.layers[layer] = "failed"
        if self.category is None:
            self.category, self.error = category, error


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def remote_listening(r: control.Remote, ports: Sequence[int], runner: Runner) -> dict[int, bool]:
    """port -> whether anything accepts on it ON the target, from one ssh -
    bash's /dev/tcp, so no tool needs to be installed there."""
    probe = "; ".join(
        f"if (exec 3<>/dev/tcp/{tunnel.LOOPBACK}/{p}) 2>/dev/null; "
        f"then echo {p} open; else echo {p} closed; fi"
        for p in ports
    )
    argv = control.ssh_argv(r, [], tty=False, command=f"bash -c {shlex.quote(probe)}")
    try:
        done = runner(argv, capture_output=True, text=True, check=False,
                      stdin=subprocess.DEVNULL, timeout=r.connect_timeout + 30)  # fmt: skip
    except subprocess.TimeoutExpired:
        raise UqsError(f"target {r.name}: probing its ports did not finish in time") from None
    except OSError as exc:
        raise UqsError(f"could not run ssh: {exc}") from None
    if done.returncode:
        said = diagnostic(done) or f"exit {done.returncode}"
        raise UqsError(f"target {r.name}: could not probe its ports: {said}")
    found: dict[int, bool] = {}
    for line in (done.stdout or "").splitlines():
        word = line.split()
        if len(word) == 2 and word[0].isdigit():
            found[int(word[0])] = word[1] == "open"
    return found


def names(answer: object) -> list[str]:
    """A q symbol list as kola hands it over - a polars Series, a list, one
    symbol, or nothing - as sorted text."""
    if answer is None:
        return []
    if isinstance(answer, str):
        return [answer]
    to_list = getattr(answer, "to_list", None)
    items = to_list() if callable(to_list) else answer
    return sorted(str(s) for s in items) if isinstance(items, (list, tuple)) else [str(items)]


def _probe(e: EndpointCheck, port: int, query: Query, scrub: Callable[[str], str]) -> None:
    """The q layers of `e`, through `port`: login, identity, readiness."""
    try:
        answer = str(query(verify.IDENTITY_EXPR, port))
    except Exception as exc:  # noqa: BLE001 - classified, never raised
        if "auth" in type(exc).__name__.lower():
            e.layers["destination"] = "ok"
            e.fail("auth", "auth_refused", f"{e.process} refused the q login: {scrub(str(exc))}")
        else:
            e.fail("destination", "unreachable",
                   f"{e.process} did not answer q through the tunnel: "
                   f"{type(exc).__name__}: {scrub(str(exc))}")  # fmt: skip
        return
    e.layers["destination"] = e.layers["auth"] = "ok"
    e.identity = answer
    if answer != e.process:
        e.fail("identity", "identity_mismatch",
               f"port {e.remote_port} answered as {answer!r}, not {e.process}")  # fmt: skip
        return
    e.layers["identity"] = "ok"
    if e.proctype != "gateway":
        e.layers["ready"] = "ok"
        return
    try:
        servers = query(GATEWAY_READY_EXPR, port)
        e.gateway_servers = names(servers)
    except Exception as exc:  # noqa: BLE001 - classified, never raised
        e.fail("ready", "not_ready", f"{e.process} did not say its servers: {scrub(str(exc))}")
        return
    if e.gateway_servers:
        e.layers["ready"] = "ok"
    else:
        e.fail("ready", "not_ready", f"{e.process} has no active server to route to")


def _local(e: EndpointCheck, listening: Callable[[int], bool], query: Query) -> None:
    """The plan's own local port: free, this process's tunnel, or held."""
    if not listening(e.local_port):
        e.layers["local"] = "ok"
        return
    try:
        answer = str(query(verify.IDENTITY_EXPR, e.local_port))
    except Exception:  # noqa: BLE001 - classified, never raised
        answer = None
    if answer == e.process:
        e.connected = True
        e.layers["local"] = "ok"
    elif answer is None and e.category is not None:
        # a tunnel to a process that is down answers nothing either
        e.layers["local"] = "skipped"
    else:
        what = f"answers as {answer!r}" if answer is not None else "does not answer as q"
        e.fail("local", "local_bind",
               f"local port {e.local_port} is held by something that {what} - stop it, "
               f"or plan another port with --local {e.process}:PORT")  # fmt: skip


def _record(root: Path, target: str, succeeded: dict[str, dict[str, Any]]) -> None:
    """Record the successes; a record that cannot be written loses only the
    `last_success` a later plan would show, never this check's answer."""
    try:
        tunnel_plan.record_checks(root, target, succeeded)
    except OSError as exc:
        log.warning("could not record the tunnel check: {}", exc)


def run(
    name: str,
    root: Path,
    selected: Sequence[str] = (),
    local: dict[str, int] | None = None,
    offset: int = tunnel_plan.LOCAL_OFFSET,
    *,
    user: str = "admin",
    passwd: str = "admin",
    runner: Runner = subprocess.run,
    popen: tunnel.Popen = subprocess.Popen,
    query: Query | None = None,
    listening: Callable[[int], bool] = tunnel_plan.local_listening,
    runtime_name: str | None = None,
    record: bool = True,
) -> dict[str, Any]:
    """The check's document: `ok`, the target, its `release`, a top-level
    `category` and `error` when the check could not reach the endpoints, and
    one record per endpoint. Never raises for what it finds; raises
    UqsError only for a target the registry does not name."""

    def scrub(text: str) -> str:
        return tunnel.scrub(text, [passwd])

    def ask(expr: str, port: int) -> object:
        return runtime.query(expr, port, host=tunnel.LOOPBACK, user=user, passwd=passwd,
                             timeout=QUERY_TIMEOUT)  # fmt: skip

    q = query or ask
    r = control.remote_for(name, root)  # an unknown target is a refusal, not a finding
    report: dict[str, Any] = {
        "ok": False, "target": name, "ssh": r.host, "release": None, "checked_at": _now(),
        "category": None, "error": None, "endpoints": [], "categories": CATEGORIES,
    }  # fmt: skip
    try:
        report["release"] = control.current_release(r, runner)
    except UqsError as exc:
        report.update(category="ssh", error=scrub(str(exc)))
        return report
    try:
        p = tunnel_plan.plan(r.name, r.host, r.connect_timeout,
                             tunnel_plan.discover(r, runtime_name, runner),
                             selected, local, offset)  # fmt: skip
    except tunnel_plan.TunnelPlanError as exc:
        report.update(category=exc.kind, error=scrub(str(exc)))
        return report
    report["launch_command"] = p.launch_command
    ends = [
        EndpointCheck(
            m.process,
            m.proctype,
            m.production_endpoint,
            m.local_endpoint,
            m.remote_port,
            m.local_port,
        )
        for m in p.mappings
    ]
    for e in ends:
        e.layers["ssh"] = "ok"
    try:
        open_ = remote_listening(r, [e.remote_port for e in ends], runner)
    except UqsError as exc:
        for e in ends:
            e.fail("ssh", "ssh", scrub(str(exc)))
        open_ = {}
    reachable = [e for e in ends if e.category is None and open_.get(e.remote_port)]
    for e in ends:
        if e.category is None and not open_.get(e.remote_port):
            e.fail("destination", "unreachable",
                   f"nothing on {r.name} listens on port {e.remote_port} - is {e.process} "
                   f"running? `uqs --target {r.name} status` shows")  # fmt: skip
    if reachable:
        pairs = [(tunnel.free_port(), e.remote_port) for e in reachable]
        try:
            with tunnel.forwarded(r, pairs, popen=popen):
                for (port, _remote), e in zip(pairs, reachable, strict=True):
                    _probe(e, port, q, scrub)
        except UqsError as exc:
            for e in reachable:
                if e.layers["destination"] == "skipped":
                    e.fail("ssh", "ssh", scrub(str(exc)))
    for e in ends:
        _local(e, listening, q)
    at = report["checked_at"]
    if record:
        _record(root, r.name, {
            e.process: {"at": at, "identity": e.identity, "remote_port": e.remote_port,
                        "local_port": e.local_port, "release": report["release"]}
            for e in ends if e.category is None
        })  # fmt: skip
    last = tunnel_plan.read_checks(root).get(r.name, {})
    for e in ends:
        e.last_success = last.get(e.process)
    report["endpoints"] = [asdict(e) for e in ends]
    report["ok"] = all(e.category is None for e in ends)
    return report
