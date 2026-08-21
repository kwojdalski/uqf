"""Utility functions for common logging tasks."""

import functools
import time
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

from loguru import logger as _loguru_logger

from torq_orchestrator.logger.core import is_level_enabled

_BANNER_WIDTH = 100


def log_banner(logger: Any, message: str) -> None:
    """Log a separator banner at INFO level."""
    inner = f"  {message}  "
    dashes = max(0, _BANNER_WIDTH - len(inner))
    left = dashes // 2
    right = dashes - left
    line = "=" * left + inner + "=" * right
    logger.info("{}", line)


def log_processing_step(
    logger: Any,
    step: str,
    details: str | None = None,
    extra_data: dict[str, Any] | None = None,
) -> None:
    """Log a processing step with consistent formatting."""
    message = f"Processing step: {step}"
    if details:
        message += f" - {details}"
    if extra_data:
        message += f" | {extra_data}"
    logger.info("{}", message)


def log_error_with_context(
    logger: Any,
    error: Exception,
    context: str,
    extra_data: dict[str, Any] | None = None,
) -> None:
    """Log an error with additional context information."""
    error_msg = f"Error in {context}: {type(error).__name__}: {error!s}"
    if extra_data:
        error_msg += f" | {extra_data}"
    logger.error("{}", error_msg)
    logger.opt(exception=True).debug("error details context={}", context)


def log_function_call(
    logger: Any,
    func_name: str,
    args: tuple | None = None,
    kwargs: dict[str, Any] | None = None,
    level: str = "DEBUG",
) -> None:
    """Log function call details."""
    if not is_level_enabled(level.upper()):
        return

    call_info = f"Calling function: {func_name}"

    if args:
        args_str = str(args)
        if len(args_str) > 200:
            args_str = args_str[:200] + "..."
        call_info += f" with args: {args_str}"

    if kwargs:
        kwargs_str = str(kwargs)
        if len(kwargs_str) > 200:
            kwargs_str = kwargs_str[:200] + "..."
        call_info += f" with kwargs: {kwargs_str}"

    logger.log(level.upper(), "{}", call_info)


def log_performance_metrics(
    logger: Any,
    operation: str,
    duration: float,
    extra_metrics: dict[str, int | float | str] | None = None,
) -> None:
    """Log performance metrics for operations."""
    perf_msg = f"Performance - {operation}: {duration:.3f}s"
    if extra_metrics:
        perf_msg += f" | Metrics: {extra_metrics}"
    logger.info("{}", perf_msg)


@contextmanager
def LogContext(
    logger: Any,
    operation: str,
    log_start: bool = True,
    log_end: bool = True,
    log_performance: bool = True,
    level: str = "INFO",
):
    """Context manager for logging operation start/end and performance."""
    start_time = time.time()

    if log_start and is_level_enabled(level.upper()):
        logger.log(level.upper(), "start operation={}", operation)

    try:
        yield
    except Exception as e:
        duration = time.time() - start_time
        log_error_with_context(
            logger, e, operation, {"duration_seconds": duration, "operation": operation}
        )
        raise
    finally:
        duration = time.time() - start_time

        if log_end and is_level_enabled(level.upper()):
            logger.log(level.upper(), "complete operation={}", operation)

        if log_performance:
            log_performance_metrics(logger, operation, duration)


def logged_function(
    logger: Any | None = None,
    level: str = "DEBUG",
    log_args: bool = False,
    log_result: bool = False,
    log_performance: bool = True,
) -> Callable:
    """Decorator to automatically log function calls and performance."""

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            func_logger = logger or _loguru_logger

            if log_args:
                log_function_call(func_logger, func.__name__, args, kwargs, level)
            else:
                log_function_call(func_logger, func.__name__, level=level)

            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                duration = time.time() - start_time

                if log_performance:
                    log_performance_metrics(func_logger, f"Function {func.__name__}", duration)

                if log_result:
                    result_str = str(result)
                    if len(result_str) > 100:
                        result_str = result_str[:100] + "..."
                    func_logger.debug("Function {} returned: {}", func.__name__, result_str)

                return result

            except Exception as e:
                duration = time.time() - start_time
                log_error_with_context(
                    func_logger,
                    e,
                    f"Function {func.__name__}",
                    {"duration_seconds": duration, "function": func.__name__},
                )
                raise

        return wrapper

    return decorator
