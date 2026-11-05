"""`--json` on a reporting command: one JSON document on stdout (#1731).

Every command that reports something takes `--json` (docs/guides/uqs.md, "CLI
conventions", rule 5) and prints the data its table shows as ONE document: an
object whose lists are records with snake_case keys, so `jq '.runs[].status'`
reads what the `status` column shows. The log already goes to stderr under
`--json` (uqs.logger.json_requested), and a refusal is the document
`{"ok": false, "error": ...}` (shared.json_refusal) - so stdout parses whatever
happens. docs/guides/uqs.md, "Machine-readable output", lists each document.

One contract for every document (#1882): it is an object, never a bare list -
a list command prints `{<plural>: [...]}`; its values are typed (a port is a
number, a flag a bool, a set of names a list), taken from the row rather than
from the text its table cell shows (uqs.cells); a process is `process`
whatever the row calls it; and a time is ISO-8601 with an explicit offset
(`timestamp`), whichever of q's, TorQ's or Python's spellings it arrived in.

The contract holds at every depth, not only on a list's records (#2122):
`emit` spells each key of every nested object as KEYS says, and refuses a
document whose `ok` is anything but a bool or whose range is an object rather
than `range_from`/`range_to`. What a document carries as data - a query's
result, a table's rows - is wrapped in `Verbatim` and printed as it came.
"""

from __future__ import annotations

import dataclasses
import json
import math
import re
from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

import typer

from uqs.cells import Shown

#: How every `--json` help begins (#1859): one phrasing, on every command.
JSON_HELP = "Print one JSON document on stdout instead"


def json_opt(shape: str | None = None, note: str | None = None) -> Any:
    """The `--json` option: one help on every command (#1859), naming the
    document's `shape` when given, then any `note` on when it applies.
    Every command prints one document; none prints a line per record (#1882)."""
    text = JSON_HELP + (f": {shape}" if shape else "")
    return Annotated[bool, typer.Option("--json", help=text + (f". {note}" if note else ""))]


#: A key one concept is spelled under in some rows -> the one spelling every
#: document uses, at any depth (#2122). process.csv calls a process `procname`
#: and its start-all flag `startwithall`; a document says `process`, as
#: `status`, `graph` and `config show` always did (#1882), and `start_with_all`,
#: as the CLI flag does. A job is `job`, the argument's name on every command
#: that takes one (the run ledger and a bounded job's state say `worker`), and
#: a stack's base port is `base_port`, `--base-port`'s name (#1855), where an
#: attached destination says `stack_id`.
KEYS = {
    "procname": "process",
    "startwithall": "start_with_all",
    "worker": "job",
    "stack_id": "base_port",
}


@dataclasses.dataclass(frozen=True)
class Verbatim:
    """A value a document carries as data - a q table's rows, a dictionary
    keyed by table names - whose keys are the data's own: `emit` neither
    renames them nor holds them to the contract."""

    value: Any


def snake(key: object) -> str:
    """A column title as a JSON key: `PortSource` -> port_source, `Depends on` -> depends_on,
    `procname` -> process."""
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(key).strip())
    word = re.sub(r"[^0-9A-Za-z]+", "_", text).strip("_").lower()
    return KEYS.get(word, word)


#: A date and a time of day as q, TorQ, Python and ISO-8601 spell them:
#: 2026.08.22D14:21:10.644413000, 2026-08-22T14:21:10Z, 2026-08-22 14:21:10+02:00.
_STAMP = re.compile(
    r"(\d{4})[.-](\d{2})[.-](\d{2})[DT ](\d{2}):(\d{2}):(\d{2})(?:[.,](\d+))?"
    r"(Z|[+-]\d{2}:?\d{2})?"
)
#: A time of day alone - TorQ's summary stamps a row with one.
_CLOCK = re.compile(r"(\d{2}):(\d{2}):(\d{2})")


def _offset(moment: datetime) -> str:
    """`moment`'s UTC offset as ISO-8601 spells it: +02:00."""
    text = moment.strftime("%z")
    return f"{text[:3]}:{text[3:]}"


