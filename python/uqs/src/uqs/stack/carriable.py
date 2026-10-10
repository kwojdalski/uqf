"""What a process.csv field may hold, given how torq.sh reads and runs it.

Two separate hazards, refused separately so each message says which:

  uncarriable   torq.sh splits lines with `awk -F,`, so a comma starts a new
                field however csv quoted it, the quote stays literal, and a
                newline starts a row it miscounts (#777).
  shell-unsafe  torq.sh composes qcmd, extras, load, U, ... into one start
                line and runs `eval "nohup $sline ..."`, so `;`, `|`, `$(..)`
                and the like in an override run as whoever started the stack -
                over HTTP, whoever holds the write token (#1041). `${NAME}` is
                exempt: it is torq.sh's own placeholder, expanded by envsubst
                before the eval, and the vendored rows depend on it.
"""

from __future__ import annotations

import re

from uqs.paths import UqsError

UNCARRIABLE = ',"\n\r'

#: Characters eval would act on rather than pass to q as text.
SHELL_UNSAFE = ";&|$`<>()\\'\"\n\r"

_PLACEHOLDER = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}")


def refuse_uncarriable(procname: str, field: str, value: str) -> None:
    if any(ch in value for ch in UNCARRIABLE):
        raise UqsError(
            f"{procname}.{field} value {value!r} contains a comma, quote or newline - "
            "torq.sh splits process.csv on commas with awk and cannot quote one; "
            "separate the parts with spaces instead (e.g. -pairs EURUSD USDJPY)"
        )


def refuse_shell_unsafe(procname: str, field: str, value: str) -> None:
    found = sorted({ch for ch in _PLACEHOLDER.sub("", value) if ch in SHELL_UNSAFE})
    if found:
        raise UqsError(
            f"{procname}.{field} value {value!r} contains shell metacharacters "
            f"{''.join(found)!r} - torq.sh runs a process's start line through eval, "
            "so an override may hold only plain words (a ${NAME} placeholder is allowed)"
        )


def refuse_unsafe_override(procname: str, field: str, value: str) -> None:
    """Refuse an override torq.sh would misread or run, naming the field."""
    refuse_uncarriable(procname, field, value)
    refuse_shell_unsafe(procname, field, value)


def check_carriable(rows: list[dict[str, str]]) -> None:
    """Refuse a process.csv torq.sh would misread, naming the field.

    For an override already on disk, written before set_process_config
    refused one: found here, at bootstrap, it fails by name instead of as a
    start whose qcmd is the second half of `extras`.
    """
    for row in rows:
        for field, value in row.items():
            refuse_uncarriable(row["procname"], field, value or "")
