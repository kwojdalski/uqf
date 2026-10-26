"""`python -m uqs`: the CLI from source, as a source-only release runs it
(deploy/control.py, #1642), without the installed `uqs` script."""

from uqs.cli import main

main()
