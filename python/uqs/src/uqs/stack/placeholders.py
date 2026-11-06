"""Resolving a process.csv row's placeholders to the values torq.sh would
start the process with.

process.csv has two placeholder styles: `${VAR}` / `$VAR` (shell parameter
expansion, which torq.sh resolves with envsubst at runtime - handled here by
string.Template, which uses the same syntax) inside e.g. `load`/`U`/`extras`;
and the `port` grammar (`{VAR}`, `{VAR}+N`, `{VAR}-N`, a literal), which
uqs.portexpr owns and the occupancy check and the frontend share.
"""

from __future__ import annotations

import re
from string import Template

from uqs import portexpr

#: A `port` value in uqs.portexpr's grammar, rather than a `${VAR}` template.
_BRACE_PLACEHOLDER = re.compile(r"^\{\w+\}(?:[+-]\d+)?$")


def _resolve_value(value: str, env: dict[str, str]) -> str:
    if _BRACE_PLACEHOLDER.match(value):
        port = portexpr.resolve(value, env)
        # unknown/non-numeric var - leave the placeholder as-is
        return value if port is None else str(port)
    return Template(value).safe_substitute(env)


def resolve_process_config(row: dict[str, str], env: dict[str, str]) -> dict[str, str]:
    return {field: _resolve_value(value, env) for field, value in row.items()}
