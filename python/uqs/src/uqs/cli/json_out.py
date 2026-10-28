"""`--json` on a reporting command: one JSON document on stdout (#1731).

Every command that reports something takes `--json` (docs/guides/uqs.md, "CLI
conventions", rule 5) and prints the data its table shows as ONE document: an
object whose lists are records with snake_case keys, so `jq '.runs[].status'`
reads what the `status` column shows. The log already goes to stderr under
`--json` (uqs.logger.json_requested), and a refusal is the document
`{"ok": false, "error": ...}` (shared.json_refusal) - so stdout parses whatever
happens. docs/guides/uqs.md, "Machine-readable output", lists each document.
"""

from __future__ import annotations

import dataclasses
import json
import math
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Annotated, Any

import typer


def json_opt(shape: str) -> Any:
    """The `--json` option, its help naming the document it prints."""
    return Annotated[
        bool, typer.Option("--json", help=f"Print one JSON document on stdout instead: {shape}")
    ]


def snake(key: object) -> str:
    """A column title as a JSON key: `PortSource` -> port_source, `Depends on` -> depends_on."""
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(key).strip())
    return re.sub(r"[^0-9A-Za-z]+", "_", text).strip("_").lower()


def plain(value: Any) -> Any:
    """`value` as what json.dumps takes: a table becomes records, a time its ISO text,
    a NaN null, and anything else unknown its str."""
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [plain(v) for v in value]
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, timedelta | Path):
        return str(value)
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return plain(dataclasses.asdict(value))
    if hasattr(value, "to_dicts"):  # a polars DataFrame: kola's table
        return plain(value.to_dicts())
    if hasattr(value, "to_list"):  # a polars Series: kola's vector
        return plain(value.to_list())
    if hasattr(value, "item"):  # a numpy scalar
        return plain(value.item())
    return str(value)


def records(rows: Any) -> list[dict[str, Any]]:
    """Rows as records: each key snake_case, each value JSON's."""
    return [{snake(k): plain(v) for k, v in dict(row).items()} for row in rows]


def emit(document: Any) -> None:
    """Print `document`, the whole of stdout for this command."""
    print(json.dumps(plain(document), indent=2))
