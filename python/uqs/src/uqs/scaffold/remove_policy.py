"""The refusal `uqs job remove` may be told past (#1852), and its guardrail.

Its own module beside scaffold/remove.py, which enforces it and has reached
its line budget.
"""

from __future__ import annotations

from uqs.guardrails import Guardrail

#: The one refusal `uqs job remove` may be told past (#1852).
EVEN_IF_EDITED = "even-if-edited"
GUARDRAILS = (
    Guardrail(
        EVEN_IF_EDITED,
        ("job remove",),
        "remove a job that has been written - no SCAFFOLDED marker left - as if it were "
        "still a scaffold",
        risk="the job's own code, not just a scaffold, is deleted from the tree",
        action="commit or copy the job first: git has it, nothing else does",
    ),
)
