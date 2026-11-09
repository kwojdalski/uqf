"""The write surface: changing the stack, not just reading it.

Everything else in this package reads. These four families change something,
and each is a different kind of change with a different way of going wrong:

    process lifecycle   start/stop/restart - a subprocess, via torq.sh
    process config      a field override persisted to process_overrides.csv
    worker config       a `.qetl.cfg` layer value, over IPC, in a live process
    backfill            a bounded worker run for a named range

WHY THIS PACKAGE NOW DEPENDS ON uqs, having deliberately not
before. `procfile.py` states the old rule and its reason: resolving two forms
of port placeholder is not worth coupling a hot read path to another package.
That reasoning holds for *reading a CSV column* and does not survive contact
with *starting a process*: `runtime.start` shells out to torq.sh with an
environment built from the vendored tree, `stack_procs.set_process_config` does a
read-modify-write against process_overrides.csv with the field whitelist that
makes it safe, and reimplementing either here would be a second writer to the
same file - which is worse than a dependency by any measure this repository
uses.

So the dependency is declared, and `procfile.py` keeps its hand-rolled
resolver: the rule was right about the case it was written for.

WHAT IS DELIBERATELY NOT HERE. Removing output: deleting logs, tplogs, wdb and
the copied sample data. It is the one orchestrator verb whose blast radius is
data rather than process state, and an HTTP route for it - reachable by
anyone who can reach the port, on a deployment whose identity is a header
anyone can set - is not something this seam should offer. `uqs clean`
remains, where the person running it is at a terminal on the host.
"""

from __future__ import annotations

import re
import time
import typing
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from uqf_frontend import instants
from uqf_frontend.config import Settings
from uqf_frontend.errors import FrontendError, StackBusy, ValidationFailed, WritesDisabled
from uqf_frontend.models.control import LifecycleAction
from uqs.generated.q_facts import CFG_ENV_LAYER, CFG_OVERRIDE_LAYER
from uqs.model.qnames import config_env_var

#: Called at the point past which an action may have changed something - the
#: audit's `Audit.begin` (activity.py), so a failure after it is recorded as
#: `unknown` rather than `refused`. A context variable rather than a
#: parameter, so no signature here changes for a caller that is not listening.
_ACTING: ContextVar[Callable[[], None] | None] = ContextVar("uqf_control_acting", default=None)


@contextmanager
def reporting_acting(callback: Callable[[], None]) -> Iterator[None]:
    """Call *callback* when an action in this block starts changing something."""
    token = _ACTING.set(callback)
    try:
        yield
    finally:
        _ACTING.reset(token)


def _acting() -> None:
    callback = _ACTING.get()
    if callback is not None:
        callback()


class ActionNotFound(FrontendError):
    """`/control/lifecycle/{action}` names no lifecycle action. 404 (#2224): an
    unknown name in the path is a thing that is not there, not a bad body."""

    status_code = 404


class ProcessNotFound(FrontendError):
    """`/control/processes/{process}/config` names a process this runtime does
    not declare. 404 (#2224), as an unknown target is."""

    status_code = 404


#: The three lifecycle verbs this seam exposes - models.LifecycleAction; any
#: other verb in the path is ActionNotFound (404, #2224). `clean` is excluded
#: on purpose - see the module docstring.
LIFECYCLE_ACTIONS: tuple[str, ...] = typing.get_args(LifecycleAction)

#: process.csv fields HTTP may NOT override, though `uqs config set` may.
#: torq.sh composes `sline="$qcmd ..."` and evals it on the next start, so a
#: `qcmd` built only from the characters the #1041 whitelist allows still names
#: the program the stack host runs - and the same token can then start it
#: (#1658). `host` chooses which machine a process is started on. Both stay
#: settable from a terminal on the host, by someone who could run a program
#: there anyway.
HTTP_REFUSED_FIELDS = frozenset({"qcmd", "host"})


