"""`uqs job attachment add NAME` as functions (#1457): the stages that execute
the attachment a command line means (resolution.py), in order, and the rules
that refuse to start them.

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
from dataclasses import dataclass, field
from pathlib import Path

from uqs.attach import apply as applying
from uqs.attach import backfill_owner, config, native, plan, reach
from uqs.attach import verify as verifying
from uqs.attach.config import Attachment, AttachmentError
from uqs.attach.facts import Host
from uqs.attach.findings import blockers
from uqs.attach.manifest import Manifest
from uqs.attach.publisher import Publisher, stop_publisher
from uqs.attach.remote import Endpoint, Ipc
from uqs.attach.resolution import Resolved, repins
from uqs.attach.service import manifest_of, newly_pinned, save_planned


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


def refuse_replacing_running(resolved: Resolved, running: bool) -> None:
    """A `--replace` of an attachment whose publisher is running (#2760).

    Every option a replacement can change reaches something only an apply,
    verify or the publisher's own start acts on: the stack and the processes
    apply changes, the credential verify and the start gate connect with, the
    persist mode (a block in the schema file), the storage it pins, the job and
    its tables - and `--process-file`/`--start-with-all` ride along into the
    native row. Apply refuses while the publisher runs, so such a replacement
    could only be saved as if applied: no block written, the start gate and a
    native `start all` trusting a configuration nothing carried out. So it is
    refused here, before anything is saved or any row written; with the
    publisher stopped the same command applies it.
    """
    if not running or resolved.source != "replaced":
        return
    proc, port = resolved.att.publisher.procname, resolved.att.stack.id
    raise AttachmentError(
        f"{proc} is running, and the replacement changes what apply, verify and its "
        f"registration act on ({'; '.join(resolved.changed)}). Apply refuses while the "
        f"publisher is running, so the replacement cannot be applied now: stop it first "
        f"(`uqs stop {proc} --base-port {port}`), then run the same command again. "
        "Nothing was saved"
    )


def refuse_running_unregistered(
    att: Attachment, p: plan.Plan, running: bool, state_dir: Path | None = None
) -> None:
    """A re-run of an attachment whose publisher runs on a registration the stack
    lost (#2765).

    A runtime-only registration is gone after a tickerplant restart, and the
    publisher, still running, has been publishing into a tickerplant without its
    table since: those rows were dropped. Apply refuses while the publisher runs,
    so the running branch of `execute` would only re-verify - and fail, taking
    `verified` back - with the publisher still running. Re-registering under it
    instead would resume publication as if nothing had been lost. So the re-run
    is refused up front, saying what was lost and that the stop comes first;
    with the publisher stopped the same command applies again, verifies, and
    starts it.
    """
    m = manifest_of(att.name, state_dir)
    gone = p.lost(m) if running and m is not None else []
    if not gone:
        return
    proc, port = att.publisher.procname, att.stack.id
    raise AttachmentError(
        f"{proc} is running, but the stack lost the registration it publishes into ("
        + "; ".join(f"{where} no longer holds {table}" for where, table in gone)
        + "): a tickerplant restart without a persisted schema drops it, so rows "
        f"{proc} published since that restart were dropped. Apply refuses while the "
        f"publisher is running: stop it first (`uqs stop {proc} --base-port {port}`), "
        f"then run `uqs job attachment add {att.name}` again - it re-applies and "
        "verifies before it starts the publisher. Nothing was saved or changed"
    )


def refuse_replacing_backfilled(resolved: Resolved, state_dir: Path | None = None) -> None:
    """A `--replace` of an attachment an attached backfill owns (#2758).

    A running backfill writes into the destination it was launched for, and keeps
    its ledgers, run lock and stop file in that destination's state directory. A
    replacement that changes the stack, tickerplant or consumers drops the storage
    pins it decides (resolution.carried) and re-observes them, so the config would name
    another HDB - or none - under a run still writing the old one, and every
    later admission would judge the run against a destination it is not using.
    So it is refused while a run owns the attachment, as detach is; with the run
    finished or stopped the same command replaces it. A plain re-run is not a
    replacement: it keeps every saved pin and applies nothing on a converged stack.
    """
    if resolved.source == "replaced":
        backfill_owner.refuse_while_active(resolved.att.name, state_dir, then="replace it")


def save(resolved: Resolved, p: plan.Plan, state_dir: Path | None = None) -> Path:
    """Save the configuration, with the storage the plan saw pinned into it.

    A replacement is saved under the attachment's lock, refused while a backfill
    owns the attachment (refuse_replacing_backfilled): launch claims under that
    lock (#1454, #1526), so no run can start between the check and the save. It is
    journalled, and takes a `verified` manifest back to `applied` (#2762):
    `verified` vouches for the configuration that was applied and verified, and
    the start gate and backfill trust it. A replaced one is neither until the
    apply and verify that follow succeed - and apply may still refuse it (a
    blocker, risks nobody confirmed), or the command stop at a blocked plan, with
    the replacement already saved."""
    name = resolved.att.name
    if resolved.source != "replaced":
        return save_planned(resolved.att, p, state_dir)
    with config.locked(name, state_dir):
        refuse_replacing_backfilled(resolved, state_dir)
        said = [*resolved.changed, *repins(resolved, p)]
        where = save_planned(resolved.att, p, state_dir)
        if Manifest.exists(name, state_dir):
            m = Manifest.load(name, state_dir)
            m.event("configuration replaced", "; ".join(said))
            if m.state == "verified":
                m.set_state("applied", "the configuration was replaced: apply and verify it")
            m.save()
    return where


@dataclass
class Stage:
    #: save | apply | verify | register | start | reach
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
    includes running the same command again. A replacement of an attachment
    whose publisher runs is refused before any stage (refuse_replacing_running),
    and one a backfill owns at its save (refuse_replacing_backfilled); so is a
    re-run whose publisher runs on a registration the stack lost
    (refuse_running_unregistered).
    """
    att, out, name, host = resolved.att, Outcome(), resolved.att.name, host or Host()
    again, back = f"`uqs job attachment add {name}`", f"`uqs job attachment remove {name}`"
    running = already_running(att, p, state_dir)
    refuse_replacing_running(resolved, running)
    refuse_running_unregistered(att, p, running, state_dir)

    def stage(what: str, status: str, detail: str, began: float) -> Stage:
        out.stages.append(Stage(what, status, detail, round(time.monotonic() - began, 3)))
        return out.stages[-1]

    t = time.monotonic()
    if running:
        # nothing to apply, but storage the attachment left unpinned (a wdb that was
        # down, or not among its consumers, when it was saved) is pinned now: a
        # backfill needs the HDB directory and never guesses it
        pins = newly_pinned(att, p)
        # a saved attachment's own change (an --ssh, the native settings) is saved too: it
        # reaches no process apply changes. A replacement never gets here (above)
        own = resolved.changed
        if pins or own:
            where = save_planned(att, p, state_dir)
            said = [f"pinned {', '.join(pins)} the stack now reports"] if pins else []
            stage("save", "done", f"{'; '.join([*said, *own])} ({where})", t)
        else:
            stage("save", "skipped", "already attached and verified; the publisher is running", t)
        stage("apply", "skipped", "nothing to apply", t)
    else:
        try:
            where = save(resolved, p, state_dir)
        except AttachmentError as exc:  # a backfill owns it, or another operation holds it
            stage("save", "failed", str(exc), t)
            out.recovery = [
                "nothing was saved, nothing on the stack changed and the publisher was not started",
                "resolve what it names, then re-run the same command",
            ]
            return out
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
                f"verifies: stop it "
                f"(`uqs stop {att.publisher.procname} --base-port {att.stack.id}`)",
            ]
            if running
            else [f"the publisher ({att.publisher.procname}) was not started"]
        ) + [
            f"fix what the failed checks name, then re-run {again} (nothing is applied twice)",
            f"or reverse what this attachment created with {back}",
        ]
        return out
    if att.native.process_file and not _register(att, root, p, out, state_dir):
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
                f"`uqs start {att.publisher.procname} --base-port {att.stack.id}`",
            ]
            return out
        stage("start", "done", f"{att.publisher.procname} on stack {att.stack.id}", t)
        if reach.applies(att, host):
            tp = p.observation.tickerplant
            _reach(att, ipc, root, publisher, out, state_dir, tp.endpoint if tp else None)
    return out


def _register(att: Attachment, root: Path, p: plan.Plan, out: Outcome,
              state_dir: Path | None) -> bool:  # fmt: skip
    """The publisher's row in the stack's process file (native.py): after verify,
    before any start, and not skipped by --no-start - it is the future policy."""
    t = time.monotonic()
    try:
        lost = out.verified.lost if out.verified else [t.name for t in att.tables]
        said = "; ".join(native.register(att, root, p.publisher_port, p.observation, state_dir,
                                         lost=lost))  # fmt: skip
    except (AttachmentError, OSError) as exc:
        said, out.recovery = str(exc), [
            "applied and verified; the process file is unchanged or as the manifest records",
            f"fix the cause, then re-run `uqs job attachment add {att.name}`",
        ]  # fmt: skip
    ok = not out.recovery
    out.stages.append(Stage("register", "done" if ok else "failed", said,
                            round(time.monotonic() - t, 3)))  # fmt: skip
    return ok


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
        out.recovery = [f"{exc}: stop it (`uqs stop {proc} --base-port {att.stack.id}`)"]
    else:
        out.recovery = [f"{proc} was stopped" if stopped else f"{proc} is not running"]
    out.recovery.append(found.action)
