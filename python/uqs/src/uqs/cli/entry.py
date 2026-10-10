"""cli/entry.py - Typer CLI for the vendored uqf stack (see
docs/guides/uqs.md). Bridges lib/torq + lib/torq-finance-starter-pack
without editing either vendored tree.

All the actual bootstrapping/config logic lives in the model/, stack/ and
external/ modules, which uqs.mcp's FastMCP server calls too, so the
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
# off for it: the fleet first, the bounded jobs that run over data it has
# already written, then the summary of it, then what reads it, then what writes
# code, then the external recorders, then deploying a release. Alphabetical would open the help with
# `backfill`. The groups (`data`, `config`, `job`, `feed`) list after every flat
# command, in the order their modules register them.
#
# isort: off
from uqs.cli import lifecycle  # noqa: F401
from uqs.cli import backfill  # noqa: F401
from uqs.cli import cleanup  # noqa: F401
from uqs.cli import runs  # noqa: F401
from uqs.cli import gaps  # noqa: F401
from uqs.cli import stream  # noqa: F401
from uqs.cli import replay  # noqa: F401
from uqs.cli import seed  # noqa: F401
from uqs.cli import migrate  # noqa: F401
from uqs.cli import summary  # noqa: F401
from uqs.cli import graph  # noqa: F401
from uqs.cli import query  # noqa: F401
from uqs.cli import inspect  # noqa: F401
from uqs.cli import config  # noqa: F401
from uqs.cli import runtime_diff  # noqa: F401
from uqs.cli import runtime_prepare  # noqa: F401
from uqs.cli import sources  # noqa: F401
from uqs.cli import source_check  # noqa: F401
from uqs.cli import create  # noqa: F401
from uqs.cli import remove  # noqa: F401
from uqs.cli import install  # noqa: F401
from uqs.cli import external  # noqa: F401
from uqs.cli import odbc  # noqa: F401
from uqs.cli import deploy  # noqa: F401
from uqs.cli import deploy_server  # noqa: F401
from uqs.cli import target  # noqa: F401

# isort: on
import sys

from uqs.cli.shared import _env_log_level, app
from uqs.cli.zsh_completion import patch_zsh_completion_script
from uqs.deploy import control
from uqs.logger import configure_logging, get_logger
from uqs.paths import UqsError
from uqs.stack.envfiles import load_repo_env_files

log = get_logger(__name__)


def main() -> None:
    """Entry point for the `uqs` script."""
    configure_logging(component="uqs", level=_env_log_level())
    # Before any command, so every process a command starts inherits them -
    # see uqs.stack.envfiles. Logging is configured twice because the files may
    # set LOG_LEVEL, and a warning about the files needs a logger to land in.
    load_repo_env_files()
    configure_logging(component="uqs", level=_env_log_level())
    # Before app(): --install-completion and --show-completion are handled
    # inside it, and both read the template this swaps out.
    patch_zsh_completion_script()
    # `uqs --target NAME <command>` runs the command on a deployed release
    # (#956). Here, before Typer parses anything, because what follows is the
    # SERVER's command line: the server's uqs parses it, and a command this
    # checkout no longer has, or has changed, still means what it means there.
    name, yes, rest = control.split_global(sys.argv[1:])
    if name is not None:
        interactive = sys.stdin.isatty() and sys.stdout.isatty()
        try:
            sys.exit(target.forward(name, yes, rest, interactive=interactive))
        except UqsError as exc:
            log.error("{}", exc)
            sys.exit(1)
    app()


if __name__ == "__main__":
    main()
