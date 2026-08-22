#!/usr/bin/env python3
"""torq_demo.py - thin backward-compatible shim over
src/torq_orchestrator/cli.py's Typer app (see docs/torq-demo.md).

The CLI itself now lives in the installable package so it can also be
reached as the shorter `torq-demo` script entry point (pyproject.toml
[project.scripts]):

    uv run --project python/torq_orchestrator torq-demo start all

or, after a one-time `uv tool install --editable python/torq_orchestrator`,
just:

    torq-demo start all

This file still works exactly as before for anything that invokes it by
path:

    uv run --project python/torq_orchestrator python/torq_orchestrator/torq_demo.py start all
"""

from __future__ import annotations

from torq_orchestrator.cli import main

if __name__ == "__main__":
    main()
