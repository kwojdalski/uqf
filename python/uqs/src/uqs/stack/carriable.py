"""What a process.csv field may hold, given how torq.sh reads and runs it.

Three separate hazards, refused separately so each message says which:

  uncarriable   torq.sh splits lines with `awk -F,`, so a comma starts a new
                field however csv quoted it, the quote stays literal, and a
                newline starts a row it miscounts (#777).
  shell-unsafe  torq.sh composes qcmd, extras, load, U, ... into one start
                line and runs `eval "nohup $sline ..."`, so `;`, `|`, `$(..)`
                and the like in an override run as whoever started the stack -
                over HTTP, whoever holds the write token (#1041). Refused by a
                whitelist, not a blacklist: the #1041 blacklist let `#` through,
                which comments out -procfile, the log redirect and the `&` so
                the process starts in the foreground and `uqs start` blocks,
                and `*` `?` `[` which glob against the working directory, and
                `~` `{` `}` `!` (#1252). Only SHELL_SAFE_CHARS and the space
                between words are allowed. Two placeholders are exempt: `${NAME}`,
                torq.sh's own, expanded by envsubst before the eval, which the
                vendored rows depend on; and `{NAME}`, the port column's, which
                bash leaves alone (brace expansion needs a comma or `..`).
  out of domain a value the field cannot mean: a port past 65535, a query
                timeout that is not a number, a startwithall other than 0/1 -
                torq.sh passes each through as given, so the process fails to
                start, listens nowhere or runs with no timeout (#2465). Empty
                is always allowed: it is process.csv's own "not set".
"""

from __future__ import annotations

import re
import string

from uqs.paths import UqsError

UNCARRIABLE = ',"\n\r'

#: The only characters an override's word may hold: ones eval passes to q as
#: text. Everything else - `;` `|` `$` `#` `*` `~` `{` ... - is refused.
SHELL_SAFE_CHARS = frozenset(string.ascii_letters + string.digits + "_./:=+@%-")

#: `${NAME}` (envsubst) and `{NAME}` (the port column's) - see the header.
_PLACEHOLDER = re.compile(r"\$?\{[A-Za-z_][A-Za-z0-9_]*\}")


def refuse_uncarriable(procname: str, field: str, value: str) -> None:
    if any(ch in value for ch in UNCARRIABLE):
        raise UqsError(
            f"{procname}.{field} value {value!r} contains a comma, quote or newline - "
            "torq.sh splits process.csv on commas with awk and cannot quote one; "
            "separate the parts with spaces instead (e.g. -pairs EURUSD USDJPY)"
        )


def refuse_shell_unsafe(procname: str, field: str, value: str) -> None:
    found = sorted(set(_PLACEHOLDER.sub("", value)) - SHELL_SAFE_CHARS - {" "})
    if found:
        raise UqsError(
            f"{procname}.{field} value {value!r} contains shell metacharacters "
            f"{''.join(found)!r} - torq.sh runs a process's start line through eval, "
            "so an override may hold only plain words of letters, digits and _./:=+@%- "
            "separated by spaces (a ${NAME} or {NAME} placeholder is allowed)"
        )


_WHOLE = re.compile(r"\d+")
#: A port: a number, or the column's own `{NAME}` placeholder plus an offset.
_PORT_EXPR = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}(\+\d+)?")

#: Each field with a domain: (whether a value is in it, what the domain is).
DOMAINS = {
    "port": (
        lambda v: bool(_PORT_EXPR.fullmatch(v)) or (v.isdecimal() and 1 <= int(v) <= 65535),
        "a TCP port 1-65535 or a {KDBBASEPORT}+N expression",
    ),
    "startwithall": (lambda v: v in ("0", "1"), "0 or 1"),
    **{
        field: (lambda v: bool(_WHOLE.fullmatch(v)), "a whole number")
        for field in ("T", "w", "g", "localtime")
    },
}


def refuse_out_of_domain(procname: str, field: str, value: str) -> None:
    if value and field in DOMAINS and not DOMAINS[field][0](value):
        raise UqsError(f"{procname}.{field} value {value!r} must be {DOMAINS[field][1]}")


def refuse_unsafe_override(procname: str, field: str, value: str) -> None:
    """Refuse an override torq.sh would misread or run, or one its field
    cannot hold, naming the field."""
    refuse_uncarriable(procname, field, value)
    refuse_shell_unsafe(procname, field, value)
    refuse_out_of_domain(procname, field, value)


def check_carriable(rows: list[dict[str, str]]) -> None:
    """Refuse a process.csv torq.sh would misread, naming the field.

    For an override already on disk, written before set_process_config
    refused one: found here, at bootstrap, it fails by name instead of as a
    start whose qcmd is the second half of `extras`.
    """
    for row in rows:
        for field, value in row.items():
            refuse_uncarriable(row["procname"], field, value or "")
