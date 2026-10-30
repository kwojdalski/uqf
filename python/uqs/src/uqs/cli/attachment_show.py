"""`uqs job attachment show NAME`, and the verbs the attachment noun replaced (#1737).

`show` is one attachment: what was saved for it and its state now. `uqs job
attach` and `uqs job detach` are its `add` and `remove` (attach.py), kept here
for one release as hidden aliases that log the new spelling. Split from
attach.py, which holds the two commands that change a stack.
"""

from __future__ import annotations

from uqs.attach import config
from uqs.cli.attach import EXTRA_WORDS, NameArg, attach, detach
from uqs.cli.attach_output import redact, secrets_of
from uqs.cli.attachment import OLD_REASON, attachment_app
from uqs.cli.json_out import emit, json_opt
from uqs.cli.renames import alias_command
from uqs.cli.safety import ReadOnly
from uqs.cli.shared import JOB_OPERATIONS_PANEL, _die, _paths, console, job_app
from uqs.paths import UqsError
from uqs.stack import attach_state

ShowJson = json_opt('{"attachment": {name, stack, publisher, tables, ...}, "state": {...}}')


@attachment_app.command("show", cls=ReadOnly)
def show(name: NameArg, as_json: ShowJson = False) -> None:
    """One attachment: what was saved for it, and its state now.

    The state is its `uqs job attachment list` row - the lifecycle state and,
    separately, whether its publisher runs; the rest is its saved
    configuration, the stack it is on, its publisher, tables and storage, with
    any credential redacted. Changes nothing.
    """
    try:
        att = config.load(name)
    except UqsError as exc:
        _die(exc)
        return
    rows = attach_state.inventory(repo=_paths().repo_root)
    state = next((r for r in rows if r["attachment"] == name), {})
    doc = redact({"attachment": config.to_json(att), "state": state}, secrets_of(att))
    if as_json:
        emit(doc)
        return
    for key, value in doc["state"].items():
        console.print(f"[bold]{key}[/]: {value}", soft_wrap=True, highlight=False)
    console.print_json(data=doc["attachment"])


# The verbs that were the noun's only spelling, kept for one release (#1737).
alias_command(
    job_app, "attach", "uqs job attachment add", attach, cls=OLD_REASON,
    rich_help_panel=JOB_OPERATIONS_PANEL, context_settings=EXTRA_WORDS,
)  # fmt: skip
alias_command(
    job_app, "detach", "uqs job attachment remove", detach,
    rich_help_panel=JOB_OPERATIONS_PANEL, context_settings=EXTRA_WORDS,
)  # fmt: skip
