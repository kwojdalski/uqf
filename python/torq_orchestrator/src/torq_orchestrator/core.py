"""Compatibility facade over the torq_orchestrator modules.

This file was 1578 lines covering seven unrelated concerns - table schemas,
the pipeline registry, filesystem paths, process.csv composition, listing,
the torq.sh runtime, log tailing and the cryptorust recorders. Each is now
its own module:

    schemas.py    the q table definitions published into the tickerplant
    pipelines.py  the Pipeline registry, port allocation, edge verification
    paths.py      where everything lives, and whether it is runnable
    env.py        the env bridge torq.sh and process.csv resolve against
    procs.py      process.csv composition and per-process overrides
    listing.py    generic listing of processes, fields, overrides, env
    runtime.py    bootstrap, driving torq.sh, query, export
    logs.py       reading TorQ's log files through the Python logger
    crypto.py     the two external cryptorust recorders

Everything is re-exported here because `from torq_orchestrator import core`
followed by `core.thing` is how cli.py, wizard.py, torq_demo_mcp.py and
test_core.py all reach this code - about seventy distinct names between
them. Keeping the facade meant the split changed no call site, which is
what makes the existing test suite a proof that it preserved behaviour
rather than just a hope.

New code should import from the specific module; this exists so the split
did not have to be a rewrite of everything that depends on it.
"""

from __future__ import annotations

# Re-exported so `core.shutil` keeps resolving: test_core patches
# `core.shutil.which`, which is the stdlib module object and therefore a
# global patch - it works through the facade exactly as it did before.
import shutil  # noqa: F401

from torq_orchestrator.crypto import (  # noqa: F401
    CRYPTO_FILLS_RECORDER_DEFAULT_POLL_MS,
    CRYPTO_FILLS_RECORDER_DEFAULT_SYMBOL,
    CRYPTO_FILLS_RECORDER_TABLE,
    CRYPTO_REAL_FILLS_RECORDER_TABLE,
    CRYPTO_RECORDER_DEFAULT_SYMBOLS,
    CRYPTO_RECORDER_DEFAULT_VENUES,
    CRYPTORUST_ROOT_ENV,
    DEFAULT_OMS_SOCKET_PATH,
    _crypto_recorder_config_yaml,
    crypto_fills_recorder_status,
    crypto_recorder_status,
    cryptorust_root,
    is_crypto_recorder_running,
    start_crypto_fills_recorder,
    start_crypto_recorder,
    stop_crypto_fills_recorder,
    stop_crypto_recorder,
)
from torq_orchestrator.env import (  # noqa: F401
    build_env,
)
from torq_orchestrator.listing import (
    LISTABLE_KINDS,
    SUMMARY_COLUMNS,
    configured_ports,
    list_items,
    summary_rows,  # noqa: F401
)
from torq_orchestrator.logs import (  # noqa: F401
    follow_logs,
    get_recent_logs,
    parse_log_line,
    print_recent_logs,
    resolve_procnames,
)
from torq_orchestrator.paths import (  # noqa: F401
    TorqDemoError,
    TorqDemoPaths,
    check_prerequisites,
    clean,
    default_paths,
)
from torq_orchestrator.pipelines import (  # noqa: F401
    CROSS_ETL_PORT_OFFSET,
    DEFAULT_BASE_PORT,
    FX_TRADES_FEED_PORT_OFFSET,
    FXFEED_PORT_OFFSET,
    MARKOUT_PORT_OFFSET,
    PIPELINE_BY_NAME,
    PIPELINE_LIB_SCRIPT,
    PIPELINE_OFFSETS,
    PIPELINES,
    POSBOOK_PORT_OFFSET,
    PROCESS_CSV_FIELDS,
    QUOTES_FEED_PORT_OFFSET,
    TAP_PORT_OFFSET,
    VECTORIZE_ETL_PORT_OFFSET,
    WIDE_BOOK_FEED_PORT_OFFSET,
    Pipeline,
    verify_pipeline_edges,
)
from torq_orchestrator.procs import (  # noqa: F401
    _base_process_rows,
    _generated_schema_content,
    add_extra_process,
    add_extra_table_schema,
    get_process_config,
    list_process_names,
    next_free_port_offset,
    resolve_process_config,
    set_process_config,
)
from torq_orchestrator.runtime import (  # noqa: F401
    bootstrap,
    export_table,
    print_procs,
    query,
    restart,
    run_torq_sh,
    start,
    stop,
    summary,
)
from torq_orchestrator.schemas import (  # noqa: F401
    CRYPTO_BOOK_TABLE_SCHEMA,
    CRYPTO_SIM_FILLS_TABLE_SCHEMA,
    CRYPTO_TRADES_TABLE_SCHEMA,
    EXECUTION_QUALITY_TABLE_SCHEMA,
    MKT_ORDERBOOK_TABLE_SCHEMA,
    POSITION_TABLE_SCHEMA,
    QUOTES_TABLE_SCHEMA,
    TRADES_TABLE_SCHEMA,
    WIDE_BOOK_LEVELS,
    WIDE_BOOK_TABLE_SCHEMA,
)