def timestamp(value: Any, naive: Literal["local", "utc"] = "local") -> str | None:
    """`value` - a datetime, or a time in q's, TorQ's or ISO's text - as ISO-8601
    with an explicit offset: 2026-08-22T14:21:10.644413000+02:00.

    A value that names no zone is in `naive`'s: this machine's (a q process's
    `.z.P`, a log line, TorQ's clock) or UTC (`.z.p`, a ledger's bounds). A
    fraction of a second keeps every digit it had: a nanosecond bound
    truncated to a datetime's microsecond would be a different bound. A time
    of day alone is today's. Text that is not a time comes back unchanged, and
    None stays None."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        moment = value if value.tzinfo else _naive(value, naive)
        return moment.isoformat()
    text = str(value).strip()
    if m := _CLOCK.fullmatch(text):
        today = datetime.now().astimezone() if naive == "local" else datetime.now(UTC)
        moment = today.replace(hour=int(m[1]), minute=int(m[2]), second=int(m[3]), microsecond=0)
        return moment.isoformat()
    m = _STAMP.fullmatch(text)
    if m is None:
        return text
    year, month, day, hh, mm, ss, fraction, zone = m.groups()
    if zone is None:
        wall = datetime(int(year), int(month), int(day), int(hh), int(mm), int(ss))
        zone = _offset(_naive(wall, naive))
    elif zone == "Z":
        zone = "+00:00"
    elif ":" not in zone:
        zone = f"{zone[:3]}:{zone[3:]}"
    fraction = f".{fraction}" if fraction else ""
    return f"{year}-{month}-{day}T{hh}:{mm}:{ss}{fraction}{zone}"


#: The keys a ledger row or a range carries a UTC time under, q's `.z.p` spelt
#: with no zone.
UTC_KEYS = ("range_from", "range_to", "started_at", "ended_at")


def utc_times(record: Mapping[str, Any], keys: tuple[str, ...] = UTC_KEYS) -> dict[str, Any]:
    """`record` with each of `keys` it has as ISO-8601 in UTC (#1882)."""
    return {k: timestamp(v, naive="utc") if k in keys else v for k, v in record.items()}


def _naive(moment: datetime, naive: Literal["local", "utc"]) -> datetime:
    return moment.astimezone() if naive == "local" else moment.replace(tzinfo=UTC)


def plain(value: Any, *, keyed: bool = True) -> Any:
    """`value` as what json.dumps takes: a table becomes records, a time its ISO text,
    a NaN null, and anything else unknown its str. Each object's keys are
    spelled as KEYS says unless `keyed` is False or the value is `Verbatim`."""
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, Verbatim):
        return plain(value.value, keyed=False)
    if isinstance(value, Shown):
        return plain(value.value, keyed=keyed)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return _keyed(value) if keyed else {str(k): plain(v, keyed=False) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [plain(v, keyed=keyed) for v in value]
    if isinstance(value, datetime):
        # naive, it came from q - kola's timestamp, which this tree takes as .z.p
        return timestamp(value, naive="utc")
    if isinstance(value, date | time):
        return value.isoformat()
    if isinstance(value, timedelta | Path):
        return str(value)
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return plain(dataclasses.asdict(value), keyed=keyed)
    if hasattr(value, "to_dicts"):  # a polars DataFrame: kola's table
        return plain(value.to_dicts(), keyed=keyed)
    if hasattr(value, "to_list"):  # a polars Series: kola's vector
        return plain(value.to_list(), keyed=keyed)
    if hasattr(value, "item"):  # a numpy scalar
        return plain(value.item(), keyed=keyed)
    return str(value)


def _keyed(obj: Mapping[Any, Any]) -> dict[str, Any]:
    """`obj` held to the contract: each key as KEYS spells it, `ok` a bool,
    and a range its two keys. A breach is this tree's bug, refused rather than
    printed: a script reading `ok` or `range_from` would read it wrong."""
    out: dict[str, Any] = {}
    spelt: dict[str, str] = {}
    for key, value in obj.items():
        name = KEYS.get(str(key), str(key))
        if name in spelt:
            both = f"{spelt[name]!r} and {key!r}"
            raise ValueError(f"a --json object has both {both}: one is the other")
        spelt[name] = str(key)
        out[name] = plain(value)
    if "ok" in out and not isinstance(out["ok"], bool):
        raise ValueError(
            f"`ok` is a --json document's success flag, a bool, not {out['ok']!r}: "
            "say what happened under another key (`outcome`)"
        )
    if isinstance(out.get("range"), dict):
        raise ValueError("a range in a --json document is `range_from`/`range_to`, not `range`")
    return out


def records(rows: Any) -> list[dict[str, Any]]:
    """Rows as records: each key snake_case, each value JSON's."""
    return [_keyed({snake(k): v for k, v in dict(row).items()}) for row in rows]


def emit(document: Mapping[str, Any]) -> None:
    """Print `document`, the whole of stdout for this command: always an object."""
    if not isinstance(document, Mapping):
        raise TypeError(f"a --json document is an object, not {type(document).__name__}")
    print(json.dumps(plain(document), indent=2))


def emit_line(document: Mapping[str, Any]) -> None:
    """`document` on one line, for a program that reads it beside a marker
    line rather than as the whole of stdout: `deploy check`'s result, which a
    push of any version parses (deploy/selection.py). Under --json, `emit`."""
    if not isinstance(document, Mapping):
        raise TypeError(f"a document is an object, not {type(document).__name__}")
    print(json.dumps(plain(document)))