#: How long a lifecycle action waits, after torq.sh returns, for the processes
#: it named to reach the state it promised, and how often it looks (#2027).
#: torq.sh starts a process in the background and stops one with a signal, so
#: neither is done the instant torq.sh exits.
VERIFY_SECONDS = 10.0
VERIFY_POLL_SECONDS = 0.5

#: torq.sh's line for a stop that found no process: `HH:MM:SS | rdb1 is not
#: currently running`. It exits 0 either way.
_NOT_RUNNING = re.compile(r"\|\s*(\S+) is not currently running")


@dataclass(frozen=True)
class CommandResult:
    """What a subprocess-backed action did.

    *problems* are what the process table showed after torq.sh returned and
    contradicted it (#2027): torq.sh exits 0 for "nothing to stop" and for a
    start whose process died at once, so its exit code alone is not success.
    """

    action: str
    procs: str
    exit_code: int
    output: str
    problems: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.problems


def require_writes(settings: Settings) -> None:
    """Refuse every control route unless writes were switched on.

    Checked HERE rather than per route, so a route added later cannot forget
    it - the thing that makes a kill switch worth having is that it cannot be
    bypassed by inattention.
    """
    if not settings.enable_writes:
        raise WritesDisabled(
            "control routes are disabled; set UQF_FRONTEND_ENABLE_WRITES=true on the "
            "server to enable them. They are off by default because this deployment "
            "has one shared credential and a claimed identity"
        )


def _paths(settings: Settings):
    """The orchestrator's path bundle.

    Imported inside the function, not at module scope, for the same reason
    `gateway.py` imports kola lazily: a read-only deployment that never calls
    a control route must not fail to start because the orchestrator's own
    imports are unhappy.

    `stack_paths.default_paths()` resolves the tree from the orchestrator package's
    own location, which is right when both are installed from this workspace
    - the single-host deployment this tree runs. `UQF_FRONTEND_STACK_ROOT`
    overrides it for the case they are not, and is checked against the tree
    it names rather than trusted, so a wrong path fails here instead of
    starting the wrong stack.
    """
    from uqs import paths as stack_paths

    if settings.stack_root is None:
        return stack_paths.default_paths()
    root = settings.stack_root
    # uqs's own test of a root, not lib/torq/torq.sh: a deployed release ships
    # no lib/ and uses an external TorQ, and was refused by that marker while
    # uqs ran it fine (#802). The launcher is checked where it is run.
    if not stack_paths.is_repo_root(root):
        raise ValidationFailed(
            f"UQF_FRONTEND_STACK_ROOT={root} does not look like the repository: "
            f"it holds no {stack_paths.root_markers_text()}"
        )
    return stack_paths.paths_for_root(root)


def lifecycle(settings: Settings, action: str, procs: str) -> CommandResult:
    """start, stop or restart one or more processes.

    *procs* is torq.sh's own selector - a process name, several separated by
    spaces, or "all" - passed through rather than reinterpreted here, so the
    HTTP surface and the CLI cannot disagree about what "all" means.
    """
    require_writes(settings)
    if action not in LIFECYCLE_ACTIONS:
        raise ActionNotFound(
            f"unknown action {action!r} - expected one of {sorted(LIFECYCLE_ACTIONS)}"
        )
    from uqs.attach import live as _registers_live_check  # noqa: F401 - the start gate's (#1651)
    from uqs.paths import UqsError
    from uqs.stack import runtime

    fn = {"start": runtime.start, "stop": runtime.stop, "restart": runtime.restart}[action]
    paths = _paths(settings)
    # The base port the stack runs on NOW - setenv.sh as the last bootstrap
    # wrote it - not the one the frontend started with: after `uqs restart
    # --base-port 7050` the old one stopped nothing and reported ok (#2027).
    base_port = settings.facts().base_port
    started_at = time.monotonic()
    # The whole action holds the stack - the look before, torq.sh, the look
    # after - so a second one cannot interleave with it (#2028).
    with _holding(paths):
        try:
            before = _observe(paths, base_port)
        except UqsError as exc:
            raise ValidationFailed(str(exc)) from None
        _acting()
        try:
            # Bounded just inside the browser's own wait (config.lifecycle_timeout),
            # so a slow `start all` is refused here rather than timing out in the
            # browser while torq.sh is still running and inviting a second Start.
            result = fn(
                paths,
                procs,
                base_port=base_port,
                capture=True,
                timeout=settings.lifecycle_timeout,
            )
        except UqsError as exc:
            raise ValidationFailed(str(exc)) from None
        output = (result.stdout or "").strip()
        problems: tuple[str, ...] = ()
        if result.returncode == 0:
            deadline = min(
                started_at + settings.lifecycle_timeout, time.monotonic() + VERIFY_SECONDS
            )
            problems = _verify(paths, base_port, action, procs, before, output, deadline)
    return CommandResult(
        action=action,
        procs=procs,
        exit_code=result.returncode,
        output="\n".join((output, *problems)).strip(),
        problems=problems,
    )


