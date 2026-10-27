"""`uqs job attach NAME` as functions (#1457): which attachment a command line
means, and the stages that execute it, in order.

One command runs the whole workflow: resolve the configuration, preflight,
show the plan, confirm, then save -> apply -> verify -> start, and with the
stack on another machine -> reach. The stages are the existing ones (apply.py,
verify.py, publisher.py, reach.py), called in order; nothing here re-implements
a check. Execution never relies on an earlier
preview: apply builds its own plan against the stack as it is when it runs,
and verify reads it again. The CLI (cli/attach.py) renders what these return.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from uqs.attach import apply as applying
from uqs.attach import config, jobs, plan, reach
from uqs.attach import verify as verifying
from uqs.attach.config import Attachment, AttachmentError, Storage
from uqs.attach.facts import Host
from uqs.attach.findings import blockers
from uqs.attach.manifest import Manifest
from uqs.attach.publisher import Publisher, stop_publisher
from uqs.attach.remote import Endpoint, Ipc
from uqs.attach.service import create, manifest_of, newly_pinned, parse_discovery, save_planned


@dataclass(frozen=True)
class Given:
    """The configuration options a command line gave. None, empty or False
    means "not given": a saved attachment's own value stands."""

    job: str | None = None
    bundle: Path | None = None
    stack_id: int | None = None
    host: str | None = None
    discovery: str | None = None
    tickerplant: str | None = None
    consumers: tuple[str, ...] = ()
    user: str | None = None
    password_env: str | None = None
    runtime_only: bool = False
    remote_schema_file: bool = False
    tplogdir: str = ""
    schema_file: str = ""


@dataclass
class Resolved:
    att: Attachment
    #: saved (reused as it is) | new | replaced
    source: str
    path: Path
    #: what a --replace changed, as "field: saved -> given"
    changed: list[str] = field(default_factory=list)


def differences(saved: Attachment, given: Given, root: Path) -> list[str]:
    """Every option `given` sets to something other than `saved` holds."""
    st, out = saved.stack, []

    def differs(what: str, before: object, after: object) -> None:
        if after is not None and after != before:
            out.append(f"{what}: {before!r} -> {after!r}")

    differs("--stack-id", st.id, given.stack_id)
    differs("--host", st.host, given.host)
    if given.discovery is not None:
        differs("--discovery", f"{st.discovery_host}:{st.discovery_port}",
                "{}:{}".format(*parse_discovery(given.discovery)))  # fmt: skip
    differs("--tickerplant", st.tickerplant, given.tickerplant)
    differs("--consumer", st.consumers, tuple(given.consumers) or None)
    differs("--user", st.user, given.user)
    differs("--password-env", st.password_env, given.password_env)
    differs("--runtime-only", saved.persist, "runtime-only" if given.runtime_only else None)
    differs(
        "--remote-schema-file",
        saved.persist,
        "remote-schema-file" if given.remote_schema_file else None,
    )
    differs("--tplogdir", saved.storage.tplogdir, given.tplogdir or None)
    differs("--schema-file", saved.storage.schema_file, given.schema_file or None)
    differs("--job", saved.publisher.job, given.job)
    if (given.job or given.bundle) and not out:
        # the job as it is declared NOW: a table that changed shape is a new attachment
        folder = given.bundle or (Path(saved.origin["folder"]) if saved.origin else None)
        spec = jobs.resolve(given.job or saved.publisher.job, folder, root)
        differs("job process", saved.publisher.procname, spec.procname)
        if spec.tables != saved.tables:
            out.append(
                f"the job's tables: {[t.name for t in saved.tables]} as saved, "
                f"{[t.name for t in spec.tables]} as declared now (or their columns changed)"
            )
    return out


