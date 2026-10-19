"""What `uqs job attach` and `uqs job detach` print (#1457): the plan tables,
the stage results, and the redaction every one of them passes through.

Every string printed - in a table, in a line, in `--json`, in an error or in
`--debug` detail - goes through `redact` first.
"""

from __future__ import annotations

import json
import re
from typing import Any

import typer
from rich.table import Table

from uqs.attach.config import Attachment
from uqs.cli import shared
from uqs.cli.shared import console
from uqs.paths import UqsError

_STYLE = {"blocker": "red", "accepted": "bold magenta", "warning": "yellow", "info": "dim"}
_OUTCOME = {
    "created": "green", "adopted": "cyan", "subscribed": "cyan", "shared": "cyan", "already": "dim",
    "removed": "green", "absent": "dim", "left": "cyan", "done": "green", "skipped": "dim",
}  # fmt: skip


# ------------------------------------------------------------------ redaction

_HPUP = re.compile(r"(:[\w.\-]+:\d+:[^:\s\"']+):[^\s,;\"'\])]+")
_URL = re.compile(r"(//[^:/\s@]+):[^@\s]+@")


def secrets_of(att: Attachment | None) -> list[str]:
    """The secret this attachment's credential resolves to, when one is set."""
    if att is None or not att.stack.password_env:
        return []
    try:
        value = att.stack.password()
    except UqsError:
        return []
    return [value] if value else []


def redact(value: Any, secrets: list[str]) -> Any:
    """`value` with every credential replaced by ***: the password itself
    wherever it appears, and the password field of a TorQ `:host:port:user:pass`
    or a `//user:pass@` address."""
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, "***")
        return _URL.sub(r"\1:***@", _HPUP.sub(r"\1:***", value))
    if isinstance(value, dict):
        return {k: redact(v, secrets) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(v, secrets) for v in value]
    return value


class Output:
    """Where this command's output goes: redacted, and in --json mode held for
    the one document printed at the end."""

    def __init__(self, as_json: bool, debug: bool) -> None:
        self.as_json, self.debug, self.secrets = as_json, debug, []
        self.doc: dict[str, Any] = {}
        self.notes: list[str] = []

    def text(self, line: str) -> None:
        if not self.as_json:
            console.print(redact(line, self.secrets))

    def table(self, table: Table) -> None:
        if not self.as_json:
            console.print(table)

    def detail(self, line: str) -> None:
        """--debug only."""
        if self.debug:
            self.notes.append(redact(line, self.secrets))
            self.text(f"[dim]debug: {line}[/]")

    def fail(self, exc: Exception | str, code: int = 1) -> None:
        message = redact(str(exc), self.secrets)
        shared.log.error("{}", message)
        self.finish(ok=False, error=message, code=code)

    def finish(self, ok: bool, code: int | None = None, **extra: Any) -> None:
        if self.as_json:
            doc = {**self.doc, **extra, "ok": ok}
            if self.debug:
                doc["debug"] = self.notes
            print(json.dumps(redact(doc, self.secrets), indent=2, default=str))
        raise typer.Exit(code=(0 if ok else 1) if code is None else code)


# ------------------------------------------------------------------- rendering