@contextmanager
def _holding(paths: Any) -> Iterator[None]:
    """The stack's lifecycle lock (uqs.stack.generated), or StackBusy at once:
    refused rather than queued behind another start, stop or restart. The
    torq.sh run inside takes it again, which the lock allows."""
    from uqs.stack import generated

    try:
        with generated.lifecycle_lock(paths, wait=0):
            yield
    except generated.StackBusy as exc:
        raise StackBusy(str(exc)) from None


def _observe(paths: Any, base_port: int) -> dict[str, list[int]]:
    """procname -> the pids running it at *base_port*, for every process of
    this stack on this host: the same match torq.sh's findproc makes
    (uqs.stack.alive). A process configured on another host is absent."""
    from uqs.stack import alive

    return alive.instances(paths, base_port, timeout=10)


def _verify(
    paths: Any,
    base_port: int,
    action: str,
    procs: str,
    before: dict[str, list[int]],
    output: str,
    deadline: float,
) -> tuple[str, ...]:
    """What contradicts torq.sh's exit code 0, or nothing (#2027).

    A stop that torq.sh says found nothing, of a process that was up just
    before, looked in the wrong place. Otherwise each named process this host
    can see must reach the state the action promised - down after a stop, up
    after a start or restart - by *deadline*.
    """
    if not before:
        return ()
    problems = [
        f"{name} was running (pid {before[name][-1]}) at base port {base_port}, but torq.sh "
        f"reported it not running - nothing was stopped"
        for name in _NOT_RUNNING.findall(output)
        if before.get(name)
    ]
    if problems:
        return tuple(problems)
    names = [n for n in _targets(paths, procs) if n in before]
    want_up = action != "stop"
    while True:
        now = _observe(paths, base_port)
        wrong = [n for n in names if bool(now.get(n)) != want_up]
        if not wrong or time.monotonic() >= deadline:
            break
        time.sleep(VERIFY_POLL_SECONDS)
    if want_up:
        return tuple(
            f"{n} is not running after {action} at base port {base_port} - see `uqs logs {n}`"
            for n in wrong
        )
    return tuple(
        f"{n} is still running (pid {now[n][-1]}) at base port {base_port} after stop"
        for n in wrong
    )


def _targets(paths: Any, procs: str) -> list[str]:
    """The processes torq.sh's selector names: `all` is the startwithall rows."""
    if procs.strip() != "all":
        return procs.split()
    from uqs.stack import procs as stack_procs

    return [
        row["procname"]
        for row in stack_procs.list_process_choices(paths)
        if row["startwithall"] == "1"
    ]


