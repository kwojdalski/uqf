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
from uqs.paths import UqsError

#: A `port` value in uqs.portexpr's grammar, rather than a `${VAR}` template.
_BRACE_PLACEHOLDER = re.compile(r"^\{\w+\}(?:[+-]\d+)?$")


def _resolve_value(value: str, env: dict[str, str]) -> str:
    if _BRACE_PLACEHOLDER.match(value):
        port = portexpr.resolve(value, env)
        # unknown/non-numeric var - leave the placeholder as-is
        return value if port is None else str(port)
    return Template(value).safe_substitute(env)


def resolve_process_config(row: dict[str, str], env: dict[str, str]) -> dict[str, str]:
    """The row as torq.sh would start it. Refuses a port the base port pushes
    outside 1..65535: no process can listen there, and leaving the cell
    unresolved would read as "unknown" rather than as the operator's mistake
    (#2609)."""
    port = portexpr.evaluate(row.get("port"), env)
    if port is not None and not portexpr.in_range(port):
        raise UqsError(
            f"{row.get('procname', '?')} would listen on port {port} "
            f"({row['port']} with KDBBASEPORT={env.get('KDBBASEPORT', '?')}) - "
            f"every port must be 1..{portexpr.MAX_PORT}: choose a lower --base-port"
        )
    return {field: _resolve_value(value, env) for field, value in row.items()}
