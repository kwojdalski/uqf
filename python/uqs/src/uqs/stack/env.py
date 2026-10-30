"""The env bridge: the variables torq.sh and process.csv placeholders resolve
against.

Its own module because three layers need it - procs (to resolve a
`${VAR}`/`{VAR}+N` placeholder in a row), listing (to show the resolved
values) and runtime (to hand the environment to torq.sh) - while it depends
on nothing but the paths and the port block. Left in runtime it made
procs -> runtime -> procs a cycle.

Pure: no filesystem writes. bootstrap() calls it and then writes; everything
else calls it and only reads. A runtime on PeachQ also needs its binary, which
comes from uqs.interpreter.peachq_binary - $QCMD when it is PeachQ, else
scripts/peachq.py's cache, or a build into that cache when cold - so that is
interpreter_env, which only what starts q calls:
bootstrap (for torq.sh and setenv.sh) and the HDB filler.
"""

from __future__ import annotations

import os

from uqs.interpreter import PEACHQ, Q_INTERPRETER_ENV, peachq_binary, q_command
from uqs.logger import get_logger
from uqs.paths import UqsError, UqsPaths
from uqs.runtimes import RUNTIME_ENV
from uqs.stack import qtree

log = get_logger()

#: The variable naming the deployment set's file to every TorQ process
#: (stack/deployment.py writes it, scripts/torqcode/handlers/deployment.q reads it).
DEPLOYMENT_ENV = "UQS_DEPLOYMENT_FILE"
#: The file q loads before anything else (q's own QINIT), and the one a PeachQ
#: runtime names in it: what TorQ's processes call and PeachQ lacks.
QINIT_ENV = "QINIT"
PEACHQ_INIT = "peachq_init.q"


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
        # What KDBSERVCODE's handlers/deployment.q lets TorQ discover (#1207).
        DEPLOYMENT_ENV: str(paths.generated_deployment),
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
    """QCMD for a runtime that declares PeachQ (#764), or {}.

    For that runtime's stack only: what torq.sh, the HDB filler and `uqs status`
    see for it, never the operator's shell, and nothing goes on PATH. A
    KDB-X runtime gets {}: the operator's own QCMD stands, as it always has.

    QINIT names scripts/peachq_init.q, so every q the runtime starts defines
    what TorQ needs and PeachQ does not have; it replaces an operator's QINIT
    for this runtime's processes only.
    """
    if paths.runtime_declaration.interpreter != PEACHQ:
        return {}
    try:
        binary = peachq_binary(scripts_dir=paths.scripts_dir)
    except UqsError as exc:
        raise UqsError(f"the {paths.runtime} runtime needs a PeachQ binary: {exc}") from None
    return {
        Q_INTERPRETER_ENV: str(binary),
        # What TorQ's processes call and PeachQ lacks (.Q.chk, which the wdb
        # calls as it starts), defined before torq.q loads (#1650).
        QINIT_ENV: str(paths.scripts_dir / PEACHQ_INIT),
    }


def with_interpreter(paths: UqsPaths) -> dict[str, str]:
    """This process's environment with the runtime's interpreter applied."""
    return {**os.environ, **interpreter_env(paths)}