def set_process_field(settings: Settings, procname: str, field: str, value: str) -> dict[str, str]:
    """Persist one process.csv field override, and return the effective row.

    The row is returned rather than a bare acknowledgement because the write
    is not the interesting part - what the process will actually start with
    is, and a caller that has to issue a second request to find out will
    sometimes not bother.
    """
    require_writes(settings)
    if field in HTTP_REFUSED_FIELDS:
        raise ValidationFailed(
            f"{field!r} cannot be set over HTTP - it chooses what or where the stack "
            f"runs; set it with `uqs config set` on the host. Settable here: "
            f"{settable_fields(settings)}"
        )
    from uqs.paths import UqsError, UqsUnknownProcess
    from uqs.stack import procs as stack_procs

    paths = _paths(settings)
    try:
        # Every check uqs makes, and nothing written: a request refused here
        # changed nothing, and is audited `refused`, not `unknown` (#2477).
        stack_procs.set_process_config(paths, procname, field, value, dry_run=True)
        _acting()
        stack_procs.set_process_config(paths, procname, field, value)
        return stack_procs.get_process_config(paths, procname, base_port=settings.facts().base_port)
    except UqsUnknownProcess as exc:
        raise ProcessNotFound(str(exc)) from None
    except UqsError as exc:
        raise ValidationFailed(str(exc)) from None


def settable_fields(settings: Settings) -> list[str]:
    """Which process.csv fields may be overridden over HTTP.

    Served so a UI can offer a closed list rather than a free-text box. The
    orchestrator's columns less HTTP_REFUSED_FIELDS: a column added there is
    settable here without a second list to update, and a refused one cannot
    come back by being re-listed. Imported here, not at module scope, so a
    read-only deployment loads nothing of uqs.paths.
    """
    from uqs.model.pipelines import PROCESS_CSV_FIELDS

    return sorted(f for f in PROCESS_CSV_FIELDS if f not in HTTP_REFUSED_FIELDS)


def process_choices(settings: Settings) -> list[dict[str, Any]]:
    """Which processes a lifecycle selector may name.

    Served for the same reason settable_fields is: a picker over a closed
    list rather than a free-text selector a caller mistypes and learns about
    from torq.sh's exit code. The rows are the orchestrator's effective
    process.csv - vendored, pipelines, extras, overrides - so the list here
    and the list `uqs start all` acts on cannot differ.
    """
    from uqs.stack import procs as stack_procs

    return [
        {
            "process": row["procname"],
            "proctype": row["proctype"],
            "start_with_all": row["startwithall"] == "1",
        }
        for row in stack_procs.list_process_choices(_paths(settings))
    ]


#: Set one `.qetl.cfg` override in a live process.
#:
#: One call to the q function that owns the layers, rather than a program that
#: reaches into them. The version before this read three layers under names
#: `.qetl.cfg` does not define, and no test could see it because the gateway is
#: mocked. python/uqs/tests/test_q_names.py now checks every q name the Python
#: code mentions against the names the q source defines.
SET_WORKER_CONFIG = """{[k;v] .qetl.cfg.set_override[k;v]}"""


def set_worker_config(gateway: Any, settings: Settings, key: str, value: str) -> dict[str, Any]:
    """Set a worker-config override in the process the gateway addresses.

    RETURNS `.qetl.cfg.explain`, which names the layer the value now comes from.
    That matters because an override is not the only layer: a key also set in
    the environment reads from there, and a caller told only "ok" would
    believe a value that is not in effect.

    The change lives in the process's memory and is lost when it restarts -
    unlike a process.csv override, which survives. The two look similar from
    a UI and are not, so the response says which layer answered.
    """
    require_writes(settings)
    if not key:
        raise ValidationFailed("a worker-config key is required")
    from uqf_frontend.gateway import TIERS

    _acting()
    raw = gateway.route(SET_WORKER_CONFIG, (key, value), TIERS["rdb"])
    explain = _plain(raw)
    layer, effective = _layer_of(explain)
    # Only the environment outranks an override (`.qetl.cfg.sources` puts it
    # first), so "shadowed" means exactly that. The layer names are q's.
    shadowed = layer == CFG_ENV_LAYER
    return {
        "key": key,
        "value": value,
        "explain": explain,
        "effective_layer": layer,
        "effective_value": effective,
        "shadowed": shadowed,
        "saved": layer == CFG_OVERRIDE_LAYER,
        "env_var": config_env_var(key) if shadowed else None,
    }


