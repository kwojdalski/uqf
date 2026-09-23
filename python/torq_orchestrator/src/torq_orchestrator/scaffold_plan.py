"""What a scaffold plan IS: the write modes, the actions, the plan itself.

Split from scaffold.py when it crossed the 400-line threshold this package
holds its modules to. The seam is the same one `pipeline.py` and `registry.py`
already use: this is the SHAPE of a plan, scaffold.py is what builds and
applies one. They change for different reasons - a new field here, a new kind
of job there.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class WriteMode(StrEnum):
    """Whether an action makes a file or adds to one.

    This was a bare `str` with the two values in a comment beside it, and the
    branching read `if mode == "create" ... else: append`. So a typo did not
    raise - it fell through to the append branch, past the guard that checks
    the target exists (which tests for `"append"` exactly), and reached
    `read_text()` on a file that may not be there. An unhandled traceback,
    from a one-character mistake, at the point where files are already being
    written.

    A StrEnum rather than a plain Enum for the same reason as PipelineKind:
    the value reaches `describe()`, which --dry-run prints.
    """

    #: Make the file. Refuses if it already exists.
    CREATE = "create"
    #: Add to the file. Refuses if it does not exist.
    APPEND = "append"


@dataclass(frozen=True)
class FileAction:
    """One file this scaffold would create, or one block it would append."""

    path: Path
    body: str
    mode: WriteMode = WriteMode.CREATE

    def describe(self) -> str:
        verb = "create" if self.mode is WriteMode.CREATE else "append to"
        n = len(self.body.splitlines())
        # The nsList entry is a single symbol, so the count is genuinely 1 here
        # and "1 lines" is what --dry-run would print.
        return f"{verb} {self.path} ({n} line{'' if n == 1 else 's'})"


@dataclass(frozen=True)
class ScaffoldPlan:
    """Everything a new job needs, before any of it is written."""

    name: str
    actions: list[FileAction] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"scaffold {self.name}:"]
        lines += [f"  {a.describe()}" for a in self.actions]
        lines += [f"  note: {n}" for n in self.notes]
        return "\n".join(lines)


#: q type CHARACTERS, as `meta` reports them - which is what a source's
#: `types` string is compared against at registration. Note `j` for a long,
#: not `l`: the first draft of demo_events.q wrote "l" and was refused,
#: correctly.
