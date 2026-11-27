"""`python -m uqs`: the CLI from source, as a source-only release runs it
(deploy/control.py, #1642), without the installed `uqs` script - through the
same launcher, so `uqs cron run`'s not-due check comes first here too (#2987)."""

from uqs.launch import main

main()
