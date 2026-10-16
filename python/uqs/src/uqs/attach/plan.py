"""Plan: identify the stack, the job, the tables and every affected component,
and say what applying would change. Makes no mutation - the stack is only ever
asked `probe`.

A plan is a value: `as_dict()` is what `--json` prints and what tests assert
on, and the CLI renders the same thing for a person. It carries the findings
of the preflight, so "can I apply this" is `plan.ok`, not a second question.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from uqs.attach import preflight
from uqs.attach.config import Attachment
from uqs.attach.findings import Finding, blockers
from uqs.attach.remote import Ipc
from uqs.attach.topology import Observation, Proc, observe


@dataclass
class Plan:
    attachment: Attachment
    observation: Observation
    findings: list[Finding]
    publisher_port: preflight.PublisherPort

    @property
    def ok(self) -> bool:
        return not blockers(self.findings)

    def _state(self, proc: Proc | None, table: str) -> str:
        if proc is None or not proc.up:
            return "unknown"
        return "present" if (proc.probe or {}).get("schemas", {}).get(table) else "absent"

    def changes(self) -> list[dict]:
        """The ordered changes applying would make, with what each can be
        undone as. `reversible_before` / `reversible_after` are about
        publication: before it, newly created empty state can be removed; after
        it, nothing is deleted (rows, logs and history are kept)."""
        att, obs, steps = self.attachment, self.observation, []
        for spec in att.tables:
            tp = obs.tickerplant
            created = self._state(tp, spec.name) == "absent"
            steps.append(
                {
                    "process": att.stack.tickerplant or (tp.procname if tp else "?"),
                    "action": "register table" if created else "adopt existing table",
                    "table": spec.name,
                    "creates": created,
                    "reversible_before": "remove" if created else "n/a (pre-existing)",
                    "reversible_after": "retained" if created else "n/a (pre-existing)",
                    "needs_restart": False,
                }
            )
            for c in obs.consumers:
                made = self._state(c, spec.name) == "absent"
                steps.append(
                    {
                        "process": c.procname,
                        "action": "subscribe" + (" and create table" if made else ""),
                        "table": spec.name,
                        "creates": made,
                        "reversible_before": "unsubscribe, drop empty table" if made else "n/a",
                        "reversible_after": "retained" if made else "n/a (pre-existing)",
                        "needs_restart": False,
                    }
                )
        if att.persist == "schema-file":
            path = (obs.tickerplant.probe or {}).get("schemafile", "") if obs.tickerplant else ""
            steps.append(
                {
                    "process": "schema file",
                    "action": f"append a marked block to {path}",
                    "table": ", ".join(t.name for t in att.tables),
                    "creates": True,
                    "reversible_before": "remove the block (hash checked)",
                    "reversible_after": "retained (restart and replay need it)",
                    "needs_restart": False,
                }
            )
        return steps

    def components(self) -> list[dict]:
        obs = self.observation
        rows: list[tuple[str, Proc | None, str]] = [("discovery", obs.discovery, "read")]
        rows.append(("tickerplant", obs.tickerplant, "registers the table, logs it"))
        rows += [(c.proctype, c, "subscribes, persists") for c in obs.consumers]
        rows += [
            ("not managed", p, "unchanged until restarted") for p in obs.unmanaged + obs.chained
        ]
        out: list[dict] = [
            {
                "role": role,
                "procname": p.procname,
                "proctype": p.proctype,
                "endpoint": p.endpoint.label,
                "up": p.up,
                "effect": effect,
            }
            for role, p, effect in rows
            if p is not None
        ]
        out += [
            {
                "role": "other",
                "procname": s["procname"],
                "proctype": s["proctype"],
                "endpoint": s["hpup"],
                "up": None,
                "effect": "none until end of day",
            }
            for s in obs.others
        ]
        return out

    def as_dict(self) -> dict:
        att = self.attachment
        return {
            "attachment": att.name,
            "ok": self.ok,
            "stack": {
                "id": att.stack.id,
                "host": att.stack.host,
                "discovery": f"{att.stack.discovery_host}:{att.stack.discovery_port}",
                "tickerplant": att.stack.tickerplant,
                "user": att.stack.user,
                "password_env": att.stack.password_env or "(TorQ default credential)",
            },
            "job": {"name": att.publisher.job, "procname": att.publisher.procname, **att.origin},
            "publisher_port": self.publisher_port.port,
            "tables": [{"name": t.name, "columns": t.as_pairs()} for t in att.tables],
            "persist": att.persist,
            "storage": att.storage.__dict__,
            "components": self.components(),
            "changes": self.changes(),
            "findings": [f.as_dict() for f in self.findings],
        }


def publisher_port(att: Attachment, root: Path) -> preflight.PublisherPort:
    """stack id + the job's locked offset; provisional when the job is not in
    process_ports.csv yet (installing it appends the next free one)."""
    from uqs.model.registry import allocate_offsets, read_port_lock

    locked = read_port_lock(root)
    offsets = allocate_offsets([att.publisher.procname], locked)
    return preflight.PublisherPort(
        att.stack.id + offsets[att.publisher.procname], att.publisher.procname not in locked
    )


def build(att: Attachment, ipc: Ipc, root: Path, facts: preflight.Facts | None = None) -> Plan:
    """Observe the stack and check it. Read-only."""
    obs, structural = observe(att, ipc)
    port = publisher_port(att, root)
    facts = facts or preflight.Facts()
    facts.publisher = port
    return Plan(att, obs, structural + preflight.check(att, obs, facts), port)
