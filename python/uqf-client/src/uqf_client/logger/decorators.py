"""Debug decorators for function call tracing."""

import functools
import inspect
import os
import threading
import time
from collections.abc import Callable
from typing import Any

from loguru import logger

from uqf_client.logger.core import is_level_enabled

# Thread-local storage for call depth tracking
_call_depth = threading.local()


def _get_call_depth() -> int:
    if not hasattr(_call_depth, "depth"):
        _call_depth.depth = 0
    return _call_depth.depth


def _set_call_depth(depth: int) -> None:
    _call_depth.depth = depth


def trace_calls(show_return: bool = False) -> Callable:
    """Decorator to trace function calls when LOG_LEVEL=DEBUG.

    Logs function entry with arguments, execution time, and optionally return
    values. Uses indentation to show call hierarchy.

    Usage:
        @trace_calls()
        def my_function(arg1, arg2):
            ...
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            env_log_level = os.getenv("LOG_LEVEL", "").upper()
            is_debug = is_level_enabled("DEBUG") or env_log_level == "DEBUG"

            if not is_debug:
                return func(*args, **kwargs)

            depth = _get_call_depth()
            indent = "  " * depth
            arrow_in = "→" if depth == 0 else "↳"
            arrow_out = "←"

            func_name = func.__name__
            module_name = func.__module__.split(".")[-1]

            args_repr = []
            try:
                param_names = list(inspect.signature(func).parameters.keys())
            except TypeError, ValueError:
                param_names = []
            for i, arg in enumerate(args):
                arg_str = _format_arg_value(arg)
                if i < len(param_names):
                    args_repr.append(f"{param_names[i]}={arg_str}")
                else:
                    args_repr.append(arg_str)

            for key, value in kwargs.items():
                args_repr.append(f"{key}={_format_arg_value(value)}")

            args_str = ", ".join(args_repr)

            logger.debug(
                "{}{} [TRACE] {}.{}({})", indent, arrow_in, module_name, func_name, args_str
            )

            _set_call_depth(depth + 1)
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                execution_time = time.time() - start_time

                if show_return:
                    logger.debug(
                        "{}{} [TRACE] {}.{} returned {} ({:.3f}s)",
                        indent,
                        arrow_out,
                        module_name,
                        func_name,
                        _format_arg_value(result),
                        execution_time,
                    )
                else:
                    logger.debug(
                        "{}{} [TRACE] {}.{} completed ({:.3f}s)",
                        indent,
                        arrow_out,
                        module_name,
                        func_name,
                        execution_time,
                    )

                return result

            except Exception as e:
                execution_time = time.time() - start_time
                logger.debug(
                    "{}{} [TRACE] {}.{} raised {}: {} ({:.3f}s)",
                    indent,
                    arrow_out,
                    module_name,
                    func_name,
                    type(e).__name__,
                    e,
                    execution_time,
                )
                raise

            finally:
                _set_call_depth(depth)

        return wrapper

    return decorator


def _format_arg_value(value: Any, max_length: int = 60) -> str:
    """Format argument value for logging (with truncation)."""
    try:
        if isinstance(value, str):
            if len(value) > max_length:
                return f"'{value[:max_length]}...'"
            return f"'{value}'"

        if isinstance(value, int | float | bool | type(None)):
            return str(value)

        if hasattr(value, "__class__"):
            class_name = value.__class__.__name__

            if class_name in ("Path", "PosixPath", "WindowsPath"):
                path_str = str(value)
                if len(path_str) > max_length:
                    return f"Path('...{path_str[-max_length:]}')"
                return f"Path('{path_str}')"

            if class_name in ("Tensor", "ndarray", "DataFrame"):
                shape = getattr(value, "shape", None)
                if shape:
                    return f"{class_name}(shape={shape})"
                return f"{class_name}(...)"

            return f"{class_name}(...)"

        value_str = str(value)
        if len(value_str) > max_length:
            return f"{value_str[:max_length]}..."
        return value_str

    except Exception:
        return "<value>"