__all__ = [
    "CROSS_ETL_PORT_OFFSET",
    "CRYPTORUST_ROOT_ENV",
    "CRYPTO_BOOK_TABLE_SCHEMA",
    "CRYPTO_FILLS_RECORDER_DEFAULT_POLL_MS",
    "CRYPTO_FILLS_RECORDER_DEFAULT_SYMBOL",
    "CRYPTO_FILLS_RECORDER_TABLE",
    "CRYPTO_REAL_FILLS_RECORDER_TABLE",
    "CRYPTO_RECORDER_DEFAULT_SYMBOLS",
    "CRYPTO_RECORDER_DEFAULT_VENUES",
    "CRYPTO_SIM_FILLS_TABLE_SCHEMA",
    "CRYPTO_TRADES_TABLE_SCHEMA",
    "DEFAULT_BASE_PORT",
    "DEFAULT_OMS_SOCKET_PATH",
    "EXECUTION_QUALITY_TABLE_SCHEMA",
    "FXFEED_PORT_OFFSET",
    "FX_TRADES_FEED_PORT_OFFSET",
    "LISTABLE_KINDS",
    "MARKOUT_PORT_OFFSET",
    "MKT_ORDERBOOK_TABLE_SCHEMA",
    "PIPELINES",
    "PIPELINE_BY_NAME",
    "PIPELINE_LIB_SCRIPT",
    "PIPELINE_OFFSETS",
    "POSBOOK_PORT_OFFSET",
    "POSITION_TABLE_SCHEMA",
    "PROCESS_CSV_FIELDS",
    "Pipeline",
    "QUOTES_FEED_PORT_OFFSET",
    "QUOTES_TABLE_SCHEMA",
    "TAP_PORT_OFFSET",
    "TRADES_TABLE_SCHEMA",
    "TorqDemoError",
    "TorqDemoPaths",
    "VECTORIZE_ETL_PORT_OFFSET",
    "WIDE_BOOK_FEED_PORT_OFFSET",
    "WIDE_BOOK_LEVELS",
    "WIDE_BOOK_TABLE_SCHEMA",
    "_base_process_rows",
    "_crypto_recorder_config_yaml",
    "_generated_schema_content",
    "add_extra_process",
    "add_extra_table_schema",
    "bootstrap",
    "build_env",
    "check_prerequisites",
    "clean",
    "crypto_fills_recorder_status",
    "crypto_recorder_status",
    "cryptorust_root",
    "default_paths",
    "export_table",
    "follow_logs",
    "get_process_config",
    "get_recent_logs",
    "is_crypto_recorder_running",
    "list_items",
    "SUMMARY_COLUMNS",
    "configured_ports",
    "summary_rows",
    "list_process_names",
    "next_free_port_offset",
    "parse_log_line",
    "print_procs",
    "print_recent_logs",
    "query",
    "resolve_process_config",
    "resolve_procnames",
    "restart",
    "run_torq_sh",
    "set_process_config",
    "start",
    "start_crypto_fills_recorder",
    "start_crypto_recorder",
    "stop",
    "stop_crypto_fills_recorder",
    "stop_crypto_recorder",
    "summary",
    "verify_pipeline_edges",
]
