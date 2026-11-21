"""`--json` on a reporting command: one JSON document on stdout (#1731).

Every command that reports something takes `--json` (docs/reference/cli.md, "CLI
conventions") and prints the data its table shows as ONE document: an
object whose lists are records with snake_case keys, so `jq '.runs[].status'`
reads what the `status` column shows. The log already goes to stderr under
`--json` (uqs.logger.json_requested), and a refusal is the document
`{"ok": false, "error": ...}` (shared.json_refusal) - so stdout parses whatever
happens. docs/reference/cli.md, "Machine-readable output", states the contract.

One contract for every document (#1882): it is an object, never a bare list -
a list command prints `{<plural>: [...]}`; its values are typed (a port is a
number, a flag a bool, a set of names a list), taken from the row rather than
from the text its table cell shows (uqs.cells); a process is `process`
whatever the row calls it; and a time is ISO-8601 with an explicit offset
(uqs.timefmt.timestamp), whichever of q's, TorQ's or Python's spellings it arrived in.

The contract holds at every depth, not only on a list's records (#2122):
`emit` spells each key of every nested object as KEYS says, and refuses a
document whose `ok` is anything but a bool, whose range is an object rather
than `range_from`/`range_to`, or whose exit code is `exit` rather than
`exit_code` (#2228). What a document carries as data - a query's
result, a table's rows - is wrapped in `Verbatim` and printed as it came.
"""

from __future__ import annotations

import dataclasses
import json
import math
import re
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Annotated, Any

import typer

from uqs.cells import Shown
from uqs.timefmt import timestamp

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
#: attached destination says `stack_id`. A log line's level is `level`, the
#: name of the `--level` option that filters it on every command (#2241),
#: where TorQ's log line calls it `loglevel`.
KEYS = {
    "procname": "process",
    "startwithall": "start_with_all",
    "worker": "job",
    "stack_id": "base_port",
    "loglevel": "level",
}

#: The keys a log line is a record of in every document that prints log lines,
#: `logs` and `connector logs` (#2241): when, how severe, and what it said. A
#: command adds its own keys beside them; `activity list`'s events say when and
#: how severe under the same two, `time` and `level`.
LINE_KEYS = ("time", "level", "message")


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


#: The keys a ledger row or a range carries a UTC time under, q's `.z.p` spelt
#: with no zone.
UTC_KEYS = ("range_from", "range_to", "started_at", "ended_at")


def utc_times(record: Mapping[str, Any], keys: tuple[str, ...] = UTC_KEYS) -> dict[str, Any]:
    """`record` with each of `keys` it has as ISO-8601 in UTC (#1882)."""
    return {k: timestamp(v, naive="utc") if k in keys else v for k, v in record.items()}


#: An infinity as a document spells it, by its sign: q's 0w/0W/0Wp/0Wn, kept
#: apart from a null - the Desk's spelling (uqf_frontend routes/common.py).
INFINITY = {1: "inf", -1: "-inf"}

#: kola's 0Wp and -0Wp, as nanoseconds since 1970: 0Wp is int64's largest, and
#: -0Wp is -0Wp's q value moved by the 30 years between q's epoch and Unix's.
_STAMP_INFINITY = {2**63 - 1: 1, -(2**63 - 1) + 946_684_800 * 10**9: -1}
#: kola's 0Wn, in nanoseconds; -0Wn is its negation.
_SPAN_INFINITY = 2**63 - 1


def plain(value: Any, *, keyed: bool = True) -> Any:
    """`value` as what json.dumps takes: a table becomes records, a time its ISO text,
    a NaN null, an infinity INFINITY's text, and anything else unknown its str.
    Each object's keys are spelled as KEYS says unless `keyed` is False or the
    value is `Verbatim`."""
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, Verbatim):
        return plain(value.value, keyed=False)
    if isinstance(value, Shown):
        return plain(value.value, keyed=keyed)
    if isinstance(value, float):
        if math.isinf(value):
            return INFINITY[1 if value > 0 else -1]
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
        return plain(_frame_rows(value), keyed=keyed)
    if hasattr(value, "to_frame"):  # a polars Series: kola's vector
        return [row[value.name] for row in plain(_frame_rows(value.to_frame()), keyed=keyed)]
    if hasattr(value, "item"):  # a numpy scalar
        return plain(value.item(), keyed=keyed)
    return str(value)


def _frame_rows(frame: Any) -> list[dict[str, Any]]:
    """A kola frame's rows, with what `to_dicts` would lose kept (#2874).

    A timestamp goes as UTC text to the nanosecond: as a Python datetime it
    stopped at the microsecond, so rows 100 ns apart printed one time. A
    timespan goes as a whole number of nanoseconds, where a timedelta showed
    one below a microsecond as 0:00:00. 0Wp/-0Wp and 0Wn/-0Wn go as INFINITY
    rather than as the dates in 2262 and 1677 their bits spell. A float
    infinity is left to `plain`.
    """
    import polars as pl

    special: dict[str, list[str | None]] = {}
    columns = []
    for name, kind in frame.schema.items():
        if isinstance(kind, pl.Datetime):
            column = pl.col(name)
            if kind.time_zone is not None:
                column = column.dt.convert_time_zone("UTC").dt.replace_time_zone(None)
            column = column.dt.cast_time_unit("ns")
            nanos = frame.select(column.cast(pl.Int64)).to_series().to_list()
            special[name] = [_infinity(_STAMP_INFINITY.get(n)) for n in nanos]
            columns.append(column.dt.to_string("%Y-%m-%dT%H:%M:%S.%9f+00:00").alias(name))
        elif isinstance(kind, pl.Duration):
            nanos = frame.select(pl.col(name).dt.total_nanoseconds()).to_series().to_list()
            special[name] = [
                _infinity(None if n is None or abs(n) != _SPAN_INFINITY else (n > 0) - (n < 0))
                for n in nanos
            ]
            columns.append(pl.col(name).dt.total_nanoseconds())
    rows = frame.with_columns(columns).to_dicts() if columns else frame.to_dicts()
    for name, values in special.items():
        for row, value in zip(rows, values, strict=True):
            if value is not None:
                row[name] = value
    return rows


def _infinity(sign: int | None) -> str | None:
    return None if sign is None else INFINITY[sign]


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
    if "exit" in out:
        raise ValueError("an exit code in a --json document is `exit_code`, not `exit` (#2228)")
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
