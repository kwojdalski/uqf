"""What a plan or a preflight concluded, in a form a human and a test can read."""

from __future__ import annotations

from dataclasses import asdict, dataclass

LEVELS = ("blocker", "warning", "info")


@dataclass(frozen=True)
class Finding:
    """One conclusion about the stack.

    `blocker` stops `apply`: continuing would break something, lose something
    or claim something false. `warning` is true and the operator should know
    it, but not a reason to stop. `info` is what the plan will do or adopt.
    `action` is what a person has to do when the workflow may not.
    """

    level: str
    code: str
    component: str
    message: str
    action: str = ""

    def __post_init__(self) -> None:
        if self.level not in LEVELS:
            raise ValueError(f"finding level {self.level!r} is not one of {LEVELS}")

    def as_dict(self) -> dict:
        return asdict(self)


def blockers(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.level == "blocker"]
