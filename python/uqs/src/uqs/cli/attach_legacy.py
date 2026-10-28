"""The stage commands `uqs job attach` replaced (#1457), refused with what
replaced each rather than taken as an attachment's name. Split from attach.py."""

from __future__ import annotations

from uqs.cli.attach_output import Output

#: The stage commands #1457 replaced, and what each is now. Refused with this
#: text rather than taken as an attachment's name.
MIGRATION = {
    "plan": ("uqs job attach {name} --dry-run", "the same options; saves nothing"),
    "validate": ("uqs job attach {name} --dry-run", "exits 1 on a blocker"),
    "status": ("uqs job attachment list", "or `uqs job attach {name} --dry-run` for the plan"),
    "apply": ("uqs job attach {name} --no-start", "applies and verifies"),
    "verify": ("uqs job attach {name} --no-start", "re-checks; applies nothing twice"),
    "start": ("uqs job attach {name}", "re-verifies, then starts the publisher"),
    "detach": ("uqs job detach {name}", ""),
}


def refuse_legacy(out: Output, command: str, name: str, extra: list[str]) -> None:
    if name in MIGRATION:
        target = extra[0] if extra else "NAME"
        new, note = (part.format(name=target) for part in MIGRATION[name])
        out.fail(
            f"`uqs job attach {name}` was replaced (#1457): run `{new}`"
            + (f" ({note})" if note else "")
        )
    if extra:
        out.fail(f"unexpected argument(s) {extra}: `uqs {command}` takes one attachment name")
