"""The live half of an attached backfill's admission, and the plan it prints (#1454).

uqs.attach.backfill resolves the destination offline. This asks the stack - which
processes write that directory, which HDB readers serve it - runs the worker's plan
in q against the destination's own ledgers, and assembles the report. Read-only
throughout: every call to the stack is `probe` or `hdb_reader`, and the q it runs
is given no HDB root, no IO manager and no lock.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from uqs.attach import config
from uqs.attach.backfill import PLAN_SCRIPT, Destination, canonical, resolve
from uqs.attach.backfill_coordination import assess
from uqs.attach.config import Attachment
from uqs.attach.remote import Endpoint, Ipc, IpcError
from uqs.attach.topology import Observation, observe, port_of
from uqs.interpreter import q_interpreter
from uqs.paths import UqsError, UqsPaths
from uqs.stack import backfill as stack_backfill


@dataclass(frozen=True)
class Reader:
    """One HDB process, and whether it serves the destination."""

    procname: str
    port: int
    serves: bool
    detail: str = ""


@dataclass
class Admission:
    """What the live stack says about a destination."""

    writers: list[str] = field(default_factory=list)
    readers: list[Reader] = field(default_factory=list)
    #: what the stack showed, kept for the coordination checks that follow
    observation: Observation | None = None

    @property
    def reload_targets(self) -> list[Reader]:
        return [r for r in self.readers if r.serves]


def _same_dir(reported: str, where: Path) -> bool:
    reported = reported.lstrip(":")
    return bool(reported) and canonical(reported) == where


def _writers(att: Attachment, obs: Observation, dest: Destination) -> list[str]:
    """The wdb/rdb processes whose HDB directory is the destination."""
    named = [*obs.consumers, *obs.unmanaged]
    reporting = [p for p in named if p.up and p.proctype in ("wdb", "rdb")]
    disagree = [
        p.procname
        for p in reporting
        if (p.probe or {}).get("hdbdir") and not _same_dir((p.probe or {})["hdbdir"], dest.hdb_dir)
    ]
    if disagree:
        raise UqsError(
            f"{', '.join(disagree)} report another HDB directory than attachment {att.name}'s "
            f"{dest.hdb_dir}: the destination is ambiguous, so nothing is planned"
        )
    writers = [
        p.procname for p in reporting if _same_dir((p.probe or {}).get("hdbdir", ""), dest.hdb_dir)
    ]
    if not any(p.proctype == "wdb" for p in reporting if p.procname in writers):
        raise UqsError(
            f"no wdb on stack {att.stack.id} reports {dest.hdb_dir} as its HDB: the process that "
            "writes the destination cannot be identified, so it cannot be coordinated with"
        )
    return sorted(writers)


def _readers(att: Attachment, obs: Observation, dest: Destination, ipc: Ipc) -> list[Reader]:
    out: list[Reader] = []
    for svc in obs.others:
        if svc["proctype"] != "hdb":
            continue
        port = port_of(svc["hpup"])
        if port is None:
            continue
        who = svc["procname"]
        try:
            answer = ipc.call(Endpoint(who, att.stack.host, port), "hdb_reader", {})
        except IpcError as exc:
            raise UqsError(
                f"HDB reader {who} (port {port}) does not answer: {exc.reason} - whether it "
                "serves the destination cannot be established, so it cannot be left out of a reload"
            ) from None
        if answer.get("stackid") not in ("", str(att.stack.id)):
            out.append(
                Reader(who, port, False, f"stack {answer.get('stackid')}, not {att.stack.id}")
            )
        elif not _same_dir(str(answer.get("hdbdir", "")), dest.hdb_dir):
            out.append(
                Reader(who, port, False, f"serves {answer.get('hdbdir') or 'an unknown directory'}")
            )
        else:
            out.append(
                Reader(who, port, True, f"{answer.get('partitions', '?')} partition(s) mapped")
            )
    return sorted(out, key=lambda r: r.procname)


def admit(att: Attachment, dest: Destination, ipc: Ipc) -> Admission:
    """The live checks: who writes the destination, and who serves it.

    Read-only: every call is `probe` or `hdb_reader`. Refuses a stack that does
    not answer, whose processes disagree about the HDB, or whose HDB readers
    cannot be asked.
    """
    obs, structural = observe(att, ipc)
    if structural:
        raise UqsError("; ".join(f"{f.component}: {f.message}" for f in structural))
    return Admission(_writers(att, obs, dest), _readers(att, obs, dest, ipc), obs)


def partitions_on_disk(dest: Destination, dates: list[date]) -> list[dict[str, Any]]:
    """For each date a run would touch: does its partition exist, and does it
    already hold the dataset. Looks; creates nothing."""
    out = []
    for day in dates:
        part = dest.hdb_dir / day.strftime("%Y.%m.%d")
        out.append(
            {
                "date": day.isoformat(),
                "exists": part.is_dir(),
                "has_dataset": (part / dest.dataset).is_dir(),
            }
        )
    return out


def evaluate(
    paths: UqsPaths,
    dest: Destination,
    worker: str,
    version: str,
    range_from: datetime,
    range_to: datetime,
    on_conflict: str | None,
) -> dict[str, Any]:
    """Run scripts/dev/attached_plan.q against the destination's state directory.

    A short-lived q that loads the worker and asks `plan_only` what a run would
    fetch. It is given the status directory (to read coverage and the
    checkpoint) and no HDB root, no IO manager and no lock: it has nothing to
    write to. The directory need not exist and is not created.
    """
    q = q_interpreter(os.environ)
    if q is None:
        raise UqsError("no q interpreter - set $QCMD, or put q on PATH; the plan is evaluated by q")
    flags = stack_backfill.backfill_flags(
        worker, version, range_from, range_to, on_conflict=on_conflict, mode="plan"
    )
    result = subprocess.run(
        [str(q), str(paths.repo_root / PLAN_SCRIPT), *flags, "-q"],
        cwd=paths.repo_root,
        env={**os.environ, "UQF_STATUS_DIR": str(dest.state_dir)},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
    if not lines:
        raise UqsError(
            f"the plan could not be evaluated:\n{(result.stdout + result.stderr).strip()[-1500:]}"
        )
    facts = json.loads(lines[-1])
    if "error" in facts:
        raise UqsError(f"{worker} cannot be planned over that range: {facts['error']}")
    return facts


def _utc(text: str) -> str:
    return stack_backfill.parse_bound("window", text).isoformat()


def build_plan(
    paths: UqsPaths,
    att: Attachment,
    dest: Destination,
    admission: Admission,
    facts: dict[str, Any],
    ipc: Ipc,
    *,
    worker: str,
    version: str,
    range_from: datetime,
    range_to: datetime,
) -> dict[str, Any]:
    """The admission plan, or a refusal when the facts say a run must not start."""
    if facts["custom_io"]:
        raise UqsError(
            f"{worker} declares its own IO manager, which would write where it likes: an attached "
            "backfill writes only through the destination it was given"
        )
    if not facts["live"]:
        raise UqsError(
            f"{worker}'s source {facts['source']} has no credential configured: without one "
            "the run would write the source's FIXTURE into a native HDB. Configure the "
            "source, or backfill a stack of your own"
        )
    windows = [
        {
            "range_from": _utc(w["range_from"]),
            "range_to": _utc(w["range_to"]),
            "decision": w["decision"],
        }
        for w in facts["windows"]
    ]
    dates = [date.fromisoformat(d.replace(".", "-")) for d in facts["dates"]]
    assert admission.observation is not None
    coordination = assess(att, dest, admission.observation, admission.writers, ipc, dates)
    return {
        "mode": "plan",
        "worker": worker,
        "attachment": att.name,
        "destination": dest.as_dict(),
        "source": {"name": facts["source"], "live": facts["live"]},
        "dataset": facts["dataset"],
        "source_version": version,
        "range": {
            "from": range_from.astimezone(UTC).isoformat(),
            "to": range_to.astimezone(UTC).isoformat(),
            "half_open": "[from, to)",
        },
        "conflict_policy": facts["on_conflict"],
        "windows": windows,
        "coverage": {
            k: sum(1 for w in windows if w["decision"] == k)
            for k in ("fetch", "covered", "deferred")
        },
        "native_partitions": partitions_on_disk(dest, dates),
        "writers": admission.writers,
        "coordination": coordination.as_dict(),
        "beside_the_root": {
            "staging": f"{dest.hdb_dir}.staging",
            "write_lock": f"{dest.hdb_dir}.write.lock",
        },
        "reload_targets": [
            {"procname": r.procname, "port": r.port} for r in admission.reload_targets
        ],
        "readers_not_serving": [
            {"procname": r.procname, "detail": r.detail} for r in admission.readers if not r.serves
        ],
        "mutates": False,
    }


def render(plan: dict[str, Any]) -> str:
    """The plan as an operator reads it."""
    d, cov, rng = plan["destination"], plan["coverage"], plan["range"]
    head = f"admission plan: {plan['worker']} -> attachment {plan['attachment']}"
    lines = [
        f"{head} (nothing was written)",
        f"  destination   {d['hdb_dir']}",
        f"                stack {d['stack_id']} on {d['host']}, id {d['identity']}",
        f"  state         {d['state_dir']}",
        f"  source        {plan['source']['name']} -> {plan['dataset']}",
        f"  version       {plan['source_version']}",
        f"  range         {rng['from']} to {rng['to']}  {rng['half_open']}",
        f"  conflict      {plan['conflict_policy']}",
        f"  windows       {len(plan['windows'])}: fetch {cov['fetch']}, already covered "
        f"{cov['covered']}, deferred {cov['deferred']}",
    ]
    for w in plan["windows"]:
        lines.append(f"    {w['decision']:<8} {w['range_from']} .. {w['range_to']}")
    lines.append("  native partitions a run would touch:")
    for p in plan["native_partitions"]:
        state = (
            "holds the dataset"
            if p["has_dataset"]
            else "exists"
            if p["exists"]
            else "would be created"
        )
        lines.append(f"    {p['date']}  {state}")
    lines.append(f"  writes it     {', '.join(plan['writers'])}")
    co = plan["coordination"]
    lines.append(
        f"  coordination  active write date {co['active_date']} is never touched (last writable "
        f"date {co['last_writable_date']}); writes stop at {co['writes_stop_at']}, "
        f"{co['eod_guard_minutes']} min before end of day; {co['filesystem']} filesystem"
    )
    lines.extend(f"    warning     {w}" for w in co["warnings"])
    lines.append(
        f"  beside root   {plan['beside_the_root']['staging']} (staging), "
        f"{plan['beside_the_root']['write_lock']} (write lock)"
    )
    targets = ", ".join(f"{t['procname']}:{t['port']}" for t in plan["reload_targets"]) or "none"
    lines.append(f"  reload        {targets}")
    for r in plan["readers_not_serving"]:
        lines.append(f"    not reloaded  {r['procname']} ({r['detail']})")
    return "\n".join(lines)


def plan_attached(
    paths: UqsPaths,
    attachment: str,
    worker: str,
    version: str,
    range_from: datetime,
    range_to: datetime,
    *,
    ipc: Ipc,
    port: int | None = None,
    fixture: bool = False,
    on_conflict: str | None = None,
    attach_root: Path | None = None,
) -> dict[str, Any]:
    """Resolve, admit and plan: everything `--attach --mode plan` does.

    Changes nothing - no directory, no ledger, no checkpoint, no symbol file, no
    stack state - and launches no worker.
    """
    dest = resolve(paths, attachment, worker, port=port, fixture=fixture, attach_root=attach_root)
    att = config.load(attachment, attach_root)
    admission = admit(att, dest, ipc)
    facts = evaluate(paths, dest, worker, version, range_from, range_to, on_conflict)
    return build_plan(
        paths,
        att,
        dest,
        admission,
        facts,
        ipc,
        worker=worker,
        version=version,
        range_from=range_from,
        range_to=range_to,
    )
