"""Compatibility facade over the uqf_stack modules.

This file was 1578 lines covering seven unrelated concerns - table schemas,
the pipeline registry, filesystem paths, process.csv composition, listing,
the torq.sh runtime, log tailing and the cryptorust recorders. Each is now
its own module:

    model/schemas.py    the q table definitions published into the tickerplant
    model/pipelines.py  the Pipeline registry, port allocation, edge verification
    paths.py      where everything lives, and whether it is runnable
    stack/env.py        the env bridge torq.sh and process.csv resolve against
    stack/procs.py      process.csv composition and per-process overrides
    stack/listing.py    generic listing of processes, fields, overrides, env
    stack/runtime.py    bootstrap, driving torq.sh, query, export
    stack/logs.py       reading TorQ's log files through the Python logger
    external/crypto.py     the two external cryptorust recorders

Everything is re-exported here because `from uqf_stack import core`
followed by `core.thing` is how cli/entry.py, scaffold/wizard.py, uqf_stack_mcp.py and
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

from uqf_stack.checks.schema_view import (  # noqa: F401
    DEFAULT_PROC as DEFAULT_SCHEMA_PROC,
)
from uqf_stack.checks.schema_view import (  # noqa: F401
    columns as schema_columns,
)
from uqf_stack.checks.schema_view import (  # noqa: F401
    match_tables,
    resolve_port,
)
from uqf_stack.checks.schema_view import (  # noqa: F401
    overview as schema_overview,
)
from uqf_stack.checks.schema_view import (  # noqa: F401
    table_names as schema_table_names,
)
from uqf_stack.external.crypto import (  # noqa: F401
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
from uqf_stack.model.pipeline import (  # noqa: F401
    FROM_DECLARATION,
)
from uqf_stack.model.pipeline_edges import (  # noqa: F401  (re-exported)
    LICENCE_CONNECTION_LIMIT,
)
from uqf_stack.model.pipelines import (  # noqa: F401
    DEFAULT_BASE_PORT,
    PIPELINE_BY_NAME,
    PIPELINE_LIB_SCRIPT,
    PIPELINE_OFFSETS,
    PIPELINES,
    PROCESS_CSV_FIELDS,
    STREAM_RUNNER_SCRIPT,
    Pipeline,
    verify_pipeline_edges,
)
from uqf_stack.paths import (  # noqa: F401
    UqfStackError,
    UqfStackPaths,
    check_prerequisites,
    clean,
    default_paths,
)
from uqf_stack.stack.env import (  # noqa: F401
    build_env,
)
from uqf_stack.stack.listing import (
    LISTABLE_KINDS,
    MONITOR_PROCNAME,  # noqa: F401  (re-exported for the CLI's heartbeat message)
    SUMMARY_ALL_COLUMNS,  # noqa: F401
    SUMMARY_COLUMNS,
    SUMMARY_GRAPH_COLUMNS,  # noqa: F401
    configured_ports,
    heartbeat_states,
    list_items,
    summary_rows,  # noqa: F401
)
from uqf_stack.stack.logs import (  # noqa: F401
    follow_logs,
    get_recent_logs,
    parse_log_line,
    print_recent_logs,
    resolve_procnames,
)
from uqf_stack.stack.procs import (  # noqa: F401
    VENDORED_STARTWITHALL_OVERLAY,
    _base_process_rows,
    _generated_schema_content,
    add_extra_process,
    add_extra_table_schema,
    get_process_config,
    list_process_choices,
    list_process_names,
    next_free_port_offset,
    resolve_process_config,
    set_process_config,
)
from uqf_stack.stack.runtime import (  # noqa: F401
    bootstrap,
    export_table,
    fill_hdb_partitions,
    print_procs,
    query,
    restart,
    run_torq_sh,
    start,
    stop,
    summary,
)

__all__ = [
    "CRYPTORUST_ROOT_ENV",
    "CRYPTO_FILLS_RECORDER_DEFAULT_POLL_MS",
    "CRYPTO_FILLS_RECORDER_DEFAULT_SYMBOL",
    "CRYPTO_FILLS_RECORDER_TABLE",
    "CRYPTO_REAL_FILLS_RECORDER_TABLE",
    "CRYPTO_RECORDER_DEFAULT_SYMBOLS",
    "CRYPTO_RECORDER_DEFAULT_VENUES",
    "DEFAULT_BASE_PORT",
    "DEFAULT_OMS_SOCKET_PATH",
    "FROM_DECLARATION",
    "LISTABLE_KINDS",
    "LICENCE_CONNECTION_LIMIT",
    "MONITOR_PROCNAME",
    "LICENCE_CONNECTION_LIMIT",
    "PIPELINES",
    "PIPELINE_BY_NAME",
    "PIPELINE_LIB_SCRIPT",
    "PIPELINE_OFFSETS",
    "PROCESS_CSV_FIELDS",
    "Pipeline",
    "UqfStackError",
    "UqfStackPaths",
    "VENDORED_STARTWITHALL_OVERLAY",
    "DEFAULT_SCHEMA_PROC",
    "schema_columns",
    "schema_overview",
    "resolve_port",
    "match_tables",
    "schema_table_names",
    "_base_process_rows",
    "_crypto_recorder_config_yaml",
    "_generated_schema_content",
    "add_extra_process",
    "add_extra_table_schema",
    "bootstrap",
    "fill_hdb_partitions",
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
    "list_process_choices",
    "SUMMARY_COLUMNS",
    "configured_ports",
    "heartbeat_states",
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
