"""The `uqf-stack` command line, one module per command family.

Every module here registers its commands onto the single shared `app` in
`shared.py` rather than onto a sub-Typer, because `uqf-stack start` must stay
spelled that way - a sub-Typer would have made it `uqf-stack lifecycle start`
and broken every script and document that calls this CLI. `main.py` imports
the families in the order `--help` should list them in.

`app` and `main` are re-exported here because they are this package's public
surface - what a caller wants from the CLI is the assembled app, not the module
that happens to assemble it. Importing either pulls in every family, so a
caller cannot get hold of a half-populated `app`.

`entry.py`, not `main.py`, deliberately: re-exporting a function called `main`
out of a module called `main` makes `cli.main` mean the function in one import
order and the module in another, and a test that patched the wrong one of those
would fail with an `AttributeError` several layers from the cause.
"""

from torq_orchestrator.cli.entry import app, main

__all__ = ["app", "main"]
