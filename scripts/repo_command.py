"""The plumbing a repository command shares with the next one (#1806, #1804).

Not a command itself: a module that `scripts/gates/check.py` loads by path
(scripts/ is not a package), and that `scripts/generate/derive.py` can load
the same way. It holds only what both genuinely do alike:

  - the exit statuses: 0 when the target passed, 1 when it found a problem or
    could not run, 2 for a usage error (argparse's own);
  - loading a target's module from its file, only when that target is named;
  - running a target so that a crash is a failure with a message, never an
    empty result read as success.

Everything else - what a target computes, what it prints, what it takes as
options - belongs to the command that registers it.
"""

from __future__ import annotations

import importlib.util
import sys
import traceback
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

#: The target passed.
OK = 0
#: The target found a problem, or could not run at all.
FAILED = 1
#: The command line was wrong. argparse exits with this itself.
USAGE = 2


def load(path: Path, name: str) -> ModuleType:
    """The module in `path`, loaded from its file and registered as `name`.

    Registered so that a test that loads or monkeypatches the same file under
    the same name shares one module object with the command; a module already
    registered from this file is returned as it is.
    """
    path = path.resolve()
    loaded = sys.modules.get(name)
    if loaded is not None and Path(getattr(loaded, "__file__", "") or "").resolve() == path:
        return loaded
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[name]
        raise
    return module


def guarded(label: str, run: Callable[[], int]) -> int:
    """`run()`'s exit status, or FAILED with a message if it could not finish.

    An exception is reported as an error naming `label`, with the traceback,
    so a target that crashes half way can never look like one that found
    nothing. A `SystemExit` carrying a message (`raise SystemExit("why")`) is
    the same failure, said more briefly; one carrying a status keeps it.
    """
    try:
        status = run()
    except SystemExit as exc:
        if exc.code is None or isinstance(exc.code, int):
            return exc.code or OK
        print(f"error: {label} could not run: {exc.code}", file=sys.stderr)
        return FAILED
    except Exception as exc:  # noqa: BLE001 - any crash is the target failing
        traceback.print_exc()
        print(f"error: {label} could not run: {type(exc).__name__}: {exc}", file=sys.stderr)
        return FAILED
    if not isinstance(status, int) or isinstance(status, bool):
        print(f"error: {label} returned {status!r}, not an exit status", file=sys.stderr)
        return FAILED
    return status
