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

#: every state a worker's status file may record - .qetl.status.status_states
STATUS_STATES: tuple[str, ...] = (
    "starting",
    "running",
    "idle",
    "completed",
    "failed",
)

#: the states that end a run - .qetl.status.terminal_states
STATUS_TERMINAL_STATES: tuple[str, ...] = (
    "idle",
    "completed",
    "failed",
)

#: the states a worker's process exits 0 for - .qetl.job.bounded.exit_code
STATUS_SUCCESS_STATES: tuple[str, ...] = (
    "idle",
    "completed",
)

#: a status file's name before the instance id - .qetl.status.file_prefix
STATUS_FILE_PREFIX: str = "airflow_status_"

#: a status file's name after the instance id - .qetl.status.file_suffix
STATUS_FILE_SUFFIX: str = ".txt"

#: dataset -> time column getdata reads it by (#1363)
BOUNDED_DATASET_TIME_COLUMNS: dict[str, str] = {
    "book_liquidity_1m": "time",
    "crypto_book_1s": "time",
    "crypto_market_data": "source_time",
    "demo_deals": "deal_time",
    "demo_execution_quality": "time",
    "duckdb_deals": "deal_time",
    "eq_orderbook": "time",
    "event_tape": "time",
    "exec_bar": "time",
    "fx_markout": "time",
    "fx_markout_wide": "time",
    "imported_trades": "time",
    "order_quality": "time",
    "trades_copy": "time",
}

#: dataset -> its instrument column, empty if none (#1363)
BOUNDED_DATASET_INSTRUMENT_COLUMNS: dict[str, str] = {
    "book_liquidity_1m": "sym",
    "crypto_book_1s": "sym",
    "crypto_market_data": "sym",
    "demo_deals": "sym",
    "demo_execution_quality": "sym",
    "duckdb_deals": "sym",
    "eq_orderbook": "sym",
    "event_tape": "sym",
    "exec_bar": "sym",
    "fx_markout": "sym",
    "fx_markout_wide": "sym",
    "imported_trades": "sym",
    "order_quality": "",
    "trades_copy": "sym",
}

#: the worker-config layers, most significant first - .qetl.cfg.sources
CFG_SOURCES: tuple[str, ...] = (
    "env",
    "overrides",
    "yaml",
    "default",
)

#: the layer that outranks a worker-config override - .qetl.cfg.env_layer
CFG_ENV_LAYER: str = "env"

#: the layer a worker-config override sits in - .qetl.cfg.override_layer
CFG_OVERRIDE_LAYER: str = "overrides"

#: a worker-config key's environment variable prefix - .qetl.cfg.env_prefix
CFG_ENV_PREFIX: str = "UQF_"

#: what a source's credential variable starts with - .qetl.source.credential_prefix
SOURCE_CREDENTIAL_PREFIX: str = "UQF_SOURCE_CRED_"

#: what a fixture run's release ends with - .qetl.source.fixture_tag
FIXTURE_TAG: str = "~fixture"

#: what precedes a worker's output revision in a release - .qetl.job.bounded.runtime.revision_tag
REVISION_TAG: str = "@r"
