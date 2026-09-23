"""cli/entry.py - Typer CLI for the vendored uqf stack (see
docs/guides/uqf-stack.md). Bridges lib/torq + lib/torq-finance-starter-pack
without editing either vendored tree.

All the actual bootstrapping/config logic lives in core.py, shared with
uqf_stack_mcp.py's FastMCP server so the two front ends can't drift apart.

Reached through the `uqf-stack` script entry point (pyproject.toml
[project.scripts]):

    uv run --project python/torq_orchestrator uqf-stack start all

or, after a one-time `uv tool install --editable python/torq_orchestrator`:

    uqf-stack start all

There was also a `uqf_stack.py` shim next to the package, for invoking the CLI
by path before the entry point existed. Nothing executed it - every reference
was prose - so it was removed rather than maintained.
"""

from __future__ import annotations

# Imported for their side effect: each module registers its commands onto the
# shared `app`. That is what keeps `uqf-stack start` spelled `uqf-stack start`
# after the split - the alternative, a sub-Typer per module, would have made it
# `uqf-stack lifecycle start` and broken every script and document that calls
# this CLI.
#
# The import ORDER is the order `--help` lists commands in, so isort is turned
# off for it: the fleet first, then the summary of it, then what reads it, then
# what writes code, then the external recorders. Alphabetical would open the
# help with `config-get`.
#
# isort: off
from torq_orchestrator.cli import lifecycle  # noqa: F401
from torq_orchestrator.cli import summary  # noqa: F401
from torq_orchestrator.cli import inspect  # noqa: F401
from torq_orchestrator.cli import create  # noqa: F401
from torq_orchestrator.cli import external  # noqa: F401

# isort: on
from torq_orchestrator.cli.shared import _env_log_level, app, configure_logging


def main() -> None:
    """Entry point for the `uqf-stack` script."""
    configure_logging(component="uqf_stack", level=_env_log_level())
    app()


if __name__ == "__main__":
    main()