def _layer_of(explain: Any) -> tuple[str | None, str | None]:
    """The (layer, raw value) pair `.qetl.cfg.explain` returns, or Nones.

    A q `(`env;"true")` arrives as a two-item list. Anything else is reported
    as unknown rather than guessed at: the caller then shows neither "saved"
    nor "shadowed", because it cannot honestly say which.
    """
    if isinstance(explain, (list, tuple)) and len(explain) == 2 and isinstance(explain[0], str):
        effective = explain[1]
        return explain[0], effective if isinstance(effective, str) else None
    return None, None


def _plain(value: Any) -> Any:
    """kola hands back bytes for q char columns; make it JSON-able."""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {_plain(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def start_backfill(
    settings: Settings,
    worker: str,
    source_version: str,
    range_from: str,
    range_to: str,
) -> dict[str, Any]:
    """Launch a bounded worker over a range, and return without waiting.

    DETACHED, not awaited, and that is the design rather than a shortcut. A
    backfill runs for as long as its range takes; an HTTP request that
    blocked on one would time out somewhere in the middle and tell the caller
    nothing about whether the work continued. The worker writes a status file
    on every transition, and `/ops/backfill` already reads it - so
    the honest answer to "did it work" is that endpoint, not this one's
    response body.

    The range is REQUIRED and has no default, which is the explicit-range rule reaching the
    HTTP surface: a backfill that guessed a range would publish the wrong
    window and record it as covered. The four values reach the process as the
    command-line flags `scripts/processes/torq_backfill.q` reads, built by the
    same `uqs.stack.backfill.backfill_flags` `uqs job backfill` uses - not a second
    way of spelling them.
    """
    require_writes(settings)
    if not worker:
        raise ValidationFailed("a worker name is required")
    if not source_version:
        raise ValidationFailed(
            "source_version is required - coverage recorded under one source release "
            "says nothing about another"
        )
    for name, raw in (("range_from", range_from), ("range_to", range_to)):
        if not raw:
            raise ValidationFailed(f"{name} is required - a backfill with no range is not a range")
    # Exact or refused (instants.to_datetime): `uqs job backfill` takes a
    # datetime, and a bound 789 ns short of the one sent would be recorded as
    # covered for the wrong window.
    bound_from = instants.bound("range_from", range_from)
    bound_to = instants.bound("range_to", range_to)

    from uqf_frontend.errors import BackfillNotStarted
    from uqs.attach import live as _registers_live_check  # noqa: F401 - the start gate's (#1651)
    from uqs.paths import UqsError
    from uqs.stack import capabilities
    from uqs.stack.backfill import backfill_flags, procname_for
    from uqs.stack.backfill import start as start_backfill

    # Through torq.sh, exactly as `uqs job backfill` starts it. Running the q
    # script directly - as this did - skipped TorQ's launcher, so the process
    # died at once on `.proc.procname` with its output sent to /dev/null,
    # while this endpoint answered 200 with the pid of a process already gone.
    paths = _paths(settings)
    try:
        procname = procname_for(worker)
        # start's own checks, before anything is acted on: a request refused
        # by them changed nothing, and is audited `refused` (#2477).
        capabilities.refuse_unsupported(paths, worker, None)
        backfill_flags(worker, source_version, bound_from, bound_to)
        # Refused, like a lifecycle action, while another holds the stack (#2028).
        with _holding(paths):
            _acting()
            result = start_backfill(
                paths,
                worker,
                source_version,
                bound_from,
                bound_to,
                base_port=settings.facts().base_port,
            )
    except UqsError as exc:
        raise ValidationFailed(str(exc)) from None
    if result.returncode != 0:
        raise BackfillNotStarted(
            f"torq.sh could not start {procname} (exit {result.returncode}) - "
            f"see `uqs logs {procname}`"
        )
    return {
        "worker": worker,
        "source_version": source_version,
        "range_from": range_from,
        "range_to": range_to,
        "process": procname,
        "status_path": "/ops/backfill",
    }
