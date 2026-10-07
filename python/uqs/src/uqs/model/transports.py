"""How a backfill source is reached, read from the committed contract surface.

`.qetl.source.transport` in src/etl/core/source_contract.q is the registry:
one row per transport, with its operations and the words an operator and the
scaffold need. `scripts/generate/contract_surface.py export` writes the words
- never the operations - to docs/reference/surfaces/current/transports.csv,
and its `check` fails when q and that file disagree. So this module reads a
generated file rather than keeping a list of its own (#616), and needs no q
process to do it.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from uqs.paths import UqsError, repo_root

#: .qetl.source's transport registry, as the contract surface exports it.
TRANSPORTS_FILE = Path("docs/reference/surfaces/current/transports.csv")


@dataclass(frozen=True)
class Transport:
    """One transport, as the surface describes it."""

    name: str
    #: The transport a source declaring none gets.
    default: bool
    #: What the credential is, for an operator.
    expects: str
    #: A credential of that shape.
    example: str
    #: How a source's query reaches it - q comment lines for the scaffold.
    query_note: str


def load(root: Path | None = None) -> tuple[Transport, ...]:
    """Every transport, in the order q registers them."""
    path = (root or repo_root()) / TRANSPORTS_FILE
    if not path.is_file():
        raise UqsError(
            f"{TRANSPORTS_FILE} is missing - regenerate the contract surface with "
            "`uv run python scripts/generate/contract_surface.py export`"
        )
    with path.open(newline="", encoding="utf-8") as handle:
        return tuple(
            Transport(
                name=row["name"],
                default=row["default"] == "1",
                expects=row["expects"],
                example=row["example"],
                query_note=row["query_note"],
            )
            for row in csv.DictReader(handle)
        )


def names(root: Path | None = None) -> tuple[str, ...]:
    """Every transport's name."""
    return tuple(t.name for t in load(root))


def default(root: Path | None = None) -> str:
    """The name of the transport a source gets when it declares none."""
    (found,) = (t.name for t in load(root) if t.default)
    return found


def get(name: str, root: Path | None = None) -> Transport:
    """One transport, or an error naming the ones there are."""
    for t in load(root):
        if t.name == name:
            return t
    raise UqsError(f"--transport must be one of {', '.join(names(root))}, not {name!r}")