def render_plan(out: Output, d: dict) -> None:
    """The plan tables, as `plan` showed them: identity, tables, components,
    changes, findings and accepted risks."""
    st = d["stack"]
    out.text(
        f"[bold]{d['attachment']}[/]: job {d['job']['name']} ({d['job']['procname']}) -> "
        f"stack {st['id']} on {st['host']}, discovery {st['discovery']}"
    )
    out.text(
        f"credential: {st['user']} / {st['password_env']}   persist: {d['persist']}   "
        f"publisher port: {d['publisher_port']}"
    )
    for t in d["tables"]:
        out.text(
            f"table {t['name']}: " + ", ".join(f"{n}:{c.strip() or '*'}" for n, c in t["columns"])
        )
    comps = Table(title="Affected components")
    for column in ("role", "procname", "endpoint", "up", "effect"):
        comps.add_column(column, overflow="fold")
    for c in d["components"]:
        comps.add_row(c["role"], c["procname"], c["endpoint"], str(c["up"]), c["effect"])
    out.table(comps)
    steps = Table(title="Changes attach would make (no process is restarted)")
    for column in ("process", "action", "table", "before publication", "after publication"):
        steps.add_column(column, overflow="fold")
    for s in d["changes"]:
        steps.add_row(
            s["process"], s["action"], s["table"], s["reversible_before"], s["reversible_after"]
        )
    out.table(steps)
    render_findings(out, d["findings"])
    if "manifest" in d:
        ops = Table(title=f"Manifest: {d['manifest']['state']}")
        for column in ("#", "step", "process", "table", "status", "ownership", "note"):
            ops.add_column(column, overflow="fold")
        for o in d["manifest"]["operations"]:
            ops.add_row(str(o["id"]), o["op"], o["process"], o["table"], o["status"],
                        o["ownership"], o["note"])  # fmt: skip
        out.table(ops)
        for line in d["health"]:
            out.text(f"[yellow]{line}[/]")
    for problem in d["override_problems"]:
        out.text(f"[yellow]override:[/] {problem}")


def render_findings(out: Output, findings: list[dict]) -> None:
    if not findings:
        return
    table = Table(title="Findings")
    for column in ("level", "code", "component", "message"):
        table.add_column(column, overflow="fold")
    order = ("blocker", "accepted", "warning", "info")
    for f in sorted(findings, key=lambda f: order.index(f["level"])):
        action = f"\n[dim]-> {f['action']}[/]" if f["action"] else ""
        table.add_row(
            f"[{_STYLE[f['level']]}]{f['level']}[/]", f["code"], f["component"],
            f["message"] + action,
        )  # fmt: skip
    out.table(table)


def verdict(out: Output, d: dict, running: bool) -> None:
    accepted = d["accepted_risks"]
    if running:
        out.text(
            "[cyan]already attached and verified, and its publisher is running[/]: "
            "nothing to apply - execution re-verifies and leaves the publisher as it is"
        )
    elif not d["ok"]:
        out.text("[red]blocked: attaching would refuse this stack[/]")
    elif accepted:
        out.text(
            f"[bold magenta]no blockers - but {len(accepted)} ACCEPTED RISK(S)[/]: "
            + ", ".join(f"{f['code']}:{f['component']}" for f in accepted)
            + ". Accepted is not verified: nothing here says this stack is compatible."
        )
    else:
        out.text("[green]no blockers[/]")


def steps_table(out: Output, title: str, steps: list[dict]) -> None:
    if not steps:
        return
    table = Table(title=title)
    for column in ("step", "process", "table", "outcome", "detail"):
        table.add_column(column, overflow="fold")
    for s in steps:
        style = _OUTCOME.get(s["outcome"], "red")
        table.add_row(s["op"], s["process"], s["table"], f"[{style}]{s['outcome']}[/]", s["detail"])
    out.table(table)


def checks_table(out: Output, checks: list[dict]) -> None:
    table = Table(title="Verify (reads only; publishes nothing)")
    for column in ("check", "ok", "detail"):
        table.add_column(column, overflow="fold")
    for c in checks:
        table.add_row(c["name"], "[green]yes[/]" if c["ok"] else "[red]NO[/]", c["detail"])
    out.table(table)


def stages_table(out: Output, stages: list[dict]) -> None:
    table = Table(title="Stages")
    for column in ("stage", "status", "detail") + (("seconds",) if out.debug else ()):
        table.add_column(column, overflow="fold")
    for s in stages:
        style = _OUTCOME.get(s["status"], "red")
        cells = [s["stage"], f"[{style}]{s['status']}[/]", s["detail"]]
        table.add_row(*cells, *([f"{s['seconds']:.3f}"] if out.debug else []))
    out.table(table)
