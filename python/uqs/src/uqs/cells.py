"""A listed row's values: typed for `--json`, rendered once for a table (#1882).

A row a list command produces (`uqs process list`, `uqs runtime list`, ...)
holds what each field IS - a port is an int, a flag a bool, the tables a
process reads a list - so `--json` prints that, and `jq '.processes[].port'`
reads a number rather than the text a table cell showed. `cell` is the one
place a value becomes that text, for the table, `--sort` and `--export`.

`Shown` is the value whose text is not its rendering: the inputs of a process
that chooses them when it starts are none yet (`null`), and the table says
"(chosen at start)" rather than leaving an empty cell that would claim it
reads nothing.

A leaf: it imports nothing from `uqs`, so the stack's listings can use it
without importing the CLI.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Shown:
    """`value` for a document, `text` for a table cell."""

    value: Any
    text: str

    def __str__(self) -> str:
        return self.text


def cell(value: Any) -> str:
    """`value` as the text a table cell shows."""
    if value is None:
        return ""
    if isinstance(value, Shown):
        return value.text
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, Mapping):
        return ", ".join(f"{k}={cell(v)}" for k, v in value.items())
    if isinstance(value, list | tuple | set | frozenset):
        return ", ".join(cell(v) for v in value)
    return str(value)


def cells(row: Mapping[str, Any]) -> dict[str, str]:
    """`row` with every value as its cell's text."""
    return {k: cell(v) for k, v in row.items()}


def values(row: Mapping[str, Any]) -> dict[str, Any]:
    """`row` with every `Shown` replaced by its value: what a document carries."""
    return {k: v.value if isinstance(v, Shown) else v for k, v in row.items()}
