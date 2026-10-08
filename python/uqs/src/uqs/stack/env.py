"""The env bridge: the variables torq.sh and process.csv placeholders resolve
against.

Its own module because three layers need it - procs (to resolve a
`${VAR}`/`{VAR}+N` placeholder in a row), listing (to show the resolved
values) and runtime (to hand the environment to torq.sh) - while it depends
on nothing but the paths and the port block. Left in runtime it made
procs -> runtime -> procs a cycle.

Pure: no filesystem writes. bootstrap() calls it and then writes; everything
else calls it and only reads. A runtime on PeachQ also needs its binary, which
comes from scripts/peachq.py's resolver - its cache, or a build into that cache
when cold - so that is interpreter_env, which only what starts q calls:
bootstrap (for torq.sh and setenv.sh) and the HDB filler.
"""

from __future__ import annotations

import functools
import importlib.util
import os
from pathlib import Path

from uqs.interpreter import PEACHQ, Q_IMPL_ENV, Q_INTERPRETER_ENV, q_command
from uqs.logger import get_logger
from uqs.paths import UqsError, UqsPaths
from uqs.runtimes import RUNTIME_ENV
from uqs.stack import qtree

log = get_logger(__name__)


def build_env(paths: UqsPaths, base_port: int | None = None) -> dict[str, str]:
    """The env vars torq.sh (and process.csv's ${VAR}/{VAR}+N placeholders)
    resolve against - pure, no filesystem writes. bootstrap() calls this and
    also writes it out as setenv.sh; get_process_config() calls this to
    resolve a row's placeholders without needing to bootstrap first.

    The torq runtime leaves out KDBSERVCONFIG and KDBSERVCODE: the service
    layer is this tree's, and without it TorQ loads its own config and the
    starter pack's, as shipped.
    """
    # A flattened runtime's processes load the converted copy (stack/qtree.py)
    # wherever they would have loaded this tree's q: the code roots and both
    # service layers, which are q too. Named here whether or not it is built
    # yet - bootstrap builds it before anything starts.
    code = qtree.code_root(paths)
    scripts = code / "scripts" if qtree.is_flattened(paths) else paths.scripts_dir
    env = {
        "TORQHOME": str(paths.torqhome),
        "TORQAPPHOME": str(paths.torqapphome),
        "TORQDATA": str(paths.torqdata),
        "UQF_SCRIPTS": str(scripts),
        "UQF_ROOT": str(code),
        "KDBCONFIG": str(paths.torqhome / "config"),
        "KDBCODE": str(paths.torqhome / "code"),
        "KDBAPPCONFIG": str(paths.torqapphome / "appconfig"),
        # One file: settings/default.q, which every TorQ process loads and
        # which names TorQ's log levels the way .qetl.log does (see it).
        "KDBSERVCONFIG": str(scripts / "torqconfig"),
        # handlers/loadpassword.q: TorQ's credential loader drops the base
        # passwords/ files once KDBSERVCONFIG adds a third config layer, so
        # this replaces it, loaded straight after trackservers.q (see it).
        "KDBSERVCODE": str(scripts / "torqcode"),
        "KDBAPPCODE": str(paths.torqapphome / "code"),
        "KDBLIB": str(paths.torqhome / "lib"),
        "KDBTESTS": str(paths.torqhome / "tests"),
        "KDBLOG": str(paths.log_dir),
        "KDBHDB": str(paths.hdb_dir),
        "KDBWDB": str(paths.torqdata / "wdbhdb"),
        "KDBTPLOG": str(paths.torqdata / "tplogs"),
        "KDBDQCDB": str(paths.torqdata / "dqe" / "dqcdb" / "database"),
        "KDBDQEDB": str(paths.torqdata / "dqe" / "dqedb" / "database"),
        "KDBBASEPORT": str(paths.runtime_declaration.resolve_base_port(base_port)),
        "TORQPROCESSES": str(paths.generated_procs),
        "RLWRAP": "rlwrap",
        "QCON": "qcon",
        # $QCMD, else `q` - the one interpreter rule, which is TorQ's own.
        # Hardcoding `q` here overrode an operator's QCMD, so the stack ran
        # whatever PATH found while its HDB filler ran another binary (#414).
        "QCMD": q_command(),
    }
    if not paths.runtime_declaration.overlays:
        del env["KDBSERVCONFIG"], env["KDBSERVCODE"]
    # Child processes - torq.sh, the frontend's and the MCP server's calls
    # back into uqs - see the same runtime this one was built for.
    env[RUNTIME_ENV] = paths.runtime
    return env


def interpreter_env(paths: UqsPaths) -> dict[str, str]:
    """QCMD and UQF_Q_IMPL for a runtime that declares PeachQ (#764), or {}.

    For that runtime's stack only: what torq.sh, the HDB filler and `summary`
    see for it, never the operator's shell, and nothing goes on PATH. A
    KDB-X runtime gets {}: the operator's own QCMD stands, as it always has.
    """
    if paths.runtime_declaration.interpreter != PEACHQ:
        return {}
    return {Q_IMPL_ENV: PEACHQ, Q_INTERPRETER_ENV: str(peachq_binary(paths.scripts_dir))}


def with_interpreter(paths: UqsPaths) -> dict[str, str]:
    """This process's environment with the runtime's interpreter applied."""
    return {**os.environ, **interpreter_env(paths)}


@functools.cache
def peachq_binary(scripts_dir: Path) -> Path:
    """The pinned PeachQ build, from scripts/peachq.py: UQF_PEACHQ if set,
    else the cached build, else a fresh one. That script runs under a bare
    python3 in CI, so it is loaded by path rather than imported."""
    spec = importlib.util.spec_from_file_location("uqf_peachq", scripts_dir / "peachq.py")
    if spec is None or spec.loader is None:  # pragma: no cover - a broken checkout
        raise UqsError(f"cannot load {scripts_dir / 'peachq.py'}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        return module.resolve()
    except module.PeachQError as exc:
        raise UqsError(f"the peachq runtime needs a PeachQ binary: {exc}") from None