def resolve(
    name: str,
    given: Given,
    root: Path,
    *,
    replace_saved: bool = False,
    runtime: str = "",
    state_dir: Path | None = None,
) -> Resolved:
    """The attachment a command line means.

    A saved attachment is reused when the options agree with it (repeating the
    first command line is the same request), and refused when they conflict,
    unless `replace_saved`. A replacement keeps the overrides and never touches
    the manifest - the record of what was changed on the stack and the only
    thing detach can reverse from - and it may not move an attachment whose
    manifest still holds changes to another stack or other tables.
    """
    path = config.directory(name, state_dir) / config.CONFIG_FILE
    if (m := manifest_of(name, state_dir)) is not None:
        applying.refuse_detaching(name, m.state)
    saved = config.load(name, state_dir) if path.is_file() else None
    changed: list[str] = []
    if saved is not None:
        changed = differences(saved, given, root)
        if not changed:
            return Resolved(saved, "saved", path)
        if not replace_saved:
            raise AttachmentError(
                f"attachment {name} is saved with other settings ({'; '.join(changed)}). "
                "Drop those options to use the saved ones, or add --replace to rebuild it"
            )
    if not given.job or given.stack_id is None:
        raise AttachmentError(
            f"a new attachment needs --job and --stack-id (no attachment {name} is saved)"
            if saved is None
            else "--replace rebuilds the attachment: give --job and --stack-id"
        )
    att = create(
        name, given.job, given.bundle, given.stack_id, root,
        host=given.host or "localhost", discovery=given.discovery,
        tickerplant=given.tickerplant or "", consumers=tuple(given.consumers),
        user=given.user or config.DEFAULT_USER, password_env=given.password_env or "",
        runtime_only=given.runtime_only, remote_schema_file=given.remote_schema_file,
        storage=Storage(tplogdir=given.tplogdir, schema_file=given.schema_file), runtime=runtime,
    )  # fmt: skip
    if saved is None:
        return Resolved(att, "new", path)
    _refuse_moving(saved, att, state_dir)
    return Resolved(replace(att, overrides=saved.overrides), "replaced", path, changed)


def _refuse_moving(saved: Attachment, att: Attachment, state_dir: Path | None) -> None:
    m = manifest_of(saved.name, state_dir)
    if m is None or m.state == "rolled_back":
        return
    where = lambda a: (  # noqa: E731
        a.stack.id, a.stack.host, a.stack.discovery_host, a.stack.discovery_port,
        a.publisher.job, a.tables,
    )  # fmt: skip
    if where(saved) != where(att):
        raise AttachmentError(
            f"attachment {saved.name} is {m.state} on stack {saved.stack.id}, and its manifest "
            "records what it changed there - the record detach reverses from. A replacement "
            "may not point it at another stack, job or tables: detach it first "
            f"(`uqs job detach {saved.name}`), or attach under another name"
        )


def already_running(att: Attachment, p: plan.Plan, state_dir: Path | None = None) -> bool:
    """An attachment already verified whose own publisher is running: the plan's
    only blocker is that publisher, and a re-run has nothing to apply - it
    re-verifies, and leaves the publisher as it is."""
    found = blockers(p.findings)
    m = manifest_of(att.name, state_dir)
    return (
        bool(found)
        and m is not None
        and m.state == "verified"
        and all(plan.own_publisher_running(att, f) for f in found)
    )


@dataclass
class Stage:
    #: save | apply | verify | start | reach
    name: str
    #: done | failed | skipped
    status: str
    detail: str = ""
    seconds: float = 0.0


@dataclass
class Outcome:
    stages: list[Stage] = field(default_factory=list)
    applied: applying.ApplyResult | None = None
    verified: verifying.VerifyResult | None = None
    #: what to do next, when a stage failed
    recovery: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(s.status == "failed" for s in self.stages)


