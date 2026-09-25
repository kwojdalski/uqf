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

WHAT IS DELIBERATELY NOT HERE. `clean`, which deletes logs, tplogs, wdb and
the copied sample data. It is the one orchestrator verb whose blast radius is
data rather than process state, and an HTTP route for it - reachable by
anyone who can reach the port, on a deployment whose identity is a header
anyone can set - is not something this seam should offer. `uqs clean`
remains, where the person running it is at a terminal on the host.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from uqf_frontend.config import Settings
from uqf_frontend.errors import ValidationFailed, WritesDisabled

#: The three lifecycle verbs this seam exposes. `clean` is excluded on
#: purpose - see the module docstring.
LIFECYCLE_ACTIONS = ("start", "stop", "restart")


@dataclass(frozen=True)
class CommandResult:
    """What a subprocess-backed action did."""

    action: str
    target: str
    exit_code: int
    output: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


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
    if not (root / "lib" / "torq" / "torq.sh").is_file():
        raise ValidationFailed(
            f"UQF_FRONTEND_STACK_ROOT={root} does not look like the repository: "
            "lib/torq/torq.sh is not there"
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
        raise ValidationFailed(f"unknown action {action!r} - expected one of {LIFECYCLE_ACTIONS}")
    from uqs.paths import UqsError
    from uqs.stack import runtime

    fn = {"start": runtime.start, "stop": runtime.stop, "restart": runtime.restart}[action]
    try:
        result = fn(_paths(settings), procs, base_port=settings.base_port, capture=True)
    except UqsError as exc:
        raise ValidationFailed(str(exc)) from None
    return CommandResult(
        action=action,
        target=procs,
        exit_code=result.returncode,
        output=(result.stdout or "").strip(),
    )


def set_process_field(settings: Settings, procname: str, field: str, value: str) -> dict[str, str]:
    """Persist one process.csv field override, and return the effective row.

    The row is returned rather than a bare acknowledgement because the write
    is not the interesting part - what the process will actually start with
    is, and a caller that has to issue a second request to find out will
    sometimes not bother.
    """
    require_writes(settings)
    from uqs.paths import UqsError
    from uqs.stack import procs as stack_procs

    paths = _paths(settings)
    try:
        stack_procs.set_process_config(paths, procname, field, value)
        return stack_procs.get_process_config(paths, procname, base_port=settings.base_port)
    except UqsError as exc:
        raise ValidationFailed(str(exc)) from None


def settable_fields(settings: Settings) -> list[str]:
    """Which process.csv fields may be overridden.

    Served so a UI can offer a closed list rather than a free-text box. The
    orchestrator's whitelist is the authority; echoing it here means the two
    cannot drift.
    """
    from uqs.model.pipelines import PROCESS_CSV_FIELDS

    return sorted(PROCESS_CSV_FIELDS)


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
            "procname": row["procname"],
            "proctype": row["proctype"],
            "start_with_all": row["startwithall"] == "1",
        }
        for row in stack_procs.list_process_choices(_paths(settings))
    ]


#: Set one `.qetl.cfg` override in a live process.
#:
#: `.qetl.cfg.set_layers[overrides;yaml;defaults]` replaces all three layers, so
#: setting one key means reading the current override layer and putting the
#: key into it - done in q, in one expression, so two concurrent callers
#: cannot interleave a read and a write and lose one of them.
SET_WORKER_CONFIG = """{[k;v]
  cur:$[99h=type .qetl.cfg.overrides; .qetl.cfg.overrides; ()!()];
  .qetl.cfg.set_layers[cur,(enlist k)!enlist v; .qetl.cfg.yaml; .qetl.cfg.defaults];
  .qetl.cfg.explain k}"""


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

    raw = gateway.route(SET_WORKER_CONFIG, (key, value), TIERS["rdb"])
    return {"key": key, "value": value, "explain": _plain(raw)}


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
    same `uqs.stack.backfill.backfill_flags` `uqs backfill` uses - not a second
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
        _require_utc(name, raw)

    import os
    import subprocess

    from uqs.paths import UqsError, q_interpreter
    from uqs.stack.backfill import backfill_flags
    from uqs.stack.runtime import bootstrap

    paths = _paths(settings)
    try:
        overrides = bootstrap(paths, base_port=settings.base_port)
    except UqsError as exc:
        raise ValidationFailed(str(exc)) from None

    try:
        flags = backfill_flags(
            worker,
            source_version,
            dt.datetime.fromisoformat(range_from),
            dt.datetime.fromisoformat(range_to),
        )
    except UqsError as exc:
        raise ValidationFailed(str(exc)) from None
    env = {**os.environ, **overrides}
    script = paths.scripts_dir.parent / "scripts" / "processes" / "torq_backfill.q"
    if not script.is_file():
        raise ValidationFailed(f"{script} not found - is this the repository root?")
    q = q_interpreter(env)
    if q is None:
        raise ValidationFailed("no q interpreter to run the backfill - set $QCMD, or put q on PATH")

    # start_new_session detaches it from this server's process group, so a
    # restart of the API does not take a running backfill down with it.
    proc = subprocess.Popen(  # noqa: S603
        [str(q), str(script), *flags],
        cwd=paths.repo_root,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return {
        "worker": worker,
        "source_version": source_version,
        "range_from": range_from,
        "range_to": range_to,
        "pid": proc.pid,
        "status_path": "/ops/backfill",
    }


def _require_utc(name: str, raw: str) -> None:
    """Reject a bound without an explicit offset.

    Identical reasoning to the coverage endpoint's: a naive timestamp is read
    as the server's local time, and a backfill an hour wide of where the
    caller meant records coverage for the wrong window.
    """
    try:
        parsed = dt.datetime.fromisoformat(raw)
    except ValueError:
        raise ValidationFailed(
            f"{name} must be ISO-8601, e.g. 2026-09-11T00:00:00Z; got {raw!r}"
        ) from None
    if parsed.tzinfo is None:
        raise ValidationFailed(
            f"{name} needs an explicit timezone offset; {raw!r} would be read as "
            "the server's local time and cover the wrong window"
        )
