"""The `-.replay.*` switches tickerlogreplay.q reads, validated (see stack/replay.py)."""

from __future__ import annotations

import re
from pathlib import Path

from uqs.paths import UqsError

#: What a value may contain before torq.sh puts it on a start line. Paths are
#: what this passes, so `/` is in and a space is not: torq.sh builds the line
#: into a string and `eval`s it, so a space would split one path into two
#: words and a `;` or `$(...)` would run as shell. Same reasoning, and the
#: same refusal, as stack/backfill._SAFE_VALUE - which cannot simply be reused
#: because it predates any flag whose value is a path.
_SAFE_VALUE = re.compile(r"[A-Za-z0-9_./:+-]+")

#: torq.sh finds its own `-csv` and `-extras` flags by grepping every argument
#: for those words, so a value containing either is mistaken for the flag and
#: the whole command line is misparsed. A path is quite capable of holding
#: "csv" - `output/uqs/csv/...` would - so this is checked, not assumed.
_TORQ_SH_WORDS = ("csv", "extras")


def _checked(name: str, value: str) -> str:
    if not _SAFE_VALUE.fullmatch(value):
        raise UqsError(
            f"{name} {value!r} may contain only letters, digits and . _ / : + - "
            "(torq.sh runs the start line through a shell)"
        )
    if any(word in value for word in _TORQ_SH_WORDS):
        raise UqsError(
            f"{name} {value!r} contains 'csv' or 'extras', which torq.sh reads as its "
            "own flags wherever they appear"
        )
    return value


def replay_flags(
    log_dir: Path,
    hdb_dir: Path,
    schema_file: Path,
    tables: list[str] | None = None,
) -> list[str]:
    """The `-.replay.*` switches tickerlogreplay.q reads, validated.

    Plain paths, no leading `:`. The script hsyms `hdbdir` itself and hsyms
    `tplogdir` on the way into `getlogdir`, while `schemafile` is loaded with
    `system "l ",string ...` - which a leading colon would break.

    `tablelist` is left off entirely for a whole-log replay rather than passed
    as `all`: TorQ's default IS `` `all ``, and a switch that restates a
    default is one more thing that can disagree with it.
    """
    flags = [
        "-.replay.tplogdir",
        _checked("the log directory", str(log_dir)),
        "-.replay.hdbdir",
        _checked("the hdb directory", str(hdb_dir)),
        "-.replay.schemafile",
        _checked("the schema file", str(schema_file)),
    ]
    if tables:
        flags.append("-.replay.tablelist")
        flags.extend(_checked("a table name", table) for table in tables)
    return flags
