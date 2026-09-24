"""cli/entry.py - Typer CLI for the vendored uqf stack (see
docs/guides/uqs.md). Bridges lib/torq + lib/torq-finance-starter-pack
without editing either vendored tree.

All the actual bootstrapping/config logic lives in the model/, stack/ and
external/ modules, which uqs_mcp.py's FastMCP server calls too, so the
two front ends can't drift apart.

Reached through the `uqs` script entry point (pyproject.toml
[project.scripts]):

    uv run --project python/uqs uqs start all

or, after a one-time `uv tool install --editable python/uqs`:

    uqs start all

There was also a `uqs.py` shim next to the package, for invoking the CLI
by path before the entry point existed. Nothing executed it - every reference
was prose - so it was removed rather than maintained.
"""

from __future__ import annotations

# Imported for their side effect: each module registers its commands onto the
# shared `app`. That is what keeps `uqs start` spelled `uqs start`
# after the split - the alternative, a sub-Typer per module, would have made it
# `uqs lifecycle start` and broken every script and document that calls
# this CLI.
#
# The import ORDER is the order `--help` lists commands in, so isort is turned
# off for it: the fleet first, then the summary of it, then what reads it, then
# what writes code, then the external recorders. Alphabetical would open the
# help with `config-get`.
#
# isort: off
from uqs.cli import lifecycle  # noqa: F401
from uqs.cli import backfill  # noqa: F401
from uqs.cli import summary  # noqa: F401
from uqs.cli import inspect  # noqa: F401
from uqs.cli import config  # noqa: F401
from uqs.cli import create  # noqa: F401
from uqs.cli import external  # noqa: F401

# isort: on
from uqs.cli.shared import _env_log_level, app
from uqs.cli.zsh_completion import patch_zsh_completion_script
from uqs.logger import configure_logging


def main() -> None:
    """Entry point for the `uqs` script."""
    configure_logging(component="uqs", level=_env_log_level())
    # Before app(): --install-completion and --show-completion are handled
    # inside it, and both read the template this swaps out.
    patch_zsh_completion_script()
    app()


if __name__ == "__main__":
    main()
