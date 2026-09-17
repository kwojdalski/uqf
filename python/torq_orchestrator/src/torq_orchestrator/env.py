"""The env bridge: the variables torq.sh and process.csv placeholders resolve
against.

Its own module because three layers need it - procs (to resolve a
`${VAR}`/`{VAR}+N` placeholder in a row), listing (to show the resolved
values) and runtime (to hand the environment to torq.sh) - while it depends
on nothing but the paths and the port block. Left in runtime it made
procs -> runtime -> procs a cycle.

Pure: no filesystem writes. bootstrap() calls it and then writes; everything
else calls it and only reads.
"""

from __future__ import annotations

from torq_orchestrator.logger import get_logger
from torq_orchestrator.paths import UqfStackPaths
from torq_orchestrator.pipelines import DEFAULT_BASE_PORT

log = get_logger(__name__)


def build_env(paths: UqfStackPaths, base_port: int = DEFAULT_BASE_PORT) -> dict[str, str]:
    """The env vars torq.sh (and process.csv's ${VAR}/{VAR}+N placeholders)
    resolve against - pure, no filesystem writes. bootstrap() calls this and
    also writes it out as setenv.sh; get_process_config() calls this to
    resolve a row's placeholders without needing to bootstrap first.
    """
    return {
        "TORQHOME": str(paths.torqhome),
        "TORQAPPHOME": str(paths.torqapphome),
        "TORQDATA": str(paths.torqdata),
        "UQFSCRIPTS": str(paths.scripts_dir),
        "UQFROOT": str(paths.repo_root),
        "KDBCONFIG": str(paths.torqhome / "config"),
        "KDBCODE": str(paths.torqhome / "code"),
        "KDBAPPCONFIG": str(paths.torqapphome / "appconfig"),
        "KDBAPPCODE": str(paths.torqapphome / "code"),
        "KDBLIB": str(paths.torqhome / "lib"),
        "KDBTESTS": str(paths.torqhome / "tests"),
        "KDBLOG": str(paths.torqdata / "logs"),
        "KDBHDB": str(paths.torqdata / "hdb"),
        "KDBWDB": str(paths.torqdata / "wdbhdb"),
        "KDBTPLOG": str(paths.torqdata / "tplogs"),
        "KDBDQCDB": str(paths.torqdata / "dqe" / "dqcdb" / "database"),
        "KDBDQEDB": str(paths.torqdata / "dqe" / "dqedb" / "database"),
        "KDBBASEPORT": str(base_port),
        "KDBSTACKID": f"-stackid {base_port}",
        "TORQPROCESSES": str(paths.generated_procs),
        "RLWRAP": "rlwrap",
        "QCON": "qcon",
        "QCMD": "q",
    }
