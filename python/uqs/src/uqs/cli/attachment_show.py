"""`uqs job attachment show NAME`: one attachment, what was saved for it and its
state now. Split from attach.py, which holds the two commands that change a stack.
"""

from __future__ import annotations

from uqs.attach import backfill_audit, config
from uqs.cli.attach import NameArg
from uqs.cli.attach_output import redact, secrets_of
from uqs.cli.attachment import attachment_app
from uqs.cli.json_out import emit, json_opt
from uqs.cli.safety import ReadOnly
from uqs.cli.shared import _die, _paths, console
from uqs.paths import UqsError
from uqs.stack import attach_state

ShowJson = json_opt(
    '{"attachment": {name, stack, publisher, tables, ...}, "state": {...}, '
    '"backfill_overrides": {"record": path, "entries": [...]}}'
)


@attachment_app.command("show", cls=ReadOnly)
def show(name: NameArg, as_json: ShowJson = False) -> None:
    """Show one attachment: what was saved for it, and its state now.

    The state is its `uqs job attachment list` row - the lifecycle state and,
    separately, whether its publisher runs; the rest is its saved
    configuration, the stack it is on, its publisher, tables and storage, with
    any credential redacted. Changes nothing. Last, every waiver an attached
    backfill into its destination used (`--skip-eod-fence`), from that
    destination's backfill_overrides.jsonl: an accepted risk, never saved here.
    """
    try:
        att = config.load(name)
    except UqsError as exc:
        _die(exc)
        return
    rows = attach_state.inventory(repo=_paths().repo_root)
    state = next((r for r in rows if r["attachment"] == name), {})
    record, waivers = backfill_audit.of_attachment(_paths(), name)
    overrides = {"record": None if record is None else str(record), "entries": waivers}
    doc = redact(
        {"attachment": config.to_json(att), "state": state, "backfill_overrides": overrides},
        secrets_of(att),
    )
    if as_json:
        emit(doc)
        return
    for key, value in doc["state"].items():
        console.print(f"[bold]{key}[/]: {value}", soft_wrap=True, highlight=False)
    console.print_json(data=doc["attachment"])
    if waivers:
        console.print(f"[bold]backfill overrides[/] ({record}):", highlight=False)
        for line in backfill_audit.lines_of(doc["backfill_overrides"]["entries"]):
            console.print(f"  {line}", markup=False, soft_wrap=True, highlight=False)
