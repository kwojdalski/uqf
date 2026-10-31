"""Shared bootstrapping/config logic for the uqf stack.
See cli/entry.py (the `uqs` CLI) and mcp.py (the `uqs-mcp` MCP server).
"""

import sys as _sys

_previous_hook = _sys.excepthook


def _schema_metadata_hook(kind, exc, tb):  # type: ignore[no-untyped-def]
    """Stale or missing plant schema metadata refuses, naming the command that
    regenerates it (#1781). Where no command catches it - a script, an import -
    print that alone, not a traceback around it."""
    from uqs.model.plant_metadata import SchemaMetadataError

    if isinstance(exc, SchemaMetadataError):
        _sys.stderr.write(f"uqs: {exc}\n")
        return
    _previous_hook(kind, exc, tb)


_sys.excepthook = _schema_metadata_hook
