"""One declaration for every guardrail a uqs command enforces (#1825, part of #1824).

A guardrail is a check a command refuses on. Each owning module declares its
own as `Guardrail` values, beside the code that enforces them:

  - stack/start_overrides.py: the start refusals `--override NAME` may skip,
    each with a generated --skip-<id> (#1918);
  - deploy/overrides.py: the deployment requirements `deploy push` may relax;
  - attach/overrides.py: the attachment finding codes, from its POLICY table,
    each waived per component as `CODE:COMPONENT` - TORQ_VERSION and
    PUBLISHER_RUNNING with a generated --skip-<code> COMPONENT (#1918).
  - attach/backfill_policy.py: `eod-fence`, the end-of-day fence an
    attached `job backfill` may waive (#1828), with a generated --skip-eod-fence.

cli/safety.registry() gathers them in that order through `aggregate`, which
refuses two with one id; `uqs override list` (cli/safety.catalog) reads it, and so
does cli/skip_options.py, which gives a command a `--skip-<id>` option for
each guardrail of it declared with `skip_flag`. This module imports nothing
from this package, so every layer may import it (test_module_split holds it
to that).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol

#: A guardrail's id: words of letters and digits joined by `-` or `_`
#: (`q-version-check`, `TORQ_VERSION`).
ID_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)*")


@dataclass(frozen=True)
class Guardrail:
    """One check a command enforces, and what waiving it means."""

    #: stable id: the `--override` name, or the finding code a waiver names
    id: str
    #: the commands that enforce it, as `uqs` spells them (`deploy push`)
    commands: tuple[str, ...]
    #: what waiving it does - `uqs override list`'s effect column
    description: str
    #: the concrete failure waiving it risks, where that is said apart from the description
    risk: str = ""
    #: what the operator must do about it once waived
    action: str = ""
    #: whether any waiver is accepted at all
    overridable: bool = True
    #: whether a waiver names one component (`CODE:COMPONENT`) rather than the whole command
    component_scoped: bool = False
    #: whether each of `commands` takes a generated `--skip-<id>` option for it
    #: (cli/skip_options.py, #1826) beside `--override`. Off unless the command
    #: honours the waiver at run time: a flag that skips nothing is never shown.
    skip_flag: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not ID_PATTERN.fullmatch(self.id):
            raise ValueError(f"guardrail id {self.id!r} is not a slug like q-version-check")
        if not isinstance(self.commands, tuple) or not self.commands:
            raise ValueError(f"guardrail {self.id}: commands must be a nonempty tuple")
        if any(not isinstance(c, str) or not c.strip() or c != c.strip() for c in self.commands):
            raise ValueError(f"guardrail {self.id}: a command is blank or padded: {self.commands}")
        if len(set(self.commands)) != len(self.commands):
            raise ValueError(f"guardrail {self.id}: a command is named twice: {self.commands}")
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError(f"guardrail {self.id}: the description is empty")
        if self.skip_flag and not self.overridable:
            raise ValueError(
                f"guardrail {self.id}: one that is not overridable gets no --skip flag"
            )

    @property
    def name(self) -> str:
        """The waiver as an operator writes it: the id spelled as every
        override is (`spelled`), with `:COMPONENT` where it is waived per
        component - `torq-version:COMPONENT` (#1884)."""
        return f"{spelled(self.id)}:COMPONENT" if self.component_scoped else spelled(self.id)


def spelled(gid: str) -> str:
    """A guardrail id as `--override` takes it: lower case, words joined by
    `-` - a finding code `TORQ_VERSION` is `torq-version` (#1884)."""
    return gid.lower().replace("_", "-")


class PolicyLike(Protocol):
    """A row of a finding-code policy table, such as attach/overrides.Policy.

    Read-only properties, not plain attributes: `from_policy` only reads a row,
    and a plain protocol attribute is writable, which a frozen dataclass row
    (attach's Policy) is not - so a row would not satisfy it."""

    @property
    def overridable(self) -> bool: ...
    @property
    def risk(self) -> str: ...
    @property
    def action(self) -> str: ...
    @property
    def waives(self) -> str: ...
    @property
    def skip_flag(self) -> bool: ...


def from_policy(
    table: Mapping[str, PolicyLike], commands: tuple[str, ...]
) -> tuple[Guardrail, ...]:
    """A finding-code policy table ({CODE: policy}) as guardrails, by code: each
    waived per component, and described by what accepting it lifts - or, for a
    row that does not say, by its risk."""
    return tuple(
        Guardrail(
            code,
            commands,
            policy.waives or policy.risk,
            risk=policy.risk,
            action=policy.action,
            overridable=policy.overridable,
            component_scoped=True,
            skip_flag=policy.skip_flag,
        )
        for code, policy in sorted(table.items())
    )


def aggregate(groups: Iterable[Iterable[Guardrail]]) -> tuple[Guardrail, ...]:
    """The guardrails of `groups`, in order - refusing an id declared twice."""
    seen: set[str] = set()
    out: list[Guardrail] = []
    for group in groups:
        for g in group:
            if g.id in seen:
                raise ValueError(f"guardrail {g.id!r} is declared twice: ids must be unique")
            seen.add(g.id)
            out.append(g)
    return tuple(out)
