"""Centralized loguru-based logging, ported from a sibling project's
generic `logger` package (see git history for provenance) with the
project-specific parts (component presets, env var names) stripped out -
what's left is the generic loguru wrapper plus a handful of formatting
helpers, useful anywhere in this repo's Python code.
"""

from torq_orchestrator.logger.core import (
    configure_logging,
    get_logger,
    is_level_enabled,
    setup_logging,
)
from torq_orchestrator.logger.decorators import trace_calls
from torq_orchestrator.logger.utils import (
    LogContext,
    log_banner,
    log_error_with_context,
    log_function_call,
    log_performance_metrics,
    log_processing_step,
    logged_function,
)

__all__ = [
    "LogContext",
    "configure_logging",
    "get_logger",
    "is_level_enabled",
    "log_banner",
    "log_error_with_context",
    "log_function_call",
    "log_performance_metrics",
    "log_processing_step",
    "logged_function",
    "setup_logging",
    "trace_calls",
]
