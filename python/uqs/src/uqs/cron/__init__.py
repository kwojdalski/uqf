"""`uqs cron`: uqs commands run on a schedule from the user's own crontab (#2987).

An entry is a standard five-field cron expression, a time zone (UTC when none is
given) and `run`, the words one would type after `uqs`. Nothing else: no new
configuration language, and scheduling stays outside q, as worker_runtime.q
assigns it. The entries are per machine (entries.py), installed into the
crontab inside one managed block (crontab.py), and each crontab line calls
`uqs cron run ENTRY` (runs.py), which checks the entry is due in its own zone
before anything slow loads (guard.py). schedule.py is the cron matcher.

The command line is cli/cron/cron.py; this package knows nothing of Typer, so
what it needs from the CLI tree - the parsed `run` - is handed to it.
"""