def execute(
    resolved: Resolved,
    p: plan.Plan,
    ipc: Ipc,
    root: Path,
    publisher: Publisher,
    *,
    start: bool = True,
    confirmed_risks: str = "",
    state_dir: Path | None = None,
    host: Host | None = None,
) -> Outcome:
    """Save, apply, verify and start, in that order, stopping at the first
    failure: a failed apply or verify never reaches the publisher.

    Each stage is the existing one and repeats its own checks against the stack
    as it is now; `p` (the plan the operator was shown) decides only what is
    saved. Every stage is safe to repeat, so the recovery for any failure
    includes running the same command again.
    """
    att, out, name, host = resolved.att, Outcome(), resolved.att.name, host or Host()
    again, back = f"`uqs job attach {name}`", f"`uqs job detach {name}`"
    running = already_running(att, p, state_dir)

    def stage(what: str, status: str, detail: str, began: float) -> Stage:
        out.stages.append(Stage(what, status, detail, round(time.monotonic() - began, 3)))
        return out.stages[-1]

    t = time.monotonic()
    if running:
        # nothing to apply, but storage the attachment left unpinned (a wdb that was
        # down, or not among its consumers, when it was saved) is pinned now: a
        # backfill needs the HDB directory and never guesses it
        if pins := newly_pinned(att, p):
            where = save_planned(att, p, state_dir)
            stage("save", "done", f"pinned {', '.join(pins)} the stack now reports ({where})", t)
        else:
            stage("save", "skipped", "already attached and verified; the publisher is running", t)
        stage("apply", "skipped", "nothing to apply", t)
    else:
        where = save_planned(att, p, state_dir)
        if resolved.source == "replaced" and Manifest.exists(name, state_dir):
            with config.locked(name, state_dir):
                m = Manifest.load(name, state_dir)
                m.event("configuration replaced", "; ".join(resolved.changed))
                m.save()
        stage("save", "done", str(where), t)
        t = time.monotonic()
        try:
            out.applied = applying.apply(
                att, ipc, root, state_dir=state_dir, confirmed_risks=confirmed_risks,
                expected_sort={c.path: c.sha for c in p.sort_changes},
            )  # fmt: skip
        except applying.ApplyFailed as exc:
            out.applied = exc.result
            stage("apply", "failed", str(exc), t)
            out.recovery = [
                "the stack is partially changed and the publisher was not started",
                f"re-run {again}: every step is idempotent, and an unknown outcome is "
                "resolved by asking the process",
                f"or reverse what this attachment created with {back}",
            ]
            return out
        except AttachmentError as exc:
            stage("apply", "failed", str(exc), t)
            out.recovery = [
                "nothing was changed on the stack and the publisher was not started",
                f"resolve the blocker, then re-run {again}",
            ]
            return out
        stage("apply", "done", f"{out.applied.mutations()} change(s); {out.applied.state}", t)
    t = time.monotonic()
    try:
        out.verified = verifying.verify(att, ipc, state_dir, root, host)
    except AttachmentError as exc:
        stage("verify", "failed", str(exc), t)
    else:
        failed = [c.name for c in out.verified.checks if not c.ok]
        stage("verify", "failed" if failed else "done",
              f"{len(failed)} check(s) failed: {failed[0]}" if failed
              else "; ".join([f"{len(out.verified.checks)} check(s) passed",
                              *out.verified.warnings]), t)  # fmt: skip
    if out.stages[-1].status == "failed":
        out.recovery = (
            [
                f"{att.publisher.procname} is RUNNING although the attachment no longer "
                f"verifies: stop it (`uqs stop {att.publisher.procname} --port {att.stack.id}`)",
            ]
            if running
            else [f"the publisher ({att.publisher.procname}) was not started"]
        ) + [
            f"fix what the failed checks name, then re-run {again} (nothing is applied twice)",
            f"or reverse what this attachment created with {back}",
        ]
        return out
    t = time.monotonic()
    if running:
        stage("start", "skipped", f"{att.publisher.procname} is already running", t)
    elif not start:
        stage("start", "skipped",
              f"--no-start: verified and held stopped; {again} starts it", t)  # fmt: skip
    elif publisher.running(att):
        stage("start", "skipped", f"{att.publisher.procname} is already running", t)
    else:
        try:
            publisher.start(att)
        except AttachmentError as exc:
            stage("start", "failed", str(exc), t)
            out.recovery = [
                "the attachment is applied and verified; only the publisher's start failed",
                f"fix the cause, then re-run {again}, or "
                f"`uqs start {att.publisher.procname} --port {att.stack.id}`",
            ]
            return out
        stage("start", "done", f"{att.publisher.procname} on stack {att.stack.id}", t)
        if reach.applies(att, host):
            tp = p.observation.tickerplant
            _reach(att, ipc, root, publisher, out, state_dir, tp.endpoint if tp else None)
    return out


def _reach(
    att: Attachment,
    ipc: Ipc,
    root: Path,
    publisher: Publisher,
    out: Outcome,
    state_dir: Path | None,
    tickerplant: Endpoint | None,
) -> None:
    """The publisher just started, and the stack is elsewhere: can it reach the
    stack with its own credential, and the stack reach it back? If not it is not
    attached: the gate closes again and the publisher this run started is
    stopped, so it is never left running as if verified."""
    t, port = time.monotonic(), plan.publisher_port(att, root).port
    found = (
        None
        if port is None
        else reach.after_start(att, ipc, port, publisher, tickerplant=tickerplant)
    )
    seconds = round(time.monotonic() - t, 3)
    if found is None:
        out.stages.append(
            Stage("reach", "done", "the publisher and the stack reach each other", seconds)
        )
        return
    out.stages.append(Stage("reach", "failed", f"{found.code}: {found.message}", seconds))
    verifying.revoke(att, state_dir, f"{found.code}: {found.facts['address']}")
    proc = att.publisher.procname
    try:
        stopped = stop_publisher(att, publisher)
    except AttachmentError as exc:
        out.recovery = [f"{exc}: stop it (`uqs stop {proc} --port {att.stack.id}`)"]
    else:
        out.recovery = [f"{proc} was stopped" if stopped else f"{proc} is not running"]
    out.recovery.append(found.action)
