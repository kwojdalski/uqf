"""q facts uqs reads, generated from the q tree - DO NOT EDIT.

Written by scripts/generate/q_facts.py, which loads src/ under KDB-X and
reads each value (#818). A hook runs it with --check, so a change to any of
these in q fails the commit until this file is regenerated.
"""

#: what a write may do with a row whose key exists - .qetl.io.strategies
IO_STRATEGIES: tuple[str, ...] = (
    "upsert",
    "replace",
    "ignore",
    "append",
    "fail",
)

#: what a bounded run may do - .qetl.job.bounded.runtime.modes
RUN_MODES: tuple[str, ...] = (
    "validate",
    "plan",
    "dry_run",
    "run",
)

#: the levels .qetl.log writes, least to most severe - .qetl.log.levels
LOG_LEVELS: tuple[str, ...] = (
    "TRACE",
    "DEBUG",
    "INFO",
    "WARNING",
    "ERROR",
)

#: the run ledger's columns - cols .qetl.run.init_runs[]
ETL_RUNS_COLUMNS: tuple[str, ...] = (
    "run_id",
    "worker",
    "process",
    "host",
    "pid",
    "started_at",
    "ended_at",
    "status",
    "dataset",
    "source_version",
    "range_from",
    "range_to",
    "width",
    "windows_planned",
    "windows_completed",
    "windows_failed",
    "rows_published",
)

#: the run facts ledger's columns - cols .qetl.run.init_meta[]
ETL_RUN_META_COLUMNS: tuple[str, ...] = (
    "run_id",
    "dataset",
    "range_from",
    "range_to",
    "label",
    "text",
    "recorded_at",
)
