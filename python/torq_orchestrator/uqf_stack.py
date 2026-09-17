#!/usr/bin/env python3
"""uqf_stack.py - thin backward-compatible shim over
src/torq_orchestrator/cli.py's Typer app (see docs/guides/uqf-stack.md).

The CLI itself now lives in the installable package so it can also be
reached as the shorter `uqf-stack` script entry point (pyproject.toml
[project.scripts]):

    uv run --project python/torq_orchestrator uqf-stack start all

or, after a one-time `uv tool install --editable python/torq_orchestrator`,
just:

    uqf-stack start all

This file still works exactly as before for anything that invokes it by
path:

    uv run --project python/torq_orchestrator python/torq_orchestrator/uqf_stack.py start all
"""

from __future__ import annotations

from torq_orchestrator.cli import main

if __name__ == "__main__":
    main()
