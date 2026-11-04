"""What the frontend knows about the stack, read from the stack's own files.

Seven settings were environment variables of their own - the gateway's host
and port, the base port, process.csv, the status directory, the fan-out list
and web/dist - each a copy of a fact the stack already holds, free to
disagree with it (#1536). Now UQF_FRONTEND_STACK_ROOT names the stack and each
is read from where uqs keeps it:

- the layout: ``uqs.paths.paths_for_root``, so UQS_RUNTIME, UQS_DATA_ROOT
  and TORQHOME move the frontend's view exactly as they move the stack;
- the base port: KDBBASEPORT in the ``setenv.sh`` bootstrap writes beside
  process.csv - what torq.sh started the fleet with, ``uqs start --base-port`` included -
  else, before the first bootstrap, the runtime's declared base port;
- process.csv: the generated one (``UqsPaths.generated_procs``);
- the gateway: its row of that process.csv, host and port, else localhost
  at the base port + GATEWAY_PORT_OFFSET;
- the fan-out list: every process.csv row ``torq.sh start all`` starts;
- web/dist: ``<root>/web/dist``, when it has been built;
- the status directory: ``status.status_directory``, read per request.

They are read PER REQUEST (``current``), not once at startup (#2027). Every
start, stop and restart rewrites process.csv and setenv.sh, so a copy taken
at startup went stale: a frontend started before the first bootstrap never
had a fleet to fan out to, and after `uqs restart --base-port 7050` a Stop
ran torq.sh at the old base port, found nothing, and reported success.
``current`` re-derives whenever either file changes, and costs a stat of each
otherwise.

Only ``uqs.paths`` and the leaf ``uqs.activity_log`` are imported (paths loads
``uqs.runtimes`` and the logger, not ``uqs.stack``), lazily and only when a
stack root is set: the read path still loads nothing of the orchestrator.
"""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from uqf_frontend import procfile

#: What ``{KDBBASEPORT}`` resolves to when the stack is started with defaults.
DEFAULT_BASE_PORT = 6050

#: The gateway's offset from the base port, as TorQ's generated process.csv
#: declares it: ``{KDBBASEPORT}+7,gateway,gateway1``. Used only before that
#: file exists; once it does, its gateway row is read instead.
#:
#: The default port used to be 6050+2 - **rdb1** - and every routed query and
#: ops view errored while ``/health``, which only opened a handle, reported
#: ``up``. Connected to the wrong process is worse than not connected.
GATEWAY_PORT_OFFSET = 7

DEFAULT_HOST = "localhost"

#: The line bootstrap writes into setenv.sh (stack/runtime.py), as torq.sh sources it.
_BASE_PORT_LINE = re.compile(r'^export KDBBASEPORT="?(\d+)"?\s*$', re.MULTILINE)


@dataclass(frozen=True)
class Process:
    """One TorQ process this layer can reach directly.

    Needed because ``.usage.usage`` is per-process with **no fleet-wide
    rollup**, so a single query log across the stack has to be
    fanned out and merged here.
    """

    name: str
    host: str
    port: int


@dataclass(frozen=True)
class StackFacts:
    """The seven facts, as one value: derived together, so they agree."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_BASE_PORT + GATEWAY_PORT_OFFSET
    base_port: int = DEFAULT_BASE_PORT
    process_csv: Path | None = None
    processes: tuple[Process, ...] = ()
    web_dist: Path | None = None
    #: Where activity history is kept (activity.py): beside the workers'
    #: status files under $TORQDATA, so a stack's history lives with its data.
    activity_db: Path | None = None


def derive(root: Path | None) -> StackFacts:
    """Every derived fact for the stack at *root*; the defaults with none.

    A root that is not this repository is refused, at startup, rather than
    read: a wrong path would otherwise point every view at nothing and say so
    only one view at a time.
    """
    if root is None:
        return StackFacts()
    from uqs import activity_log
    from uqs import paths as stack_paths

    if not stack_paths.is_repo_root(root):
        raise ValueError(
            f"UQF_FRONTEND_STACK_ROOT={root} does not look like the repository: "
            f"it holds no {stack_paths.root_markers_text()}"
        )
    paths = stack_paths.paths_for_root(root)
    base_port = generated_base_port(paths.generated_setenv)
    if base_port is None:
        base_port = paths.runtime_declaration.base_port
    process_csv = paths.generated_procs
    declared = procfile.read(process_csv, base_port) if process_csv.is_file() else []
    gateway = next((p for p in declared if p.proctype == "gateway" and p.port is not None), None)
    web_dist = root / "web" / "dist"
    return StackFacts(
        host=gateway.host if gateway else DEFAULT_HOST,
        port=gateway.port if gateway and gateway.port else base_port + GATEWAY_PORT_OFFSET,
        base_port=base_port,
        process_csv=process_csv,
        processes=tuple(
            Process(p.procname, p.host, p.port)
            for p in declared
            if p.start_with_all and p.port is not None
        ),
        web_dist=web_dist if web_dist.is_dir() else None,
        activity_db=activity_log.history_file(paths.torqdata),
    )


#: root -> (the stat signature the facts were derived at, the facts).
_CURRENT: dict[Path, tuple[tuple[object, ...], StackFacts]] = {}
_CURRENT_LOCK = threading.Lock()


def current(root: Path) -> StackFacts:
    """``derive(root)``, as of the stack's files now: derived again whenever
    the generated process.csv or setenv.sh changed since the last call - a
    rewrite in place or a replace - and otherwise the facts already read."""
    from uqs import paths as stack_paths

    paths = stack_paths.paths_for_root(root)
    key = (
        _signature(paths.generated_procs),
        _signature(paths.generated_setenv),
        (root / "web" / "dist").is_dir(),
    )
    with _CURRENT_LOCK:
        cached = _CURRENT.get(root)
    if cached is not None and cached[0] == key:
        return cached[1]
    facts = derive(root)
    with _CURRENT_LOCK:
        _CURRENT[root] = (key, facts)
    return facts


def _signature(path: Path) -> tuple[int, int, int, int] | None:
    """What changes when *path* is rewritten or replaced; None while it is absent."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None
    return (st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def generated_base_port(setenv: Path) -> int | None:
    """KDBBASEPORT as the last bootstrap wrote it, or None before one has."""
    if not setenv.is_file():
        return None
    match = _BASE_PORT_LINE.search(setenv.read_text())
    return int(match.group(1)) if match else None


def generated_setting(setenv: Path, name: str) -> str | None:
    """Variable *name* as the last bootstrap wrote it into setenv.sh - one
    `export NAME="value"` line each - or None before one has, or without it."""
    if not setenv.is_file():
        return None
    line = re.compile(rf'^export {re.escape(name)}="?(.*?)"?\s*$', re.MULTILINE)
    match = line.search(setenv.read_text())
    return match.group(1) if match else None
