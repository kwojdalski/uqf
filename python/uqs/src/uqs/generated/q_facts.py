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

#: the strategy a worker that declares none writes with - .qetl.io.default_strategy
DEFAULT_IO_STRATEGY: str = "upsert"

#: what a bounded run may do - .qetl.job.bounded.runtime.modes
RUN_MODES: tuple[str, ...] = (
    "validate",
    "plan",
    "dry_run",
    "run",
)

#: the run modes that open no source and write nothing - .qetl.job.bounded.runtime.read_only_modes
READ_ONLY_RUN_MODES: tuple[str, ...] = (
    "validate",
    "plan",
)

#: the levels .qetl.log writes, least to most severe - .qetl.log.levels
LOG_LEVELS: tuple[str, ...] = (
    "TRACE",
    "DEBUG",
    "INFO",
    "WARNING",
    "ERROR",
)

#: the log channels a record may carry in its id - .qetl.log.channels (#2274)
LOG_CHANNELS: tuple[str, ...] = (
    "lifecycle",
    "ipc",
    "source",
    "transform",
    "storage",
    "coverage",
    "eod",
    "config",
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

#: every key of a worker's status file, in order - .qetl.status.fields
STATUS_FIELDS: tuple[str, ...] = (
    "worker",
    "instance_id",
    "state",
    "source_version",
    "range_from",
    "range_to",
    "cursor",
    "rows_published",
    "windows_completed",
    "reactions_owed",
    "error",
    "updated_at",
    "pid",
    "host",
    "run_id",
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

#: dataset -> time column getdata reads it by - .qetl.job.bounded.read_time_column
BOUNDED_DATASET_TIME_COLUMNS: dict[str, str] = {
    "book_liquidity_1m": "time",
    "crypto_book_1s": "time",
    "crypto_markout": "time",
    "demo_deals": "time",
    "duckdb_deals": "time",
    "eq_markout": "time",
    "event_tape": "time",
    "exec_bar": "time",
    "executions": "time",
    "fx_markout": "time",
    "fx_markout_wide": "time",
    "imported_trades": "time",
    "order_quality": "time",
    "trades_copy": "time",
}

#: dataset -> its instrument column, empty if none - .qetl.job.bounded.read_instrument_column
BOUNDED_DATASET_INSTRUMENT_COLUMNS: dict[str, str] = {
    "book_liquidity_1m": "sym",
    "crypto_book_1s": "sym",
    "crypto_markout": "sym",
    "demo_deals": "sym",
    "duckdb_deals": "sym",
    "eq_markout": "sym",
    "event_tape": "sym",
    "exec_bar": "sym",
    "executions": "sym",
    "fx_markout": "sym",
    "fx_markout_wide": "sym",
    "imported_trades": "sym",
    "order_quality": "book",
    "trades_copy": "sym",
}

#: table -> the jobs that write it, reactions included - .qetl.dag.producers (#1862)
DAG_PRODUCERS: dict[str, tuple[str, ...]] = {
    "arbitrage": ("arbitrage",),
    "book_liquidity_1m": (
        "book_liquidity_1m",
        "hdb_book_liquidity_1m_backfill",
    ),
    "client_flow": ("kafka_flow",),
    "config_change": (
        "cross_arbitrage",
        "fx_positions",
        "superbook",
    ),
    "cross_arbitrage": ("cross_arbitrage",),
    "crypto_book": ("crypto_mock",),
    "crypto_book_1s": (
        "crypto_book_1s",
        "hdb_crypto_book_backfill",
    ),
    "crypto_markout": (
        "crypto_markout",
        "hdb_crypto_markout_backfill",
    ),
    "crypto_trades": ("crypto_mock",),
    "deal_positions": ("demo_deals~rebuild_positions",),
    "demo_deals": (
        "demo_deals_backfill",
        "demo_deals_feed",
    ),
    "duckdb_deals": ("duckdb_deals_backfill",),
    "eq_markout": (
        "eq_markout",
        "hdb_eq_markout_backfill",
    ),
    "event_tape": ("demo_events_backfill",),
    "exec_bar": (
        "exec_bars",
        "hdb_exec_bars_backfill",
    ),
    "executions": (
        "eq_fills_backfill",
        "executions",
    ),
    "fx_limit_breach": ("fx_positions",),
    "fx_markout": (
        "fx_markout",
        "hdb_fx_markouts_backfill",
    ),
    "fx_markout_wide": (
        "fx_markout_wide",
        "hdb_fx_markout_wide_backfill",
    ),
    "fx_orderbook": ("fx_orderbook_feed",),
    "fx_position": ("fx_positions",),
    "fx_position_open": ("fx_positions",),
    "imported_trades": ("upstream_trades_backfill",),
    "kafka_dead_letter": ("kafka_flow",),
    "kafka_message": ("kafka_feed",),
    "last_value": ("last_value",),
    "market_data": ("market_data",),
    "mkt_orderbook": ("vectorize",),
    "order_outcomes": ("order_outcomes",),
    "order_quality": (
        "hdb_order_quality_backfill",
        "order_quality",
    ),
    "orders": ("fx_orders_feed",),
    "position": ("posbook",),
    "position_open": ("posbook",),
    "quote": ("fx_feed",),
    "superbook": ("superbook",),
    "trades": ("fx_trades_feed",),
    "trades_copy": ("hdb_transfer_backfill",),
    "wide_orderbook": ("wide_orderbook_feed",),
}

#: source -> its transport, as its declaration resolved it (#1865)
SOURCE_TRANSPORTS: dict[str, str] = {
    "demo_deals": "ipc",
    "demo_events": "ipc",
    "duckdb_deals": "odbc",
    "eq_fills": "csv",
    "hdb_crypto_book": "ipc",
    "hdb_crypto_markouts": "ipc",
    "hdb_eq_markouts": "ipc",
    "hdb_executions": "ipc",
    "hdb_fx_markouts": "ipc",
    "hdb_market_data": "ipc",
    "hdb_orders": "ipc",
    "hdb_transfer": "local",
    "upstream_trades": "ipc",
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

#: the least a grant's max_rows may be - .qetl.io.remote.chunk_rows
REMOTE_CHUNK_ROWS: int = 50000

#: q's type characters (`meta`'s `t`) -> the names a cast takes (#1785)
Q_TYPE_NAMES: dict[str, str] = {
    "b": "boolean",
    "g": "guid",
    "x": "byte",
    "h": "short",
    "i": "int",
    "j": "long",
    "e": "real",
    "f": "float",
    "c": "char",
    "s": "symbol",
    "p": "timestamp",
    "m": "month",
    "d": "date",
    "z": "datetime",
    "n": "timespan",
    "u": "minute",
    "v": "second",
    "t": "time",
}

#: the names a q cast takes -> q's type characters (#1785)
Q_TYPE_CHARS: dict[str, str] = {
    "boolean": "b",
    "guid": "g",
    "byte": "x",
    "short": "h",
    "int": "i",
    "long": "j",
    "real": "e",
    "float": "f",
    "char": "c",
    "symbol": "s",
    "timestamp": "p",
    "month": "m",
    "date": "d",
    "datetime": "z",
    "timespan": "n",
    "minute": "u",
    "second": "v",
    "time": "t",
}

#: q's column attributes, as `attr` and `meta`'s `a` report them (#1785)
Q_ATTRIBUTES: tuple[str, ...] = (
    "s",
    "u",
    "p",
    "g",
)
